"""Durable JSON-file chat history with atomic writes and attachment metadata."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatManager:
    """Store one JSON document per conversation under the configured data directory."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, chat_id: str) -> Path:
        if not chat_id or Path(chat_id).name != chat_id or "/" in chat_id or "\\" in chat_id:
            raise ValueError("Invalid chat id")
        return self.directory / f"{chat_id}.json"

    def create(self, title: str | None = None) -> dict[str, Any]:
        timestamp = _now()
        chat_id = uuid.uuid4().hex
        chat = {
            "id": chat_id,
            "title": (title or "Новый чат").strip()[:200] or "Новый чат",
            "created_at": timestamp,
            "updated_at": timestamp,
            "messages": [],
        }
        self._write(chat)
        return chat

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            chats = []
            for path in self.directory.glob("*.json"):
                try:
                    chat = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(chat, dict) and chat.get("id"):
                        chats.append(self._summary(chat))
                except (OSError, json.JSONDecodeError):
                    continue
            return sorted(chats, key=lambda item: item["updated_at"], reverse=True)

    def get(self, chat_id: str) -> dict[str, Any] | None:
        try:
            path = self._path(chat_id)
        except ValueError:
            return None
        with self._lock:
            if not path.is_file():
                return None
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None

    def delete(self, chat_id: str) -> None:
        path = self._path(chat_id)
        with self._lock:
            if not path.is_file():
                raise KeyError("Chat not found")
            path.unlink()

    def append_message(
        self,
        chat_id: str,
        role: str,
        content: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        chat = self.get(chat_id)
        if chat is None:
            raise KeyError("Chat not found")
        message = {
            "role": role,
            "content": content,
            "timestamp": _now(),
            "attachments": attachments or [],
        }
        chat.setdefault("messages", []).append(message)
        if role == "user" and (not chat.get("title") or chat["title"] == "Новый чат"):
            chat["title"] = content.strip().replace("\n", " ")[:80] or "Новый чат"
        chat["updated_at"] = message["timestamp"]
        self._write(chat)
        return message

    def replace_last_assistant(
        self,
        chat_id: str,
        content: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        chat = self.get(chat_id)
        if chat is None:
            raise KeyError("Chat not found")
        messages = chat.setdefault("messages", [])
        if messages and messages[-1].get("role") == "assistant":
            messages[-1]["content"] = content
            messages[-1]["attachments"] = attachments or []
            messages[-1]["timestamp"] = _now()
            message = messages[-1]
        else:
            message = {
                "role": "assistant",
                "content": content,
                "timestamp": _now(),
                "attachments": attachments or [],
            }
            messages.append(message)
        chat["updated_at"] = message["timestamp"]
        self._write(chat)
        return message

    def _summary(self, chat: dict[str, Any]) -> dict[str, Any]:
        messages = chat.get("messages", [])
        return {
            "id": chat["id"],
            "title": chat.get("title") or "Новый чат",
            "created_at": chat.get("created_at"),
            "updated_at": chat.get("updated_at"),
            "message_count": len(messages) if isinstance(messages, list) else 0,
        }

    def _write(self, chat: dict[str, Any]) -> None:
        path = self._path(str(chat["id"]))
        temporary = path.with_suffix(".json.tmp")
        with self._lock:
            temporary.write_text(
                json.dumps(chat, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, path)