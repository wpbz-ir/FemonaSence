from __future__ import annotations

import asyncio
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from fastapi import Request
from fastapi.responses import StreamingResponse


def _allowed_source(url: str) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return False

    allowlist = [
        item.strip().lower()
        for item in os.getenv("MEDIA_UPSTREAM_ALLOWLIST", "").split(",")
        if item.strip()
    ]
    configured = urlsplit(os.getenv("TELEGRAM_BOT_API_BASE_URL", "https://api.telegram.org")).hostname
    host = parsed.hostname.lower()
    if host == "api.telegram.org" and configured == "api.telegram.org":
        return True
    if not allowlist:
        if os.getenv("APP_ENV", "development").lower() == "production":
            return os.getenv("MEDIA_ALLOW_ANY_HTTPS", "0").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
        return True

    return any(host == item or host.endswith("." + item) for item in allowlist)


async def proxy_media(source_url: str, request: Request):
    if not _allowed_source(source_url):
        raise ValueError("Upstream media host is not allowed.")

    headers: dict[str, str] = {}
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]
    if request.headers.get("if-none-match"):
        headers["If-None-Match"] = request.headers["if-none-match"]

    timeout = aiohttp.ClientTimeout(total=None, connect=20, sock_read=120)
    client = aiohttp.ClientSession(timeout=timeout, raise_for_status=False)
    try:
        upstream = await client.get(source_url, headers=headers, allow_redirects=False)
    except Exception:
        await client.close()
        raise

    if upstream.status >= 300 and upstream.status != 304:
        status = upstream.status
        await upstream.release()
        await client.close()
        raise PermissionError(f"Upstream returned HTTP {status}")

    response_headers: dict[str, str] = {}
    for name in (
        "Content-Length",
        "Content-Range",
        "Accept-Ranges",
        "Content-Type",
        "ETag",
        "Last-Modified",
    ):
        value = upstream.headers.get(name)
        if value:
            response_headers[name] = value

    async def body():
        try:
            async for chunk in upstream.content.iter_chunked(1024 * 1024):
                yield chunk
        finally:
            upstream.close()
            await client.close()

    return StreamingResponse(
        body(),
        status_code=upstream.status,
        media_type=upstream.headers.get("Content-Type", "video/mp4"),
        headers=response_headers,
    )



def _local_root() -> Path | None:
    raw = os.getenv("TELEGRAM_LOCAL_FILE_ROOT", "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def _safe_local_file(path: str) -> Path:
    root = _local_root()
    if root is None:
        raise ValueError("TELEGRAM_LOCAL_FILE_ROOT is not configured.")
    candidate = Path(path).expanduser().resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("Telegram local media path is outside the configured root.") from exc
    if not candidate.is_file():
        raise FileNotFoundError(candidate)
    return candidate


def _parse_range(value: str | None, size: int) -> tuple[int, int] | None:
    if not value or not value.startswith("bytes="):
        return None
    raw = value[6:].split(",", 1)[0].strip()
    if "-" not in raw:
        return None
    start_s, end_s = raw.split("-", 1)
    if not start_s:
        suffix = int(end_s)
        if suffix <= 0:
            return None
        start = max(0, size - suffix)
        return start, size - 1
    start = int(start_s)
    if start >= size:
        raise ValueError("range_not_satisfiable")
    end = int(end_s) if end_s else size - 1
    end = min(end, size - 1)
    if end < start:
        raise ValueError("range_not_satisfiable")
    return start, end


async def proxy_local_file(path: str, request: Request):
    file_path = _safe_local_file(path)
    size = file_path.stat().st_size
    media_type = mimetypes.guess_type(file_path.name)[0] or "video/mp4"

    try:
        selected = _parse_range(request.headers.get("range"), size)
    except ValueError as exc:
        if str(exc) == "range_not_satisfiable":
            return StreamingResponse(
                iter(()),
                status_code=416,
                headers={"Content-Range": f"bytes */{size}"},
                media_type=media_type,
            )
        raise

    start, end = selected if selected else (0, size - 1)
    length = max(0, end - start + 1)
    status = 206 if selected else 200
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Content-Type": media_type,
        "Content-Range": f"bytes {start}-{end}/{size}" if selected else "",
    }
    if not selected:
        headers.pop("Content-Range")

    async def body():
        handle = await asyncio.to_thread(file_path.open, "rb")
        try:
            await asyncio.to_thread(handle.seek, start)
            remaining = length
            while remaining > 0:
                chunk_size = min(1024 * 1024, remaining)
                chunk = await asyncio.to_thread(handle.read, chunk_size)
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
        finally:
            await asyncio.to_thread(handle.close)

    return StreamingResponse(body(), status_code=status, media_type=media_type, headers=headers)
