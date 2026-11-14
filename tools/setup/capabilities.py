"""Установочная проверка полного набора инструментов и штатных моделей."""
import json
import shutil
from pathlib import Path
from local_mode.capabilities import report
from local_mode.config import ROOT

if __name__ == "__main__":
    # Создаём исходные конфиги, не перезаписывая настройки владельца.
    for example in (ROOT / "config").glob("*.example.json"):
        target = example.with_name(example.name.replace(".example.json", ".json"))
        if not target.exists():
            shutil.copyfile(example, target)
    result = report()
    print(json.dumps({"ok": result["ok"], "tools_count": result["tools_count"],
                      "missing_handlers": [t for t in result["tools"] if t["error"]],
                      "vision_models": result["vision_models"]}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)
