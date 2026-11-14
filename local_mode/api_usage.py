"""Локальная статистика SDK-запросов: без промптов, ключей и ложного остатка квоты."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timezone
from functools import wraps
import json
import os
from pathlib import Path
import re
import threading
from typing import Any

from local_mode.config import ROOT
from local_mode.storage import write_json

FIELDS = {
    "input_tokens": ("prompt_token_count", "total_input_tokens"),
    "output_tokens": ("candidates_token_count", "total_output_tokens"),
    "cached_tokens": ("cached_content_token_count", "total_cached_tokens"),
    "thought_tokens": ("thoughts_token_count", "total_thought_tokens"),
    "total_tokens": ("total_token_count", "total_tokens"),
}


def token_usage(response: Any) -> dict[str, int]:
    result = {}
    for source in (
        getattr(response, "usage_metadata", None),
        getattr(response, "usage", None),
    ):
        if source is None:
            continue
        for name, options in FIELDS.items():
            for key in options:
                value = (
                    source.get(key)
                    if isinstance(source, dict)
                    else getattr(source, key, None)
                )
                if type(value) is int and 0 <= value <= 10**12:
                    result[name] = value
                    break
    return result


class UsageLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._days: dict = {}
        self.persistence_error = False
        try:
            if path.exists() and path.stat().st_size > 1024 * 1024:
                raise ValueError("Слишком большой файл статистики.")
            value = (
                json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            )
            if not isinstance(value, dict):
                raise ValueError("Некорректная структура статистики.")
            if value.get("version", 1) == 1 and isinstance(value.get("days", {}), dict):
                for day, rows in sorted(value.get("days", {}).items())[-7:]:
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) and isinstance(
                        rows, dict
                    ):
                        valid = {}
                        for key, row in list(rows.items())[:64]:
                            if not isinstance(row, dict) or not re.fullmatch(
                                r"[a-z0-9._-]{1,100}:[a-z]{1,16}", key
                            ):
                                continue
                            valid[key] = {
                                k: v
                                for k, v in row.items()
                                if k
                                in {
                                    "attempts",
                                    "successes",
                                    "failures",
                                    "rate_limited",
                                    "reported_usage",
                                    *FIELDS,
                                }
                                and type(v) is int
                                and 0 <= v <= 10**12
                            }
                        self._days[day] = valid
        except (OSError, ValueError, TypeError):
            self.persistence_error = True

    def add(
        self,
        model: str,
        operation: str,
        response=None,
        error: BaseException | None = None,
    ) -> None:
        model = model.removeprefix("models/")
        if not re.fullmatch(r"[a-z0-9._-]{1,100}", model) or operation not in {
            "generate",
            "stream",
            "tts",
            "embed",
            "catalog",
            "count",
        }:
            return
        with self._lock:
            day = datetime.now(timezone.utc).date().isoformat()
            self._days = (
                dict(sorted(self._days.items())[-6:])
                if day not in self._days
                else self._days
            )
            rows = self._days.setdefault(day, {})
            key = model + ":" + operation
            if key not in rows and len(rows) >= 64:
                return
            row = rows.setdefault(key, {})

            def increment(key: str, value: int = 1):
                row[key] = row.get(key, 0) + value

            increment("attempts")
            increment("failures" if error else "successes")
            if getattr(error, "code", getattr(error, "status_code", None)) == 429:
                increment("rate_limited")
            usage = token_usage(response)
            if usage:
                increment("reported_usage")
                for name, value in usage.items():
                    increment(name, value)
            try:
                write_json(self.path, {"version": 1, "days": self._days})
                self.persistence_error = False
            except (OSError, ValueError):
                self.persistence_error = True

    def snapshot(self) -> dict:
        with self._lock:
            today = datetime.now(timezone.utc).date().isoformat()
            rows = self._days.get(today, {})
            return {
                "day_utc": today,
                "scope": "local_client_attempts",
                "remaining_quota": None,
                "persistence_error": self.persistence_error,
                "rows": [
                    {"model": key.split(":")[0], "operation": key.split(":")[1], **row}
                    for key, row in sorted(rows.items())
                ],
            }


_default = UsageLedger(
    ROOT
    / (
        ".cache/desktop-test/api-usage.json"
        if os.environ.get("JARVIS_DESKTOP_TEST") == "1"
        else "data/api-usage.json"
    )
)


_qa_cloud_lock = threading.Lock()
_qa_cloud_calls = 0

class RequestUsage(AbstractContextManager):
    def __init__(self, model: str, operation: str) -> None:
        self.model = model
        self.operation = operation
        self.response = None

    def __enter__(self):
        global _qa_cloud_calls
        if os.environ.get("JARVIS_DESKTOP_TEST") == "1" and os.environ.get("JARVIS_QA_ALLOW_CLOUD") == "1":
            # Default stays deliberately small; multi-step QA must explicitly
            # align this SDK-attempt cap with its native transport request budget.
            budget_text = os.environ.get("JARVIS_QA_MAX_CLOUD_CALLS", "1")
            if not re.fullmatch(r"[1-8]", budget_text):
                raise ValueError("JARVIS_QA_MAX_CLOUD_CALLS must be an integer from 1 to 8.")
            budget = int(budget_text)
            with _qa_cloud_lock:
                if _qa_cloud_calls >= budget:
                    raise RuntimeError(f"QA cloud budget exhausted: {budget} SDK attempt(s) per process, including retries and fallback.")
                _qa_cloud_calls += 1
        return self

    def __exit__(self, exc_type, error, traceback):
        if (
            os.environ.get("PYTEST_CURRENT_TEST")
            and _default.path == ROOT / "data/api-usage.json"
        ):
            return False  # Регрессии с mock не загрязняют статистику владельца.
        # Счётчики никогда не подменяют результат действия/ошибку сети.
        try:
            _default.add(self.model, self.operation, self.response, error)
        except Exception:
            _default.persistence_error = True
        return False


def track_call(operation: str):
    def decorate(function):
        @wraps(function)
        def tracked(*args, **kwargs):
            model = kwargs.get("model", args[1] if len(args) > 1 else "unknown")
            with RequestUsage(model, operation) as usage:
                usage.response = function(*args, **kwargs)
                return usage.response

        return tracked

    return decorate
