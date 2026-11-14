"""Offline weather coverage: no browser, cloud model or live weather requests."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from actions import weather_report as weather
from local_mode import owned_process


def fixture_responses():
    return [{"results": [{"name": "Vorzel", "latitude": 50.54, "longitude": 30.15,
                          "country": "Ukraine", "admin1": "Kyiv Oblast"}]},
            {"timezone": "Europe/Kyiv", "current": {"time": "2026-11-12T12:00", "temperature_2m": 4.5,
              "apparent_temperature": 2.2, "weather_code": 3, "wind_speed_10m": 8.1},
             "current_units": {"temperature_2m": "°C", "wind_speed_10m": "km/h"},
             "daily": {"time": ["2026-11-12"], "temperature_2m_max": [7], "temperature_2m_min": [1],
                       "weather_code": [3], "precipitation_probability_max": [20]},
             "daily_units": {"temperature_2m_max": "°C"}}]


def test_current_and_today_weather_preserve_provider_values_and_attribution(monkeypatch):
    request = Mock(side_effect=fixture_responses())
    monkeypatch.setattr(weather, "_json_request", request)
    result = weather._fetch_weather({"city": "Vorzel"})
    assert result["ok"] is True
    assert result["current"]["temperature_2m"] == 4.5
    assert result["daily"]["temperature_2m_min"] == [1]
    assert result["current_units"]["temperature_2m"] == "°C"
    assert result["location"]["country"] == "Ukraine"
    assert result["timezone"] == "Europe/Kyiv"
    assert result["source"]["forecast_url"].startswith("https://api.open-meteo.com/")
    assert "timezone=auto" in request.call_args.args[0]
    assert request.call_count == 2


@pytest.mark.parametrize("locations", [[], [{"name": "Springfield", "country": "US"}, {"name": "Springfield", "country": "CA"}]])
def test_missing_or_ambiguous_city_never_invents_weather(monkeypatch, locations):
    request = Mock(return_value={"results": locations})
    monkeypatch.setattr(weather, "_json_request", request)
    result = weather._fetch_weather({"city": "Springfield"})
    assert result["ok"] is False
    assert "current" not in result
    assert request.call_count == 1


def test_incomplete_data_is_rejected(monkeypatch):
    responses = fixture_responses()
    responses[1]["current"]["temperature_2m"] = None
    monkeypatch.setattr(weather, "_json_request", Mock(side_effect=responses))
    with pytest.raises(ValueError, match="incomplete"):
        weather._fetch_weather({"city": "Vorzel"})


@pytest.mark.parametrize("error,field", [(owned_process.ProcessTimedOut("fixture"), "error"),
                                         (owned_process.ProcessCancelled("fixture"), "cancelled")])
def test_owned_deadline_and_stop_return_truthful_failure(monkeypatch, error, field):
    worker = Mock(side_effect=error)
    monkeypatch.setattr(owned_process, "run_owned", worker)
    result = weather.weather_action({"city": "Vorzel"})
    assert result["ok"] is False
    assert result[field]
    assert worker.call_args.kwargs["timeout"] == 20
    assert "current" not in result


def test_worker_result_is_returned_without_browser_or_model(monkeypatch):
    response = {"ok": True, "current": {"temperature_2m": 4.5}}
    monkeypatch.setattr(owned_process, "run_owned", Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(response))))
    assert weather.weather_action({"city": "Vorzel"}) == response


def test_provider_request_limits_size_and_socket_wait(monkeypatch):
    response = Mock()
    response.read.return_value = b"{}"
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    opener = Mock(return_value=response)
    monkeypatch.setattr(weather, "urlopen", opener)
    assert weather._json_request("https://api.open-meteo.com/fixture") == {}
    assert opener.call_args.kwargs["timeout"] == 8
    response.read.assert_called_once_with(weather.MAX_BYTES + 1)


def test_unsupported_time_does_not_request_network(monkeypatch):
    request = Mock()
    monkeypatch.setattr(weather, "_json_request", request)
    assert weather._fetch_weather({"city": "Vorzel", "time": "tomorrow"})["ok"] is False
    request.assert_not_called()


def test_actual_owned_worker_starts_and_returns_without_network():
    result = weather.weather_action({"city": "Vorzel", "time": "unsupported"})
    assert result["ok"] is False
    assert "Only current weather" in result["error"]


@pytest.mark.parametrize("qualifier", ["Ukraine", "UA", "Kyiv"])
def test_comma_qualifier_filters_exact_city_before_forecast(monkeypatch, qualifier):
    responses = fixture_responses()
    responses[0]["results"][0]["country_code"] = "UA"
    responses[0]["results"].insert(0, {"name": "Vorzel", "country": "Another country", "latitude": 1, "longitude": 2})
    request = Mock(side_effect=responses)
    monkeypatch.setattr(weather, "_json_request", request)
    result = weather._fetch_weather({"city": f"Vorzel, {qualifier}"})
    assert result["ok"] is True
    assert result["location"]["country"] == "Ukraine"
    assert "name=Vorzel&" in request.call_args_list[0].args[0]
    assert "latitude=50.54" in request.call_args_list[1].args[0]


@pytest.mark.parametrize("city", ["Vorzel, Canada", "Vorz"])
def test_region_mismatch_and_prefix_match_never_select_another_city(monkeypatch, city):
    request = Mock(return_value=fixture_responses()[0])
    monkeypatch.setattr(weather, "_json_request", request)
    result = weather._fetch_weather({"city": city})
    assert result["ok"] is False
    assert "current" not in result
    assert request.call_count == 1


@pytest.mark.parametrize("spelling", ["Vorzel'", "Vorzel’", "Vorzelʼ", "Vorzelʹ"])
def test_geonames_apostrophe_transliteration_is_an_exact_identity(monkeypatch, spelling):
    responses = fixture_responses()
    responses[0]["results"][0]["name"] = spelling
    monkeypatch.setattr(weather, "_json_request", Mock(side_effect=responses))
    result = weather._fetch_weather({"city": "Vorzel, Ukraine"})
    assert result["ok"] is True
    assert result["location"]["name"] == spelling


@pytest.mark.parametrize("qualifier", ["Kyiv region", "Kyiv Oblast", "Kyiv", "  KYIV   REGION "])
def test_natural_region_matches_provider_administrative_class(monkeypatch, qualifier):
    responses = fixture_responses()
    responses[0]["results"][0]["name"] = "Vorzel’"
    request = Mock(side_effect=responses)
    monkeypatch.setattr(weather, "_json_request", request)
    result = weather._fetch_weather({"city": f"Vorzel, {qualifier}"})
    assert result["ok"] is True
    assert result["location"]["admin1"] == "Kyiv Oblast"
    assert request.call_count == 2


@pytest.mark.parametrize("city,localized,language", [
    ("Ворзель, Kyiv region", {"name": "Ворзель", "admin1": "Киевская область"}, "ru"),
    ("Vorzel, Київська область", {"name": "Ворзель", "admin1": "Київська область"}, "uk"),
    ("Ворзель, Киевская обл.", {"name": "Ворзель", "admin1": "Киевская область"}, "ru"),
])
def test_provider_localized_identity_joins_only_stable_ids(monkeypatch, city, localized, language):
    geocode, forecast = fixture_responses()
    geocode["results"][0].update(id=688956, name="Vorzel'")
    request = Mock(side_effect=[geocode, {"results": [{"id": 688956, **localized}]}, forecast])
    monkeypatch.setattr(weather, "_json_request", request)
    assert weather._fetch_weather({"city": city})["ok"] is True
    assert f"language={language}" in request.call_args_list[1].args[0]
    assert request.call_count == 3


def test_localized_name_cannot_attach_to_different_provider_id(monkeypatch):
    geocode, _ = fixture_responses()
    geocode["results"][0]["id"] = 1
    request = Mock(side_effect=[geocode, {"results": [{"id": 2, "name": "Ворзель"}]}])
    monkeypatch.setattr(weather, "_json_request", request)
    result = weather._fetch_weather({"city": "Ворзель, Kyiv region"})
    assert result["ok"] is False
    assert result["retryable"] is False
    assert result["clarification_required"] is True
    assert request.call_count == 2


@pytest.mark.parametrize("city", ["Vorzel, Kyiv region", "Regionville, Kyiv region", "Vorzel, Kyi region", "Vorzel, region"])
def test_region_normalization_never_guesses_city_or_ambiguous_region(monkeypatch, city):
    geocode, _ = fixture_responses()
    geocode["results"].append({**geocode["results"][0], "latitude": 51, "country": "Another country"})
    request = Mock(return_value=geocode)
    monkeypatch.setattr(weather, "_json_request", request)
    result = weather._fetch_weather({"city": city})
    assert result["ok"] is False
    assert "current" not in result
    assert request.call_count == 1
