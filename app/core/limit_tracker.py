"""Parsing and presentation helpers for Groq x-ratelimit headers."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any


def parse_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_reset_seconds(value: Any) -> float | None:
    """Parse values such as 2m30s, 1.5s, or an integer number of seconds."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if text.isdigit():
        return float(text)
    matches = re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", text)
    if not matches:
        return None
    total = 0.0
    for number, unit in matches:
        multiplier = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
        total += float(number) * multiplier
    return total


def normalize_headers(headers: Any) -> dict[str, str]:
    """Convert SDK/httpx header objects into a plain lower-case dictionary."""
    if headers is None:
        return {}
    try:
        return {str(key).lower(): str(value) for key, value in headers.items()}
    except AttributeError:
        return {}


def limits_from_headers(headers: Any) -> dict[str, Any]:
    """Extract the Groq rate-limit fields we persist."""
    normalized = normalize_headers(headers)
    result: dict[str, Any] = {}
    mapping = {
        "limit_requests": "x-ratelimit-limit-requests",
        "remaining_requests": "x-ratelimit-remaining-requests",
        "limit_tokens": "x-ratelimit-limit-tokens",
        "remaining_tokens": "x-ratelimit-remaining-tokens",
    }
    for field, header in mapping.items():
        parsed = parse_int(normalized.get(header))
        if parsed is not None:
            result[field] = parsed
    reset_values = [
        normalized.get("x-ratelimit-reset-requests"),
        normalized.get("x-ratelimit-reset-tokens"),
    ]
    reset_seconds = next(
        (parsed for value in reset_values if (parsed := parse_reset_seconds(value)) is not None),
        None,
    )
    if reset_seconds is not None:
        result["reset_at"] = (
            datetime.now(timezone.utc) + timedelta(seconds=reset_seconds)
        ).isoformat()
    return result


def key_health(row: dict[str, Any]) -> dict[str, Any]:
    """Return a frontend-friendly status representation."""
    status = row.get("status", "active")
    remaining = row.get("remaining_tokens")
    limit = row.get("limit_tokens")
    ratio = None
    if isinstance(remaining, int) and isinstance(limit, int) and limit > 0:
        ratio = max(0, min(100, round(remaining / limit * 100)))
    if status == "paused":
        label = "На паузе"
        tone = "paused"
    elif ratio is not None and ratio <= 15:
        label = "Почти лимит"
        tone = "warning"
    else:
        label = "Активен"
        tone = "active"
    return {
        "status": status,
        "status_label": label,
        "status_tone": tone,
        "token_percent": ratio,
    }