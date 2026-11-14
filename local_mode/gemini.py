"""Gemini только для текстового планирования; локальные действия вне retry."""
from __future__ import annotations
import os
import math
from typing import Callable
from threading import Event
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
import httpx
from local_mode.network.network_preferences import read_network_preferences, network_options
from local_mode.network.network_transport import RoutingTransport, NetworkRouteError, is_regional_error
from local_mode.credentials import KEY_FILE, BOOTSTRAP_FILE, load_key
from local_mode.api_usage import track_call
from local_mode.messages import OwnedError, OwnedText
from google import genai
from google.genai import types
from google.genai.errors import APIError
from tenacity import RetryCallState, retry, stop_after_attempt, wait_exponential


def has_key() -> bool:
    from api_keys import get_all_keys
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or os.environ.get("JARVIS_GEMINI_API_KEY") or KEY_FILE.is_file() or BOOTSTRAP_FILE.is_file() or get_all_keys())


def create_client(*, network: dict[str, str] | None = None,
                  retry_attempts: int | None = None, stop: Event | None = None) -> genai.Client:
    if stop is not None and stop.is_set():
        raise RuntimeError("Model request cancelled")
    if os.environ.get("JARVIS_DESKTOP_TEST") == "1":
        if os.environ.get("JARVIS_QA_ALLOW_CLOUD") != "1":
            raise RuntimeError("Cloud API is disabled in QA. Use a synthetic local provider.")
        retry_attempts = 0
    if not has_key():
        raise ValueError("Для свободных запросов задайте GEMINI_API_KEY. Две основные команды работают локально без ключа.")
    # Совместимость с именем ключа исходного Jarvis, без записи секрета в файл.
    if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
        from api_keys import next_key
        os.environ["GEMINI_API_KEY"] = next_key()
    if any(os.environ.get(name, "").lower() in {"true", "1"} for name in
           ("GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_ENTERPRISE")):
        raise ValueError("Нужен Developer API Google AI Studio; отключите Vertex/Enterprise.")
    network = read_network_preferences() if network is None else network_options(network["mode"], network["proxy"])
    options = types.HttpOptions()
    if retry_attempts is not None:
        options.retry_options = types.HttpRetryOptions(attempts=retry_attempts)
    if network["mode"] == "system":
        return genai.Client(http_options=options) if retry_attempts is not None else genai.Client()
    http_client = httpx.Client(transport=RoutingTransport(network["mode"], network["proxy"], stop=stop), trust_env=False)
    try:
        options.httpx_client = http_client
        return genai.Client(http_options=options)
    except Exception:
        http_client.close()
        raise


def is_transient(error: BaseException) -> bool:
    """Ошибки авторизации и неверные запросы не исправляются повтором."""
    if isinstance(error, APIError):
        if (error.code == 429 and (is_daily_quota(error) or zero_quota(error))) or server_retry_delay(error) > 60:
            return False
    return (isinstance(error, APIError) and error.code in {429, 500, 502, 503, 504}) or isinstance(
        error, httpx.TransportError
    )


def api_error_details(error: APIError) -> list[dict]:
    """Извлекаем служебные сведения Google без вывода исходного ответа."""
    payload = error.details if isinstance(error.details, dict) else {}
    payload = payload.get("error", payload)
    if not isinstance(payload, dict) or not isinstance(payload.get("details", []), list):
        return []
    return [item for item in payload.get("details", []) if isinstance(item, dict)]


def can_fallback(error: BaseException) -> bool:
    """Неверный ключ, запрос или общий сетевой сбой сменой модели не исправляются."""
    return isinstance(error, APIError) and error.code in {404, 429, 500, 502, 503, 504}


def should_retry(state: RetryCallState) -> bool:
    """Даём текущей модели три попытки; сетевой таймаут не меняет модель."""
    if state.kwargs.get("single_attempt", False):
        return False
    error = state.outcome.exception() if state.outcome else None
    if not is_transient(error):
        return False
    return not (state.kwargs.get("fallback_available", False) and can_fallback(error)
                and state.attempt_number >= 3)


def is_daily_quota(error: APIError) -> bool:
    """Дневной лимит не восстановится за несколько секунд повторных попыток."""
    for item in api_error_details(error):
        violations = item.get("violations", [])
        if isinstance(violations, list) and any(
            isinstance(value, dict) and "perday" in str(value.get("quotaId", "")).lower()
            for value in violations
        ):
            return True
    return False


def zero_quota(error: APIError) -> bool:
    """Нулевой лимит не восстановится при повторе того же запроса."""
    for item in api_error_details(error):
        violations = item.get("violations", [])
        if isinstance(violations, list) and any(isinstance(v, dict) and str(v.get("quotaValue")) == "0" for v in violations):
            return True
    return False


def server_retry_delay(error: APIError) -> float:
    """Читаем длительность паузы из RetryInfo и числового Retry-After."""
    values = [item.get("retryDelay", "0s") for item in api_error_details(error)]
    if error.response is not None:
        values.append(error.response.headers.get("retry-after", "0"))
    delays = []
    for value in values:
        try:
            seconds = float(str(value).removesuffix("s"))
        except (ValueError, TypeError):
            try:
                seconds = (parsedate_to_datetime(str(value)) - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                continue
        if math.isfinite(seconds) and seconds >= 0:
            delays.append(seconds)
    return max(delays, default=0)


def wait_for_retry(state: RetryCallState) -> float:
    """Учитываем паузу сервера, ограничивая ожидание одной минуты на попытку."""
    exponential = wait_exponential(multiplier=1, min=2, max=10)(state)
    error = state.outcome.exception() if state.outcome else None
    server_delay = server_retry_delay(error) if isinstance(error, APIError) else 0
    return min(60, max(exponential, server_delay))


@retry(stop=stop_after_attempt(5), wait=wait_for_retry,
       retry=should_retry, reraise=True)
@track_call("generate")
def generate(client: genai.Client, model: str, contents: types.ContentListUnion,
                     config: types.GenerateContentConfig | None = None, *,
                     fallback_available: bool = False, single_attempt: bool = False) -> types.GenerateContentResponse:
    """Повторяем только HTTP-запрос, никогда не повторяем локальные действия.

    Встроенные повторы SDK отключены для этого запроса, чтобы они не умножали
    пять попыток Tenacity. Конфигурация вызывающего кода не изменяется.
    """
    request_config = config.model_copy(deep=True) if config else types.GenerateContentConfig()
    http_options = request_config.http_options or types.HttpOptions(timeout=30000)
    request_config.http_options = http_options.model_copy(update={
        "retry_options": types.HttpRetryOptions(attempts=1)
    })
    return client.models.generate_content(model=model, contents=contents, config=request_config)


@retry(stop=stop_after_attempt(5), wait=wait_for_retry,
       retry=should_retry, reraise=True)
@track_call("stream")
def generate_stream(client: genai.Client, model: str, contents: types.ContentListUnion,
                    config: types.GenerateContentConfig | None = None, *,
                    fallback_available: bool = False, on_text: Callable[[str], None] | None = None,
                    stop: Event | None = None, single_attempt: bool = False
                    ) -> types.GenerateContentResponse:
    """Показываем накопленный текст; инструменты возвращаем только после конца потока.

    Новый запрос очищает черновик, поэтому повтор/резерв не склеивает ответы.
    Подписи и служебные части сохраняются для следующего хода модели.
    """
    request_config = config.model_copy(deep=True) if config else types.GenerateContentConfig()
    options = request_config.http_options or types.HttpOptions(timeout=30000)
    request_config.http_options = options.model_copy(update={
        "retry_options": types.HttpRetryOptions(attempts=1)
    })
    if on_text:
        on_text("")
    parts: list[types.Part] = []
    text = ""
    final = None
    final_candidate = None
    stream = client.models.generate_content_stream(model=model, contents=contents, config=request_config)
    try:
        for chunk in stream:
            if stop is not None and stop.is_set():
                raise RuntimeError("Действие отменено пользователем.")
            final = chunk
            candidate = next((c for c in chunk.candidates or [] if c.index in (None, 0)), None)
            if candidate is None:
                continue
            final_candidate = candidate
            if candidate.content is None:
                continue
            for part in candidate.content.parts or []:
                parts.append(part.model_copy(deep=True))
                if part.text and not part.thought:
                    text += part.text
                    if on_text:
                        on_text(text)
    finally:
        stream.close()
    if final is None or not parts:
        raise RuntimeError("Gemini вернул пустой или заблокированный ответ.")
    candidate = final_candidate.model_copy(update={
        "content": types.Content(role="model", parts=parts)
    })
    return final.model_copy(update={"candidates": [candidate]})


def safe_error(error: Exception | OwnedText) -> str:
    if isinstance(error, (OwnedError, OwnedText)):
        values = tuple(safe_error(value) if isinstance(value, OwnedText) else safe_error(Exception(value)) if isinstance(value, str) else value for value in error.values)
        return OwnedText(error.template, *values)
    if isinstance(error, NetworkRouteError):
        return str(error)
    if isinstance(error, httpx.HTTPError):
        return OwnedText("Gemini connection failed. Check Network & Proxy settings.")
    if isinstance(error, APIError):
        if is_regional_error(error.code, str(error.message)):
            return OwnedText("Gemini is unavailable in the current region. Configure Network & Proxy routing.")
        if error.code == 429 and is_daily_quota(error):
            return OwnedText("This model's daily quota is exhausted (429). Wait for the AI Studio reset or select another model.")
        if error.code == 429 and server_retry_delay(error) > 60:
            return OwnedText("Gemini requires a pause longer than one minute (429). Retry later or select another model.")
        templates = {
            429: "Gemini request/token limit reached (429). Check AI Studio limits.",
            403: "Gemini rejected the API key or project access.",
            404: "The Gemini model is unavailable to your project.",
        }
        return OwnedText(templates[error.code]) if error.code in templates else OwnedText("Gemini HTTP error {0}.", error.code)
    message = str(error)
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "JARVIS_GEMINI_API_KEY"):
        if secret := os.environ.get(name):
            message = message.replace(secret, "[redacted]")
    return message[:1500]


def local_fallback_allowed(error: Exception) -> bool:
    """Локальный резерв разрешён при квоте/сети/отсутствии доступа, но не неверном входе."""
    return (isinstance(error, APIError) and error.code in {403,404,429,500,502,503,504}) or isinstance(error, (httpx.TransportError, NetworkRouteError)) or (isinstance(error, ValueError) and not has_key())
