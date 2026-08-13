"""Optional, deliberately allow-listed upstream proxy helper."""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

import httpx


class UpstreamProxy:
    """Proxy only configured GET requests to a trusted upstream base URL."""

    def __init__(self, base_url: str | None) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    async def get(self, path: str) -> tuple[int, str, str]:
        if not self.base_url:
            raise RuntimeError("UPSTREAM_PROXY_URL is not configured")
        parsed = urlparse(path)
        if parsed.scheme or parsed.netloc or ".." in parsed.path.split("/"):
            raise ValueError("Only relative upstream paths are allowed")
        target = urljoin(f"{self.base_url}/", path.lstrip("/"))
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(target)
            return response.status_code, response.headers.get("content-type", "text/plain"), response.text