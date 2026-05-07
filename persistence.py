from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


class StateStore:
    def __init__(self, db_path: str = "state/bot_state.sqlite"):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS kv_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

    def save(self, key: str, value: Any):
        payload = json.dumps(value, default=str)
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_state(key, value, updated_at) VALUES (?, ?, ?)",
                (key, payload, datetime.now().isoformat(timespec="seconds")),
            )

    def load(self, key: str, default=None):
        with sqlite3.connect(self.path) as conn:
            row = conn.execute("SELECT value FROM kv_state WHERE key = ?", (key,)).fetchone()
        if not row:
            return default
        return json.loads(row[0])

    def delete(self, key: str):
        with sqlite3.connect(self.path) as conn:
            conn.execute("DELETE FROM kv_state WHERE key = ?", (key,))

    def save_open_position(self, position: dict):
        self.save("open_position", position)

    def load_open_position(self):
        return self.load("open_position", {})

    def save_daily_risk(self, state: dict):
        self.save("daily_risk", state)

    def load_daily_risk(self):
        return self.load("daily_risk", {})

    def save_runtime_stats(self, stats: dict):
        self.save("runtime_stats", stats)

    def save_oi_cache(self, cache: dict):
        self.save("oi_cache", cache)

    def save_session_state(self, state: dict):
        self.save("session_state", state)
