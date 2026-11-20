"""Small SQLite store for searchable vision event history."""

from __future__ import annotations

from contextlib import closing
import json
import sqlite3
from pathlib import Path
from typing import Iterable

from .vision_events import VisionEvent


class VisionEventStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS vision_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                source TEXT NOT NULL,
                timestamp REAL NOT NULL,
                label TEXT,
                confidence REAL,
                payload TEXT NOT NULL
            )""")

    def _connect(self):
        return sqlite3.connect(self.path)

    def add(self, event: VisionEvent) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO vision_events(type,source,timestamp,label,confidence,payload) VALUES(?,?,?,?,?,?)",
                (event.type, event.source, event.timestamp, event.label, event.confidence,
                 json.dumps(event.to_dict(), default=str)),
            )

    def add_many(self, events: Iterable[VisionEvent]) -> None:
        rows = [
            (event.type, event.source, event.timestamp, event.label, event.confidence,
             json.dumps(event.to_dict(), default=str))
            for event in events
        ]
        if not rows:
            return
        with closing(self._connect()) as db, db:
            db.executemany(
                "INSERT INTO vision_events(type,source,timestamp,label,confidence,payload) VALUES(?,?,?,?,?,?)",
                rows,
            )

    def recent(self, limit: int = 50) -> list[dict]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT type,source,timestamp,label,confidence,payload FROM vision_events "
                "ORDER BY id DESC LIMIT ?", (max(1, int(limit)),)
            ).fetchall()
        return [
            {"type": row[0], "source": row[1], "timestamp": row[2],
             "label": row[3], "confidence": row[4], "payload": json.loads(row[5])}
            for row in rows
        ]
