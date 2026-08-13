"""Request and response shapes shared by the API routes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LoginPayload(BaseModel):
    password: str = Field(min_length=1, max_length=512)


class AddKeyPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    key: str = Field(min_length=8, max_length=512)
    provider: str | None = Field(default=None, max_length=40)
    endpoint: str | None = Field(default=None, max_length=500)
    priority: int = Field(default=100, ge=0, le=10000)


class DetectProviderPayload(BaseModel):
    key: str = Field(min_length=8, max_length=512)
    endpoint: str | None = Field(default=None, max_length=500)


class EditKeyPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    priority: int = Field(default=100, ge=0, le=10000)


class CreateChatPayload(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ChatMessagePayload(BaseModel):
    role: str = Field(pattern="^(user|assistant|system)$")
    content: str = Field(max_length=200000)
    timestamp: str | None = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)