"""Диагностика полного каталога без выполнения действий и сетевых запросов."""
from __future__ import annotations
import importlib
from typing import Any
from local_mode.tools import DECLARATIONS, HANDLERS
from local_mode.config import ROOT
from local_mode.gemini import safe_error

SPECIAL = {"save_memory", "recall_memory", "forget_memory", "clear_memory", "set_mode", "set_mood", "set_voice", "wake_word", "create_presentation", "create_spreadsheet", "smart_home_control", "api_add_key", "google_auth", "google_auth_status", "schedule", "agent_task"}


def report() -> dict[str, Any]:
    tools = []
    for declaration in DECLARATIONS:
        name = declaration["name"]
        error = ""
        try:
            if name in HANDLERS:
                module, attribute = HANDLERS[name]
                if not callable(getattr(importlib.import_module(module), attribute)):
                    raise ValueError("Нет обработчика.")
            elif name not in SPECIAL:
                raise ValueError("Инструмент не подключён.")
        except Exception as exc:
            error = safe_error(exc)
        tools.append({"name": name, "handler_available": not error, "error": error})
    required = ["holistic_landmarker.task", "face_detector.tflite", "face_detection_yunet_2023mar.onnx", "face_recognition_sface_2021dec.onnx", "yolo11n.pt"]
    models = {name: (ROOT / "models" / name).is_file() for name in required}
    return {"ok": all(t["handler_available"] for t in tools) and all(models.values()),
            "tools_count": len(tools), "tools": tools, "vision_models": models,
            "execution_tested": False}
