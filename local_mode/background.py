"""Local scheduling and watchdog services without a cloud Live session."""
from __future__ import annotations
import json
from pathlib import Path
import threading
import time
from datetime import datetime
from typing import Any, Callable
from local_mode.config import ROOT
from local_mode.gemini import safe_error
from local_mode.messages import OwnedText, OwnedValueError


class BackgroundServices:
    def __init__(self, stop: threading.Event, report: Callable[[str], None], path: Path | None = None) -> None:
        self.stop = stop
        self.report = report
        self.path = path or ROOT / "data" / "schedules.json"
        self.lock = threading.Lock()
        self.items: list[dict[str, Any]] = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []
        self.thread: threading.Thread | None = None

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.items, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.path)

    def schedule(self, args: dict[str, Any]) -> dict[str, Any]:
        action = args.get("action", "list")
        with self.lock:
            if action in {"add", "create", "set"}:
                if args.get("delay_minutes") is not None:
                    at = time.time() + float(args["delay_minutes"]) * 60
                elif args.get("time"):
                    value = str(args["time"])
                    dt = datetime.fromisoformat(value) if "-" in value else datetime.combine(datetime.now().date(), datetime.strptime(value, "%H:%M").time())
                    at = dt.timestamp()
                else:
                    at = time.time() + float(args.get("seconds", args.get("delay_seconds", 60)))
                if at <= time.time():
                    raise OwnedValueError("Select a time in the future.")
                message = str(args.get("message", args.get("text", "Reminder")))
                default_message = "message" not in args and "text" not in args
                item = {"id": max([int(i["id"]) for i in self.items] + [0]) + 1, "fire_at": at, "message": message, "default_message": default_message, "fired": False}
                self.items.append(item)
                self._save()
                return {"ok": True, "schedule": item}
            if action in {"cancel", "delete", "remove"}:
                before = len(self.items)
                self.items = [i for i in self.items if i["id"] != int(args["id"])]
                self._save()
                return {"ok": len(self.items) < before}
            return {"ok": True, "schedules": [i for i in self.items if not i.get("fired")]}

    def tick(self, now: float | None = None) -> None:
        due = []
        with self.lock:
            for item in self.items:
                if not item.get("fired") and item["fire_at"] <= (now if now is not None else time.time()):
                    item["fired"] = True
                    if item.get("default_message") is True:
                        due.append(OwnedText("Reminder."))
                    else:
                        due.append(OwnedText("Reminder: {0}", item["message"]))
            if due:
                self._save()
        for message in due:
            self.report(message)

    def start(self) -> None:
        if self.thread:
            return
        self.thread = threading.Thread(target=self._loop, name="Jarvis-watchdog", daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        last = 0.0
        while not self.stop.wait(1):
            try:
                self.tick()
                if time.monotonic() - last >= 30:
                    last = time.monotonic()
                    from actions.monitor import check_rules, check_email_watch
                    from actions.guardian import check_guardian
                    for check in (check_rules, check_guardian, check_email_watch):
                        for message in check():
                            self.report(message)
            except Exception as error:
                self.report(OwnedText("Monitoring: {0}", safe_error(error)))
