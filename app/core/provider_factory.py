"""Provider detection, model discovery, and normalized streaming clients."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from groq import AsyncGroq


DEFAULT_MODELS: dict[str, list[str]] = {
    "groq": [
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "moonshotai/kimi-k2-instruct",
    ],
    "openai": ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4o"],
    "anthropic": ["claude-3-5-haiku-latest", "claude-3-5-sonnet-latest"],
    "openai-compatible": [],
}

PROVIDER_LABELS = {
    "groq": "Groq",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "openai-compatible": "OpenAI-compatible",
}


class ProviderError(RuntimeError):
    """Provider failure with enough context for retry and UI reporting."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def detect_provider(raw_key: str, endpoint: str | None = None) -> str:
    """Detect a provider from a key prefix first, then from its endpoint."""
    key = raw_key.strip().lower()
    endpoint_text = (endpoint or "").strip().lower()
    if key.startswith("gsk_") or "groq.com" in endpoint_text:
        return "groq"
    if key.startswith("sk-ant-") or "anthropic.com" in endpoint_text:
        return "anthropic"
    if key.startswith("sk-") or "openai.com" in endpoint_text:
        return "openai"
    if endpoint_text:
        return "openai-compatible"
    raise ValueError("Provider could not be detected. Use a recognized key or endpoint.")


def normalize_provider(provider: str | None, raw_key: str, endpoint: str | None) -> str:
    detected = detect_provider(raw_key, endpoint)
    value = (provider or detected).strip().lower()
    if value == "local":
        value = "openai-compatible"
    if value not in PROVIDER_LABELS:
        raise ValueError(f"Unsupported provider: {provider}")
    return value


def default_endpoint(provider: str, endpoint: str | None = None) -> str | None:
    """Return a canonical endpoint while preserving custom local gateways."""
    if endpoint and endpoint.strip():
        return endpoint.strip().rstrip("/")
    if provider == "openai":
        return "https://api.openai.com/v1"
    if provider == "anthropic":
        return "https://api.anthropic.com/v1"
    return None


def _models_from_payload(payload: Any) -> list[str]:
    values = payload.get("data", []) if isinstance(payload, dict) else []
    models = []
    for item in values:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            models.append(item["id"])
    return sorted(set(models))


async def discover_models(
    provider: str,
    api_key: str,
    endpoint: str | None = None,
) -> tuple[list[str], str | None]:
    """Load `/models` where supported, returning defaults and a diagnostic on failure."""
    provider = provider.lower()
    base = default_endpoint(provider, endpoint)
    if provider == "groq":
        request_url = "https://api.groq.com/openai/v1/models"
        headers = {"Authorization": f"Bearer {api_key}"}
    elif provider == "anthropic":
        request_url = f"{base or 'https://api.anthropic.com/v1'}/models"
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    else:
        request_url = f"{base or 'http://127.0.0.1:11434/v1'}/models"
        headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(request_url, headers=headers)
            response.raise_for_status()
            models = _models_from_payload(response.json())
            return (models or DEFAULT_MODELS.get(provider, []), None)
    except Exception as exc:
        return (DEFAULT_MODELS.get(provider, []), str(exc)[:300])


def _openai_url(provider: str, endpoint: str | None) -> str:
    base = default_endpoint(provider, endpoint)
    if base and base.endswith("/chat/completions"):
        return base
    return f"{base or 'http://127.0.0.1:11434/v1'}/chat/completions"


async def stream_chat(
    provider: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    endpoint: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield only normalized delta content plus response headers."""
    provider = provider.lower()
    if provider == "groq":
        client = AsyncGroq(api_key=api_key)
        completion = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=1,
            max_completion_tokens=2048,
            top_p=1,
            stream=True,
        )
        headers = getattr(completion, "headers", None)
        if headers is None:
            response = getattr(completion, "_response", None)
            headers = getattr(response, "headers", None)
        yield {"headers": headers}
        async for chunk in completion:
            choices = getattr(chunk, "choices", [])
            delta = getattr(choices[0], "delta", None) if choices else None
            content = getattr(delta, "content", None) if delta else None
            if content:
                yield {"content": content}
        return

    if provider == "anthropic":
        url = f"{default_endpoint(provider, endpoint) or 'https://api.anthropic.com/v1'}/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        system = next((item["content"] for item in messages if item["role"] == "system"), "")
        body = {
            "model": model,
            "max_tokens": 2048,
            "stream": True,
            "system": system,
            "messages": [
                {"role": item["role"], "content": item["content"]}
                for item in messages
                if item["role"] != "system"
            ],
        }
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", url, headers=headers, json=body) as response:
                if response.status_code >= 400:
                    raise ProviderError((await response.aread()).decode()[:500], response.status_code)
                yield {"headers": response.headers}
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "content_block_delta":
                        content = event.get("delta", {}).get("text", "")
                        if content:
                            yield {"content": content}
        return

    url = _openai_url(provider, endpoint)
    headers = {"Authorization": f"Bearer {api_key}", "content-type": "application/json"}
    body = {
        "model": model,
        "messages": messages,
        "temperature": 1,
        "max_tokens": 2048,
        "stream": True,
    }
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", url, headers=headers, json=body) as response:
            if response.status_code >= 400:
                raise ProviderError((await response.aread()).decode()[:500], response.status_code)
            yield {"headers": response.headers}
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                choices = event.get("choices", [])
                content = choices[0].get("delta", {}).get("content", "") if choices else ""
                if content:
                    yield {"content": content}