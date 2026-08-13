"""FastAPI entry point for the standalone AI Balancer panel."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.api.ws import router as ws_router
from app.config.database import Database
from app.core.chat_router import ChatRouter
from app.core.chat_manager import ChatManager
from app.core.crypto import SecretBox, SessionAuth
from app.core.file_builder import FileBuilder
from app.core.key_pool import KeyPool
from app.core.proxy_router import UpstreamProxy


BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
load_dotenv(PROJECT_DIR / ".env")


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


data_dir = Path(os.getenv("DATA_DIR", str(PROJECT_DIR / "data")))
database = Database(os.getenv("DATABASE_PATH", str(data_dir / "ai_balancer.sqlite3")))
secret_box = SecretBox(required_env("MASTER_KEY"))
auth = SessionAuth(required_env("SESSION_SECRET"), required_env("ADMIN_PASSWORD_HASH"))
key_pool = KeyPool(database, secret_box)
file_builder = FileBuilder(database, data_dir / "files")
chat_router = ChatRouter(key_pool)
chat_manager = ChatManager(data_dir / "chats")
upstream_proxy = UpstreamProxy(os.getenv("UPSTREAM_PROXY_URL"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Close SQLite cleanly when Uvicorn shuts down."""
    yield
    database.close()


app = FastAPI(
    title="AI Balancer",
    description="Private Groq API-key pool and streaming AI workspace.",
    version="1.0.0",
    lifespan=lifespan,
)
app.state.db = database
app.state.auth = auth
app.state.key_pool = key_pool
app.state.file_builder = file_builder
app.state.chat_router = chat_router
app.state.chat_manager = chat_manager
app.state.upstream_proxy = upstream_proxy
app.include_router(api_router)
app.include_router(ws_router)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/keys")
async def keys_page() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "keys.html")