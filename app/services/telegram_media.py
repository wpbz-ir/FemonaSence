from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import aiohttp
from sqlalchemy import text

from app.bot.session import telegram_aiohttp_connector


class TelegramMediaError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class TelegramMediaClient:
    def __init__(self, *, token: str, base_url: str):
        if not token:
            raise TelegramMediaError("BOT_TOKEN_MISSING", "BOT_TOKEN is not configured")
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=None, connect=60, sock_read=300)

    def _url(self, method: str) -> str:
        return f"{self.base_url}/bot{self.token}/{method}"

    def _file_url(self, path: str) -> str:
        return f"{self.base_url}/file/bot{self.token}/{path.lstrip('/') }"

    async def get_stream_source(self, file_id: str) -> dict:
        """Resolve a Telegram file to either a local path or an internal upstream URL.

        The returned URL is server-internal and must never be sent to the browser.
        """
        info = await self.get_file(file_id)
        file_path = str(info.get("file_path") or "").strip()
        if not file_path:
            raise TelegramMediaError("FILE_PATH_MISSING", "Telegram did not return file_path")

        path = Path(file_path)
        if path.is_absolute() and path.exists():
            return {"kind": "local", "path": str(path), "file_path": file_path}
        return {"kind": "upstream", "url": self._file_url(file_path), "file_path": file_path}

    async def _json(self, session, method: str, *, params=None, data=None):
        async with session.post(self._url(method), params=params, data=data, timeout=self.timeout) as response:
            payload = await response.json(content_type=None)
            if response.status >= 400 or not payload.get("ok"):
                description = str(payload.get("description") or f"HTTP {response.status}")
                raise TelegramMediaError(f"TELEGRAM_{method.upper()}_FAILED", description)
            return payload["result"]

    async def get_file(self, file_id: str) -> dict:
        async with aiohttp.ClientSession(timeout=self.timeout, connector=telegram_aiohttp_connector()) as session:
            return await self._json(session, "getFile", params={"file_id": file_id})

    async def download_file(self, *, file_id: str, destination: Path) -> dict:
        info = await self.get_file(file_id)
        file_path = str(info.get("file_path") or "")
        if not file_path:
            raise TelegramMediaError("FILE_PATH_MISSING", "Telegram did not return file_path")

        # Local Bot API may return an absolute filesystem path. Use it directly when visible to the worker.
        candidate = Path(file_path)
        if candidate.is_absolute() and candidate.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            # کپی فایل حجیم روی thread انجام می‌شود تا event loop بلاک نشود.
            await asyncio.to_thread(shutil.copy2, candidate, destination)
            return info

        url = self._file_url(file_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        async with aiohttp.ClientSession(timeout=self.timeout, connector=telegram_aiohttp_connector()) as session:
            async with session.get(url) as response:
                if response.status >= 400:
                    raise TelegramMediaError("TELEGRAM_FILE_DOWNLOAD_FAILED", f"HTTP {response.status}")
                with destination.open("wb") as output:
                    async for chunk in response.content.iter_chunked(1024 * 1024):
                        await asyncio.to_thread(output.write, chunk)
        return info

    async def send_video(self, *, chat_id: int | str, path: Path, caption: str, width: int | None = None, height: int | None = None, duration: int | None = None) -> dict:
        if not path.exists():
            raise TelegramMediaError("OUTPUT_MISSING", "Output media does not exist")
        # HTTP multipart is streamed by aiohttp; the worker does not load the video into RAM.
        with path.open("rb") as video_handle:
            form = aiohttp.FormData()
            form.add_field("chat_id", str(chat_id))
            form.add_field("caption", caption[:1024])
            form.add_field("supports_streaming", "true")
            form.add_field(
                "video",
                video_handle,
                filename=path.name,
                content_type="video/mp4",
            )
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, connect=60, sock_read=600), connector=telegram_aiohttp_connector()) as session:
                return await self._json(session, "sendVideo", data=form)



async def find_storage_by_sha256(session, *, sha256: str):
    return await session.scalar(
        text(
            """
            SELECT id FROM storage_files
            WHERE status = 'READY'
              AND COALESCE(extra_data->>'sha256', '') = :sha256
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"sha256": sha256},
    )


async def register_storage_file(session, *, provider_code: str, message: dict, source_job_id=None, sha256: str | None = None) -> object:
    provider = await session.scalar(
        text("SELECT id FROM storage_providers WHERE code = :code LIMIT 1"),
        {"code": provider_code},
    )
    if provider is None:
        raise TelegramMediaError("STORAGE_PROVIDER_MISSING", f"Storage provider {provider_code} not found")

    video = message.get("video") or {}
    unique_key = video.get("file_unique_id") or video.get("file_id")
    existing = await session.scalar(
        text(
            "SELECT id FROM storage_files WHERE provider_id = :provider_id AND file_unique_key = :key LIMIT 1"
        ),
        {"provider_id": provider, "key": unique_key},
    )
    if existing:
        return existing

    row = await session.scalar(
        text(
            """
            INSERT INTO storage_files
              (id, provider_id, chat_id, message_id, file_id, file_unique_key, filename,
               mime_type, size_bytes, status, storage_scope, verified_at, extra_data, created_at)
            VALUES
              (gen_random_uuid(), :provider_id, :chat_id, :message_id, :file_id, :file_unique_key,
               :filename, :mime_type, :size_bytes, 'READY', :storage_scope, :verified_at, CAST(:extra_data AS jsonb), CURRENT_TIMESTAMP)
            RETURNING id
            """
        ),
        {
            "provider_id": provider,
            "chat_id": message.get("chat", {}).get("id"),
            "message_id": message.get("message_id"),
            "file_id": video.get("file_id"),
            "file_unique_key": unique_key,
            "filename": video.get("file_name"),
            "mime_type": video.get("mime_type") or "video/mp4",
            "size_bytes": video.get("file_size"),
            "storage_scope": "PRODUCTION",
            "verified_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            "extra_data": __import__("json").dumps({"source": "media_worker", "job_id": str(source_job_id) if source_job_id else None, "sha256": sha256}, ensure_ascii=False),
        },
    )
    return row


async def attach_storage_to_release(session, *, release_id, storage_file_id) -> None:
    await session.execute(
        text(
            "UPDATE release_files SET is_primary = FALSE WHERE release_id = :release_id AND active IS TRUE"
        ),
        {"release_id": release_id},
    )
    await session.execute(
        text(
            """
            INSERT INTO release_files (id, release_id, storage_file_id, is_primary, active, created_at)
            VALUES (gen_random_uuid(), :release_id, :storage_file_id, TRUE, TRUE, CURRENT_TIMESTAMP)
            ON CONFLICT (release_id, storage_file_id)
            DO UPDATE SET is_primary = TRUE, active = TRUE
            """
        ),
        {"release_id": release_id, "storage_file_id": storage_file_id},
    )
