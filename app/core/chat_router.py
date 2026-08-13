"""Provider-neutral streaming router with clean conversation history."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
import re
from typing import Any

from app.core.key_pool import KeyPool
from app.core.provider_factory import ProviderError, stream_chat


StatusCallback = Callable[[dict[str, Any]], Awaitable[None]]


class ChatRouter:
    """Stream only model content and fail over rate-limited keys."""

    default_model = "openai/gpt-oss-120b"
    system_prompt = """You are the AI Balancer engineering assistant.
Answer the user's request clearly and in the user's language.
When the user asks to create, build, or generate a project, include every generated file
as a separate block using exactly this format:
[FILE relative/path.ext]
file contents
[/FILE]
For binary files use:
[BINARY relative/path.ext base64]
base64 data
[/BINARY]
Do not put secrets, API keys, or private credentials into generated files.
Never expose internal key numbers, retry labels, or routing metadata in the answer."""

    def __init__(self, key_pool: KeyPool) -> None:
        self.key_pool = key_pool

    @staticmethod
    def clean_assistant_text(text: str) -> str:
        """Remove routing labels if an upstream model echoed internal metadata."""
        return re.sub(
            r"(?:Ответ\s+через\s+ключ|(?:ключ|key))\s*#?\s*\d+\s*:?\s*",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()

    @property
    def model(self) -> str:
        return self.default_model

    async def stream(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        status: StatusCallback | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield metadata and delta.content only; routing labels never enter the transcript."""
        clean_messages = [
            {"role": item["role"], "content": item["content"]}
            for item in messages
            if item.get("role") in {"user", "assistant", "system"}
            and isinstance(item.get("content"), str)
            and item.get("content", "").strip()
        ]
        if not clean_messages or clean_messages[0].get("role") != "system":
            clean_messages.insert(0, {"role": "system", "content": self.system_prompt})

        selected_model = model or self.default_model
        attempted: set[int] = set()
        max_attempts = max(1, len(self.key_pool.list_public()))
        last_error = "No provider request was completed"
        for attempt in range(max_attempts):
            key_id: int | None = None
            try:
                key_id, api_key, provider, endpoint = self.key_pool.acquire(selected_model)
                if key_id in attempted:
                    raise RuntimeError("No additional active API keys are available")
                attempted.add(key_id)
                meta = {"type": "route", "provider": provider, "model": selected_model}
                await self._notify(status, meta)
                yield meta
                async for event in stream_chat(
                    provider,
                    api_key,
                    selected_model,
                    clean_messages,
                    endpoint,
                ):
                    if "headers" in event:
                        self.key_pool.update_from_headers(key_id, event["headers"])
                    content = event.get("content", "")
                    if content:
                        yield {"type": "token", "content": content}
                self.key_pool.mark_success(key_id)
                done = {"type": "done", "provider": provider, "model": selected_model}
                yield done
                return
            except Exception as exc:
                last_error = str(exc) or "Unknown provider error"
                status_code = getattr(exc, "status_code", None)
                is_rate_limit = status_code == 429 or "429" in last_error or "rate limit" in last_error.lower()
                if key_id is not None and is_rate_limit:
                    self.key_pool.mark_paused(key_id, last_error)
                if attempt + 1 < max_attempts and is_rate_limit:
                    retry = {
                        "type": "retry",
                        "message": "Лимит текущего ключа исчерпан. Переключаюсь на другой ключ.",
                    }
                    await self._notify(status, retry)
                    yield retry
                    continue
                error_event = {"type": "error", "message": last_error[:500]}
                await self._notify(status, error_event)
                yield error_event
                return
        yield {"type": "error", "message": last_error[:500]}

    @staticmethod
    async def _notify(callback: StatusCallback | None, event: dict[str, Any]) -> None:
        if callback:
            await callback(event)