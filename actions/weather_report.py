"""Read-only Open-Meteo weather, with an owned deadline and no browser/model calls."""
import json
import math
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

SOURCE = {"name": "Open-Meteo", "url": "https://open-meteo.com/",
          "documentation": "https://open-meteo.com/en/docs"}
TIMEOUT = 20
MAX_BYTES = 64000


def _json_request(url):
    with urlopen(url, timeout=8) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Weather response exceeded the size limit")
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("error"):
        raise ValueError("Weather provider returned an error")
    return data


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _location_key(value):
    # GeoNames transliterations may append an apostrophe (Vorzel'); this is
    # an identity normalization, never a prefix or substring match.
    value = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    return " ".join(value.translate(str.maketrans("", "", "'’ʼʹ")).split())


def _region_key(value):
    # Administrative class words may differ between natural language and
    # GeoNames. Never remove substrings or apply this normalization to cities.
    words = _location_key(value).split()
    classes = {"region", "oblast", "область", "обл.", "обл"}
    return " ".join(word for word in words if word not in classes)


def _location_failure(city, error, **details):
    return {"ok": False, "city": city,
            "error": error + ". Ask for clarification rather than repeat this request",
            "retryable": False,
            "clarification_required": True,
            "next_step": "Ask for a country or a more specific region; do not repeat the same location request",
            "source": SOURCE, **details}


def _fetch_weather(parameters):
    city = parameters.get("city")
    period = parameters.get("time", "today")
    if not isinstance(city, str) or not city.strip() or len(city) > 200:
        return {"ok": False, "error": "A city name is required", "source": SOURCE}
    city = city.strip()
    if period not in {"today", "current"}:
        return {"ok": False, "city": city, "error": "Only current weather and today's forecast are supported", "source": SOURCE}
    city_name, *qualifiers = [part.strip() for part in city.split(",")]
    if not city_name or any(not part for part in qualifiers):
        return {"ok": False, "city": city, "error": "Specify a city and nonempty country or region", "source": SOURCE}
    geocode_url = "https://geocoding-api.open-meteo.com/v1/search?" + urlencode({"name": city_name, "count": 20, "language": "en", "format": "json"})
    geocoding_urls = [geocode_url]
    locations = _json_request(geocode_url).get("results", [])
    if not isinstance(locations, list) or not locations:
        return _location_failure(city, "No matching location found; include a country or region")
    locations = [item for item in locations if isinstance(item, dict)]
    aliases = {}
    # Provider-owned translations, joined only by stable GeoNames IDs, let a
    # Cyrillic city and English region (or vice versa) identify the same place.
    # One optional lookup; no fuzzy spelling retries or invented transliterations.
    if re.search("[А-Яа-яІіЇїЄєҐґЁё]", city):
        language = "uk" if re.search("[ІіЇїЄєҐґ]", city) else "ru"
        localized_url = "https://geocoding-api.open-meteo.com/v1/search?" + urlencode({
            "name": city_name, "count": 20, "language": language, "format": "json"})
        geocoding_urls.append(localized_url)
        localized = _json_request(localized_url).get("results", [])
        if isinstance(localized, list):
            aliases = {item["id"]: item for item in localized
                       if isinstance(item, dict) and type(item.get("id")) is int}
    matches = []
    for item in locations:
        names = [item]
        if type(item.get("id")) is int and item["id"] in aliases:
            names.append(aliases[item["id"]])
        if (_location_key(city_name) in {_location_key(name.get("name", "")) for name in names}
                and all(_region_key(qualifier) and _region_key(qualifier) in {
                    _region_key(name.get(key, "")) for name in names
                    for key in ("country", "country_code", "admin1", "admin2", "admin3", "admin4")}
                    for qualifier in qualifiers)):
            matches.append(item)
    if not matches:
        return _location_failure(city, "No exact matching city and region found; specify the provider's city name, country or region")
    if len(matches) > 1:
        return _location_failure(city, "Location is ambiguous; specify a country or region",
                locations=[{key: item.get(key) for key in ("name", "admin1", "country")} for item in matches])
    place = matches[0]
    if not isinstance(place, dict) or not _number(place.get("latitude")) or not _number(place.get("longitude")):
        raise ValueError("Weather provider returned invalid coordinates")
    forecast_url = "https://api.open-meteo.com/v1/forecast?" + urlencode({
        "latitude": place["latitude"], "longitude": place["longitude"], "timezone": "auto", "forecast_days": 1,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"})
    data = _json_request(forecast_url)
    current, daily = data.get("current"), data.get("daily")
    if (not isinstance(current, dict) or not isinstance(current.get("time"), str)
            or not all(_number(current.get(key)) for key in ("temperature_2m", "apparent_temperature", "weather_code", "wind_speed_10m"))
            or not isinstance(daily, dict)
            or not all(isinstance(daily.get(key), list) and len(daily[key]) == 1 for key in ("time", "temperature_2m_max", "temperature_2m_min", "precipitation_probability_max", "weather_code"))
            or not all(_number(daily[key][0]) for key in ("temperature_2m_max", "temperature_2m_min", "precipitation_probability_max", "weather_code"))):
        raise ValueError("Weather provider returned incomplete weather data")
    return {"ok": True, "city": city, "requested_time": period,
            "location": {key: place.get(key) for key in ("id", "name", "admin1", "country", "latitude", "longitude")},
            "timezone": data.get("timezone"), "current": current, "current_units": data.get("current_units", {}),
            "daily": daily, "daily_units": data.get("daily_units", {}),
            "source": {**SOURCE, "geocoding_url": geocode_url, "geocoding_urls": geocoding_urls, "forecast_url": forecast_url},
            "data_kind": "Weather model current conditions and today's forecast; not a station observation"}


def weather_action(parameters, player=None, session_memory=None):
    """Fetch actual weather in a cancellable process; never claim data on failure."""
    from local_mode.owned_process import run_owned, ProcessCancelled, ProcessTimedOut
    try:
        result = run_owned([sys.executable, "-X", "utf8", "-m", "actions.weather_report"],
                           payload=json.dumps(parameters).encode("utf-8"), timeout=TIMEOUT, max_output=MAX_BYTES,
                           cwd=Path(__file__).resolve().parent.parent)
        if result.returncode:
            raise ValueError("Weather worker failed")
        return json.loads(result.stdout)
    except ProcessCancelled:
        return {"ok": False, "cancelled": True, "error": "Weather request cancelled", "source": SOURCE}
    except ProcessTimedOut:
        return {"ok": False, "error": "Weather request timed out; no weather data retrieved", "source": SOURCE}
    except Exception:
        return {"ok": False, "error": "Weather data is unavailable; no weather data retrieved", "source": SOURCE}


if __name__ == "__main__":
    try:
        request = sys.stdin.buffer.readline(MAX_BYTES + 1)
        if len(request) > MAX_BYTES:
            raise ValueError("Request too large")
        result = _fetch_weather(json.loads(request))
    except Exception:
        result = {"ok": False, "error": "Weather data is unavailable; no weather data retrieved", "source": SOURCE}
    print(json.dumps(result, ensure_ascii=False))
