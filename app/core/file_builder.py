"""Turn structured text and base64 output into safe downloadable ZIP archives."""

from __future__ import annotations

import base64
import io
import re
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from app.config.database import Database, utc_now


FILE_BLOCK_RE = re.compile(
    r"\[FILE\s+([^\]\r\n]+)\]\s*(.*?)\s*\[/FILE\]",
    re.IGNORECASE | re.DOTALL,
)
BINARY_BLOCK_RE = re.compile(
    r"\[BINARY\s+([^\]\r\n]+)\]\s*(.*?)\s*\[/BINARY\]",
    re.IGNORECASE | re.DOTALL,
)
FENCED_FILE_RE = re.compile(
    r"```[a-zA-Z0-9_+\-.]*\s*(?:file|path)\s*:\s*([^\r\n]+)\r?\n(.*?)```",
    re.IGNORECASE | re.DOTALL,
)


class FileBuilder:
    """Build archives while preventing path traversal and protected-file writes."""

    def __init__(self, database: Database, output_dir: str | Path) -> None:
        self.db = database
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def should_build(prompt: str) -> bool:
        lowered = prompt.lower()
        triggers = (
            "собери", "создай проект", "создай файлы", "упакуй", "архив",
            "файл", "generate", "build", "create a project", "export config", "zip",
        )
        return any(trigger in lowered for trigger in triggers)

    @staticmethod
    def _safe_path(path_text: str) -> str:
        normalized = path_text.strip().replace("\\", "/").lstrip("/")
        path = PurePosixPath(normalized)
        if not normalized or path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Unsafe generated path: {path_text}")
        if path.parts[0].lower() in {".git", ".env", "__pycache__"}:
            raise ValueError(f"Protected generated path: {path_text}")
        return str(path)

    def parse_files(self, response: str) -> tuple[dict[str, str], dict[str, bytes]]:
        """Parse text blocks and optional base64 blocks for arbitrary binary files."""
        text_files: dict[str, str] = {}
        binary_files: dict[str, bytes] = {}
        for match in FILE_BLOCK_RE.finditer(response):
            text_files[self._safe_path(match.group(1))] = match.group(2).strip() + "\n"
        if not text_files:
            for match in FENCED_FILE_RE.finditer(response):
                text_files[self._safe_path(match.group(1))] = match.group(2).strip() + "\n"
        for match in BINARY_BLOCK_RE.finditer(response):
            path = self._safe_path(match.group(1))
            try:
                binary_files[path] = base64.b64decode(re.sub(r"\s+", "", match.group(2)), validate=True)
            except (ValueError, base64.binascii.Error) as exc:
                raise ValueError(f"Invalid base64 for generated file {path}") from exc
        return text_files, binary_files

    def build_archive(self, prompt: str, response: str) -> dict[str, Any]:
        text_files, binary_files = self.parse_files(response)
        if not text_files and not binary_files:
            text_files = {
                "README.md": (
                    "# Generated project\n\n"
                    "This archive contains the complete assistant response for the request below.\n\n"
                    f"## Request\n\n{prompt.strip()}\n\n"
                ),
                "assistant-response.md": response.strip() + "\n",
            }
        archive_id = uuid.uuid4().hex
        filename = f"ai-balancer-export-{archive_id[:8]}.zip"
        archive_path = self.output_dir / filename
        with zipfile.ZipFile(
            archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for path, content in text_files.items():
                archive.writestr(path, content)
            for path, content in binary_files.items():
                archive.writestr(path, content)
        self.db.execute(
            """
            INSERT INTO generated_files (id, filename, path, size_bytes, created_at, prompt)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                archive_id, filename, str(archive_path), archive_path.stat().st_size,
                utc_now(), prompt[:2000],
            ),
        )
        return {
            "id": archive_id,
            "filename": filename,
            "download_url": f"/api/download/{archive_id}",
            "file_count": len(text_files) + len(binary_files),
            "size_bytes": archive_path.stat().st_size,
        }

    def list_archives(self) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT id, filename, size_bytes, created_at FROM generated_files ORDER BY created_at DESC"
        )
        return [
            {**dict(row), "download_url": f"/api/download/{row['id']}"}
            for row in rows
            if Path(row["filename"]).name == row["filename"]
        ]

    def get_archive(self, archive_id: str) -> dict[str, Any] | None:
        row = self.db.fetchone("SELECT * FROM generated_files WHERE id = ?", (archive_id,))
        return dict(row) if row else None