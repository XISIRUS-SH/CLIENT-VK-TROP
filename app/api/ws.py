"""Authenticated WebSocket endpoint for durable, provider-neutral chat streaming."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.chat_manager import ChatManager
from app.core.chat_router import ChatRouter
from app.core.crypto import SessionAuth
from app.core.file_builder import FileBuilder


router = APIRouter()


@router.websocket("/ws/chat")
async def chat_socket(websocket: WebSocket) -> None:
    auth: SessionAuth = websocket.app.state.auth
    if not auth.is_valid(websocket.cookies.get(auth.cookie_name)):
        await websocket.close(code=1008, reason="Authentication required")
        return
    await websocket.accept()
    chat_router: ChatRouter = websocket.app.state.chat_router
    file_builder: FileBuilder = websocket.app.state.file_builder
    chat_manager: ChatManager = websocket.app.state.chat_manager
    try:
        while True:
            payload = await websocket.receive_json()
            chat_id = payload.get("chat_id")
            messages = payload.get("messages")
            model = payload.get("model")
            if not isinstance(chat_id, str) or not chat_id:
                await websocket.send_json({"type": "error", "message": "chat_id is required"})
                continue
            if chat_manager.get(chat_id) is None:
                await websocket.send_json({"type": "error", "message": "Chat not found"})
                continue
            if not isinstance(messages, list):
                await websocket.send_json({"type": "error", "message": "messages must be a list"})
                continue
            prompt = next(
                (
                    item.get("content", "")
                    for item in reversed(messages)
                    if item.get("role") == "user" and isinstance(item.get("content"), str)
                ),
                "",
            )
            chat_manager.append_message(chat_id, "user", prompt)
            response_parts: list[str] = []
            attachments: list[dict[str, object]] = []
            async for event in chat_router.stream(messages, model=model):
                if event.get("type") == "token":
                    response_parts.append(str(event.get("content", "")))
                await websocket.send_json(event)
            assistant_text = chat_router.clean_assistant_text("".join(response_parts))
            if assistant_text:
                if file_builder.should_build(prompt):
                    try:
                        archive = file_builder.build_archive(prompt, assistant_text)
                        attachments.append(archive)
                        await websocket.send_json({"type": "file", "file": archive})
                    except ValueError as exc:
                        await websocket.send_json({"type": "error", "message": f"Archive was not created: {exc}"})
                chat_manager.append_message(chat_id, "assistant", assistant_text, attachments)
            await websocket.send_json({"type": "turn_complete"})
    except WebSocketDisconnect:
        return