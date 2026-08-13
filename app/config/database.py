"""SQLite persistence for credentials, generated files, and audit events."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    """Return an ISO timestamp that sorts correctly in SQLite."""
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Thread-safe SQLite wrapper with additive migrations for upgrades."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path,
            check_same_thread=False,
            isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self.init_schema()

    def init_schema(self) -> None:
        """Create tables and migrate older installations without data loss."""
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS api_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    key_ciphertext TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'groq',
                    endpoint TEXT,
                    models_json TEXT NOT NULL DEFAULT '[]',
                    priority INTEGER NOT NULL DEFAULT 100,
                    status TEXT NOT NULL DEFAULT 'active',
                    paused_until TEXT,
                    limit_requests INTEGER,
                    remaining_requests INTEGER,
                    limit_tokens INTEGER,
                    remaining_tokens INTEGER,
                    reset_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS generated_files (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    prompt TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            existing = {
                row["name"]
                for row in self._connection.execute("PRAGMA table_info(api_keys)").fetchall()
            }
            migrations = {
                "provider": "ALTER TABLE api_keys ADD COLUMN provider TEXT NOT NULL DEFAULT 'groq'",
                "endpoint": "ALTER TABLE api_keys ADD COLUMN endpoint TEXT",
                "models_json": "ALTER TABLE api_keys ADD COLUMN models_json TEXT NOT NULL DEFAULT '[]'",
                "priority": "ALTER TABLE api_keys ADD COLUMN priority INTEGER NOT NULL DEFAULT 100",
            }
            for name, statement in migrations.items():
                if name not in existing:
                    self._connection.execute(statement)

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        """Execute a statement under the shared lock."""
        with self._lock:
            return self._connection.execute(query, params)

    def fetchone(self, query: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._connection.execute(query, params).fetchone()

    def fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(query, params).fetchall()

    def add_audit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Keep an operational trail without storing secrets."""
        self.execute(
            "INSERT INTO audit_events (event_type, payload, created_at) VALUES (?, ?, ?)",
            (event_type, json.dumps(payload, ensure_ascii=False), utc_now()),
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()