"""Encrypted multi-provider API-key pool with model-aware failover."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config.database import Database, utc_now
from app.core.crypto import SecretBox
from app.core.limit_tracker import key_health, limits_from_headers
from app.core.provider_factory import DEFAULT_MODELS, detect_provider, normalize_provider


PAUSE_HOURS = 5


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class KeyPool:
    """Select the healthiest active key that supports a requested model."""

    def __init__(self, database: Database, secret_box: SecretBox) -> None:
        self.db = database
        self.secret_box = secret_box

    def add(
        self,
        name: str,
        raw_key: str,
        provider: str | None = None,
        endpoint: str | None = None,
        priority: int = 100,
        models: list[str] | None = None,
    ) -> dict[str, Any]:
        name, raw_key = name.strip(), raw_key.strip()
        if not name or not raw_key:
            raise ValueError("Name and API key are required")
        provider_name = normalize_provider(provider, raw_key, endpoint)
        now = utc_now()
        cursor = self.db.execute(
            """
            INSERT INTO api_keys
                (name, key_ciphertext, provider, endpoint, models_json, priority, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                self.secret_box.encrypt(raw_key),
                provider_name,
                endpoint.strip().rstrip("/") if endpoint else None,
                json.dumps(models or DEFAULT_MODELS.get(provider_name, [])),
                priority,
                now,
                now,
            ),
        )
        self.db.add_audit_event(
            "key_added",
            {"key_id": cursor.lastrowid, "name": name, "provider": provider_name},
        )
        row = self.db.fetchone("SELECT * FROM api_keys WHERE id = ?", (cursor.lastrowid,))
        if row is None:
            raise RuntimeError("Key was inserted but could not be read")
        return self.public_row(dict(row))

    def get_credentials(self, key_id: int) -> tuple[str, str, str | None]:
        row = self.db.fetchone("SELECT * FROM api_keys WHERE id = ?", (key_id,))
        if row is None:
            raise KeyError("API key not found")
        return (
            str(row["provider"]),
            self.secret_box.decrypt(row["key_ciphertext"]),
            row["endpoint"],
        )

    def delete(self, key_id: int) -> None:
        if self.db.fetchone("SELECT id FROM api_keys WHERE id = ?", (key_id,)) is None:
            raise KeyError("API key not found")
        self.db.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
        self.db.add_audit_event("key_deleted", {"key_id": key_id})

    def edit(self, key_id: int, name: str, priority: int) -> dict[str, Any]:
        if self.db.fetchone("SELECT id FROM api_keys WHERE id = ?", (key_id,)) is None:
            raise KeyError("API key not found")
        self.db.execute(
            "UPDATE api_keys SET name = ?, priority = ?, updated_at = ? WHERE id = ?",
            (name.strip(), priority, utc_now(), key_id),
        )
        row = self.db.fetchone("SELECT * FROM api_keys WHERE id = ?", (key_id,))
        return self.public_row(dict(row)) if row else {}

    def update_models(self, key_id: int, models: list[str]) -> None:
        self.db.execute(
            "UPDATE api_keys SET models_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(sorted(set(models))), utc_now(), key_id),
        )

    def resume(self, key_id: int) -> dict[str, Any]:
        self.db.execute(
            """
            UPDATE api_keys
            SET status = 'active', paused_until = NULL, last_error = NULL, updated_at = ?
            WHERE id = ?
            """,
            (utc_now(), key_id),
        )
        row = self.db.fetchone("SELECT * FROM api_keys WHERE id = ?", (key_id,))
        if row is None:
            raise KeyError("API key not found")
        return self.public_row(dict(row))

    def list_public(self) -> list[dict[str, Any]]:
        self._release_expired_pauses()
        rows = self.db.fetchall("SELECT * FROM api_keys ORDER BY priority ASC, id ASC")
        return [self.public_row(dict(row)) for row in rows]

    def public_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Never expose ciphertext or a full API key to the browser."""
        row.pop("key_ciphertext", None)
        raw_models = row.pop("models_json", "[]")
        try:
            row["models"] = json.loads(raw_models or "[]")
        except (TypeError, json.JSONDecodeError):
            row["models"] = []
        row.pop("last_error", None)
        row.update(key_health(row))
        until = parse_datetime(row.get("paused_until"))
        row["pause_seconds"] = (
            max(0, round((until - datetime.now(timezone.utc)).total_seconds()))
            if until
            else None
        )
        return row

    def acquire(self, model: str | None = None, provider: str | None = None) -> tuple[int, str, str, str | None]:
        """Return id, secret, provider, and endpoint for the best matching key."""
        self._release_expired_pauses()
        rows = self.db.fetchall(
            """
            SELECT * FROM api_keys
            WHERE status = 'active' AND (paused_until IS NULL OR paused_until <= ?)
            ORDER BY priority ASC,
              CASE WHEN remaining_tokens IS NULL THEN 0 ELSE remaining_tokens END DESC,
              CASE WHEN remaining_requests IS NULL THEN 0 ELSE remaining_requests END DESC,
              id ASC
            """,
            (utc_now(),),
        )
        for row in rows:
            if provider and row["provider"] != provider:
                continue
            if model:
                try:
                    models = json.loads(row["models_json"] or "[]")
                except json.JSONDecodeError:
                    models = []
                if models and model not in models:
                    continue
            return (
                int(row["id"]),
                self.secret_box.decrypt(row["key_ciphertext"]),
                str(row["provider"]),
                row["endpoint"],
            )
        raise RuntimeError("No active API key supports the selected model")

    def _release_expired_pauses(self) -> None:
        now = datetime.now(timezone.utc)
        rows = self.db.fetchall("SELECT id, paused_until FROM api_keys WHERE status = 'paused'")
        for row in rows:
            until = parse_datetime(row["paused_until"])
            if until and until <= now:
                self.db.execute(
                    """
                    UPDATE api_keys SET status = 'active', paused_until = NULL,
                        last_error = NULL, updated_at = ? WHERE id = ?
                    """,
                    (utc_now(), row["id"]),
                )

    def update_from_headers(self, key_id: int, headers: Any) -> None:
        values = limits_from_headers(headers)
        if not values:
            return
        columns = ", ".join(f"{field} = ?" for field in values)
        self.db.execute(
            f"UPDATE api_keys SET {columns}, updated_at = ? WHERE id = ?",
            tuple(values.values()) + (utc_now(), key_id),
        )

    def mark_paused(self, key_id: int, error: str) -> None:
        paused_until = (datetime.now(timezone.utc) + timedelta(hours=PAUSE_HOURS)).isoformat()
        self.db.execute(
            """
            UPDATE api_keys SET status = 'paused', paused_until = ?, last_error = ?,
                updated_at = ? WHERE id = ?
            """,
            (paused_until, error[:500], utc_now(), key_id),
        )
        self.db.add_audit_event("key_paused", {"key_id": key_id, "reason": error[:200]})

    def mark_success(self, key_id: int) -> None:
        self.db.execute(
            "UPDATE api_keys SET last_error = NULL, updated_at = ? WHERE id = ?",
            (utc_now(), key_id),
        )