"""Каталог проверенных Free Tier API; доступ проекта проверяется отдельно."""

from local_mode import gemini
from local_mode.api_usage import RequestUsage
from local_mode.models import MODEL_ORDER
from local_mode.gemini_voice import FLASH, LITE, PREVIEW

MODELS = (*MODEL_ORDER, FLASH, LITE, PREVIEW, "gemini-embedding-2")


def available_models() -> dict:
    with RequestUsage("models", "catalog"):
        with gemini.create_client(retry_attempts=0) as client:
            found = {}
            for item in client.models.list():
                name = (item.name or "").removeprefix("models/")
                if name in MODELS:
                    found[name] = {
                        "model": name,
                        "listed": True,
                        "input_token_limit": item.input_token_limit,
                        "output_token_limit": item.output_token_limit,
                        "actions": item.supported_actions or [],
                    }
    return {
        "models": [
            found.get(name, {"model": name, "listed": False}) for name in MODELS
        ],
        "billing_verified": False,
        "remaining_quota": None,
    }
