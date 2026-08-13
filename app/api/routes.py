"""HTTP routes for authentication, chat history, providers, keys, and downloads."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse

from app.core.chat_manager import ChatManager
from app.core.chat_router import ChatRouter
from app.core.crypto import SessionAuth
from app.core.file_builder import FileBuilder
from app.core.key_pool import KeyPool
from app.core.provider_factory import (
    DEFAULT_MODELS,
    PROVIDER_LABELS,
    discover_models,
    detect_provider,
)
from app.core.proxy_router import UpstreamProxy
from app.models.schemas import (
    AddKeyPayload,
    CreateChatPayload,
    DetectProviderPayload,
    EditKeyPayload,
    LoginPayload,
)


router = APIRouter(prefix="/api")


def require_auth(request: Request) -> None:
    auth: SessionAuth = request.app.state.auth
    if not auth.is_valid(request.cookies.get(auth.cookie_name)):
        raise HTTPException(status_code=401, detail="Authentication required")


def services(
    request: Request,
) -> tuple[KeyPool, FileBuilder, ChatRouter, UpstreamProxy, ChatManager]:
    return (
        request.app.state.key_pool,
        request.app.state.file_builder,
        request.app.state.chat_router,
        request.app.state.upstream_proxy,
        request.app.state.chat_manager,
    )


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    key_pool: KeyPool = request.app.state.key_pool
    return {
        "ok": True,
        "service": "ai-balancer",
        "active_keys": sum(1 for key in key_pool.list_public() if key["status"] == "active"),
    }


@router.get("/session")
async def session(request: Request) -> dict[str, bool]:
    auth: SessionAuth = request.app.state.auth
    return {"authenticated": auth.is_valid(request.cookies.get(auth.cookie_name))}


@router.post("/login")
async def login(payload: LoginPayload, request: Request, response: Response) -> dict[str, bool]:
    auth: SessionAuth = request.app.state.auth
    if not auth.authenticate(payload.password):
        raise HTTPException(status_code=401, detail="Invalid administrator password")
    response.set_cookie(
        auth.cookie_name,
        auth.create(),
        max_age=60 * 60 * 24,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
    )
    request.app.state.db.add_audit_event("login_success", {})
    return {"authenticated": True}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, bool]:
    auth: SessionAuth = request.app.state.auth
    response.delete_cookie(auth.cookie_name)
    return {"authenticated": False}


@router.get("/status")
async def status(request: Request, _: None = Depends(require_auth)) -> dict[str, Any]:
    key_pool, _, chat_router, proxy, _ = services(request)
    keys = key_pool.list_public()
    return {
        "active_keys": sum(1 for key in keys if key["status"] == "active"),
        "paused_keys": sum(1 for key in keys if key["status"] == "paused"),
        "total_keys": len(keys),
        "proxy_enabled": proxy.enabled,
        "model": chat_router.model,
        "models": sorted({model for key in keys for model in key.get("models", [])}),
        "providers": sorted({key.get("provider") for key in keys if key.get("provider")}),
    }


@router.get("/models")
async def list_models(request: Request, _: None = Depends(require_auth)) -> dict[str, Any]:
    """Return the union of models advertised by every active key."""
    keys = request.app.state.key_pool.list_public()
    grouped: dict[str, dict[str, Any]] = {}
    for key in keys:
        provider = key.get("provider", "unknown")
        entry = grouped.setdefault(
            provider,
            {"provider": provider, "label": PROVIDER_LABELS.get(provider, provider), "models": []},
        )
        entry["models"].extend(key.get("models", []))
    for provider, defaults in DEFAULT_MODELS.items():
        if provider not in grouped and defaults:
            grouped[provider] = {
                "provider": provider,
                "label": PROVIDER_LABELS.get(provider, provider),
                "models": [],
            }
    for entry in grouped.values():
        entry["models"] = sorted(set(entry["models"]))
    return {
        "models": sorted(
            [
                {"id": model, "provider": entry["provider"], "label": entry["label"]}
                for entry in grouped.values()
                for model in entry["models"]
            ],
            key=lambda item: (item["provider"], item["id"]),
        ),
        "providers": list(grouped.values()),
    }


@router.post("/detect_provider")
async def detect_provider_route(
    payload: DetectProviderPayload,
    request: Request,
    _: None = Depends(require_auth),
) -> dict[str, Any]:
    try:
        provider = detect_provider(payload.key, payload.endpoint)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    models, error = await discover_models(provider, payload.key, payload.endpoint)
    return {"provider": provider, "label": PROVIDER_LABELS[provider], "models": models, "error": error}


@router.get("/keys")
async def list_keys(request: Request, _: None = Depends(require_auth)) -> list[dict[str, Any]]:
    return request.app.state.key_pool.list_public()


@router.post("/keys")
async def add_key(
    payload: AddKeyPayload,
    request: Request,
    _: None = Depends(require_auth),
) -> dict[str, Any]:
    key_pool: KeyPool = request.app.state.key_pool
    try:
        provider = payload.provider or detect_provider(payload.key, payload.endpoint)
        models, discovery_error = await discover_models(provider, payload.key, payload.endpoint)
        row = key_pool.add(
            payload.name,
            payload.key,
            provider,
            payload.endpoint,
            payload.priority,
            models,
        )
        row["discovery_error"] = discovery_error
        return row
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/keys/{key_id}")
async def edit_key(
    key_id: int,
    payload: EditKeyPayload,
    request: Request,
    _: None = Depends(require_auth),
) -> dict[str, Any]:
    try:
        return request.app.state.key_pool.edit(key_id, payload.name, payload.priority)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/keys/{key_id}")
async def delete_key(
    key_id: int,
    request: Request,
    _: None = Depends(require_auth),
) -> dict[str, bool]:
    try:
        request.app.state.key_pool.delete(key_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}


@router.post("/keys/{key_id}/resume")
async def resume_key(
    key_id: int,
    request: Request,
    _: None = Depends(require_auth),
) -> dict[str, Any]:
    try:
        return request.app.state.key_pool.resume(key_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/chats")
async def list_chats(request: Request, _: None = Depends(require_auth)) -> list[dict[str, Any]]:
    return services(request)[4].list()


@router.post("/chats")
async def create_chat(
    payload: CreateChatPayload,
    request: Request,
    _: None = Depends(require_auth),
) -> dict[str, Any]:
    return services(request)[4].create(payload.title)


@router.get("/chats/{chat_id}")
async def get_chat(
    chat_id: str,
    request: Request,
    _: None = Depends(require_auth),
) -> dict[str, Any]:
    chat = services(request)[4].get(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


@router.delete("/chats/{chat_id}")
async def delete_chat(
    chat_id: str,
    request: Request,
    _: None = Depends(require_auth),
) -> dict[str, bool]:
    try:
        services(request)[4].delete(chat_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}


@router.get("/files")
async def list_files(request: Request, _: None = Depends(require_auth)) -> list[dict[str, Any]]:
    return services(request)[1].list_archives()


@router.get("/download/{archive_id}")
async def download(
    archive_id: str,
    request: Request,
    _: None = Depends(require_auth),
) -> FileResponse:
    archive = services(request)[1].get_archive(archive_id)
    if not archive or not Path(archive["path"]).is_file():
        raise HTTPException(status_code=404, detail="Archive not found")
    return FileResponse(
        archive["path"],
        media_type="application/zip",
        filename=archive["filename"],
    )


@router.get("/proxy")
async def proxy(
    path: str,
    request: Request,
    _: None = Depends(require_auth),
) -> Response:
    upstream: UpstreamProxy = services(request)[3]
    try:
        code, content_type, body = await upstream.get(path)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=body, status_code=code, media_type=content_type)