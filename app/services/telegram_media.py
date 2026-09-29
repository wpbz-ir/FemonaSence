from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.bot.session import telegram_aiohttp_connector
from app.db.models import StorageFile

logger = logging.getLogger(__name__)

# [INT-c] Hard download cap: pre-flight reject on getFile's file_size plus a
# running byte cap inside the chunk loop. 2.5 GB gives the 2 GB local Bot API
# upload limit comfortable headroom while bounding disk-exhaustion from a
# malicious/compromised endpoint reporting a bogus (or no) file_size.
MAX_DOWNLOAD_BYTES = 2_500_000_000


def _local_bot_api_data_dir() -> Path:
    """دایرکتوری داده‌ی سرور Bot API محلی (پیش‌فرض رسمی telegram-bot-api --local).

    با TELEGRAM_LOCAL_FILE_ROOT (همان متغیری که مستندات استقرار برای ریشه‌ی
    فایل‌های Bot API محلی تعریف می‌کنند) قابل بازنویسی است. عمداً per-call
    خوانده می‌شود، نه import-time — چون media_worker پس از import ماژول‌ها
    load_dotenv() را اجرا می‌کند و خواندن زودهنگام .env را از دست می‌دهد.
    """
    return Path(
        os.getenv("TELEGRAM_LOCAL_FILE_ROOT", "/var/lib/telegram-bot-api").strip()
        or "/var/lib/telegram-bot-api"
    ).resolve()


class TelegramMediaError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class TelegramMediaClient:
    def __init__(self, *, token: str, base_url: str):
        if not token:
            raise TelegramMediaError("BOT_TOKEN_MISSING", "توکن ربات تنظیم نشده است.")
        self.token = token
        self.base_url = base_url.rstrip("/")
        # Bot API محلی = هر base_url‌ای غیر از کلود رسمی (همان تشخیص telegram_storage).
        self.local_bot_api = (urlsplit(self.base_url).hostname or "").lower() not in {"api.telegram.org"}
        # [INT-c] FINITE total timeout (was total=None): a dribbling stalled
        # transfer used to hang forever while _lease_heartbeat kept the lease
        # fresh, so recovery never fired and the job stayed RUNNING forever.
        # total=3600 bounds the whole download; sock_read still catches a
        # stalled connection inside it.
        self.timeout = aiohttp.ClientTimeout(total=3600, connect=60, sock_read=300)

    def _url(self, method: str) -> str:
        return f"{self.base_url}/bot{self.token}/{method}"

    def _file_url(self, path: str) -> str:
        return f"{self.base_url}/file/bot{self.token}/{path.lstrip('/') }"

    def _safe_local_copy_source(self, raw_file_path: str) -> Path | None:
        """اعتبارسنجی مسیر مطلقِ برگشتی از getFile سرور Bot API محلی.

        [Path Guard] پاسخ getFile در حالت local می‌تواند یک مسیر فایل‌سیستم
        باشد؛ این مسیر ورودیِ اعتمادنشده است و بدون محدودسازی، کپیِ آن یعنی
        خواندن دلخواه فایل از سرور. فقط وقتی (۱) کلاینت واقعاً به Bot API
        محلی وصل است، (۲) مسیر مطلق است و (۳) پس از resolve (شکستن symlink)،
        زیر دایرکتوری داده‌ی همان سرور می‌ماند، مسیرِ امن برگردانده می‌شود؛
        در هر حالت دیگر (کلود، مسیر نسبی، ../ ترِورسال، بیرون از پیشوند)
        None برمی‌گردد تا دانلود از مسیر HTTP فایل انجام شود.
        """
        if not self.local_bot_api:
            return None
        candidate = Path(raw_file_path)
        if not candidate.is_absolute():
            return None
        try:
            resolved = candidate.resolve(strict=False)
            resolved.relative_to(_local_bot_api_data_dir())
        except (OSError, ValueError):
            logger.warning(
                "getFile file_path خارج از دایرکتوری داده‌ی Bot API محلی رد شد: %.300s",
                raw_file_path,
            )
            return None
        return resolved

    async def _json(self, session, method: str, *, params=None, data=None, timeout=None):
        # [FIX] Per-request timeout override: aiohttp lets an explicit
        # request-level ClientTimeout fully REPLACE the session-level one
        # (which otherwise stays self.timeout, total=3600). The storage health
        # check passes a short total=30 timeout here so a hung endpoint cannot
        # stall the health check for an hour.
        async with session.post(
            self._url(method), params=params, data=data, timeout=timeout or self.timeout
        ) as response:
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
        # [INT-c] Pre-flight size cap: reject BEFORE touching the disk when the
        # provider reports a file_size over MAX_DOWNLOAD_BYTES.
        reported_size = info.get("file_size")
        try:
            reported_size = int(reported_size) if reported_size is not None else None
        except (TypeError, ValueError):
            reported_size = None
        if reported_size is not None and reported_size > MAX_DOWNLOAD_BYTES:
            raise TelegramMediaError(
                "FILE_TOO_LARGE",
                "حجم فایل از سقف دانلود (۲.۵ گیگابایت) بیشتر است.",
            )

        file_path = str(info.get("file_path") or "")
        if not file_path:
            raise TelegramMediaError("FILE_PATH_MISSING", "تلگرام مسیر فایل را برنگرداند.")

        # [Path Guard] فقط در حالت Bot API محلی و فقط برای مسیرهای مطلقِ داخل
        # دایرکتوری داده‌ی همان سرور، کپی مستقیم انجام می‌شود؛ بقیهٔ حالت‌ها
        # (و هر مسیر مشکوک) به دانلود HTTP فایل می‌روند.
        candidate = self._safe_local_copy_source(file_path)
        if candidate is not None and candidate.exists():
            if candidate.stat().st_size > MAX_DOWNLOAD_BYTES:
                raise TelegramMediaError(
                    "FILE_TOO_LARGE",
                    "فایل سرور Bot API محلی از سقف بیشتر است.",
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            # کپی فایل حجیم روی thread انجام می‌شود تا event loop بلاک نشود.
            await asyncio.to_thread(shutil.copy2, candidate, destination)
            return info

        url = self._file_url(file_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        async with aiohttp.ClientSession(timeout=self.timeout, connector=telegram_aiohttp_connector()) as session:
            async with session.get(url) as response:
                if response.status >= 400:
                    raise TelegramMediaError(
                        "TELEGRAM_FILE_DOWNLOAD_FAILED",
                        f"دانلود فایل از تلگرام ناموفق بود (HTTP {response.status}).",
                    )
                received = 0
                exceeded_cap = False
                try:
                    with destination.open("wb") as output:
                        async for chunk in response.content.iter_chunked(1024 * 1024):
                            received += len(chunk)
                            # [INT-c] Running byte cap: the server may lie about (or
                            # omit) file_size, so the stream itself is capped too.
                            if received > MAX_DOWNLOAD_BYTES:
                                exceeded_cap = True
                                break
                            await asyncio.to_thread(output.write, chunk)
                except BaseException:
                    # [FIX] Don't leave a multi-GB .partial file behind on a
                    # network error, timeout or task cancellation either —
                    # the with-block closes the handle before this unlink runs
                    # (safe on Windows); the worker rmtree stays as backstop.
                    destination.unlink(missing_ok=True)
                    raise
                if exceeded_cap:
                    # Close first (the with-block above), then unlink — safe on
                    # Windows too, where unlinking an open file fails.
                    destination.unlink(missing_ok=True)
                    raise TelegramMediaError(
                        "FILE_TOO_LARGE",
                        "حجم دریافتی از سقف دانلود گذشت.",
                    )
        return info

    async def send_video(self, *, chat_id: int | str, path: Path, caption: str, width: int | None = None, height: int | None = None, duration: int | None = None) -> dict:
        if not path.exists():
            raise TelegramMediaError("OUTPUT_MISSING", "فایل خروجی وجود ندارد.")
        # [FIX] Pre-send size guard: the cloud Bot API rejects sendVideo above
        # 50 MB while a local Bot API server (--local) accepts up to 2 GB.
        # Fail fast with a structured error BEFORE streaming minutes of
        # multipart just to be rejected mid-upload.
        max_send = 2_000_000_000 if self.local_bot_api else 49_000_000
        output_size = path.stat().st_size
        if output_size > max_send:
            limit_label = "سرور محلی (۲ گیگابایت)" if self.local_bot_api else "ابر (۵۰ مگابایت)"
            raise TelegramMediaError(
                "OUTPUT_TOO_LARGE",
                f"حجم خروجی از سقف ارسال Bot API {limit_label} بیشتر است؛ "
                "برای فایل‌های بزرگ باید سرور Bot API محلی فعال باشد.",
            )
        # HTTP multipart is streamed by aiohttp; the worker does not load the video into RAM.
        with path.open("rb") as video_handle:
            form = aiohttp.FormData()
            form.add_field("chat_id", str(chat_id))
            form.add_field("caption", caption[:1024])
            form.add_field("supports_streaming", "true")
            # [FIX] width/height/duration were accepted but never added to the
            # multipart form, so storage rows built from the sendVideo result
            # lost the transcoded dimensions forever.
            if width is not None:
                form.add_field("width", str(int(width)))
            if height is not None:
                form.add_field("height", str(int(height)))
            if duration is not None:
                form.add_field("duration", str(int(duration)))
            form.add_field(
                "video",
                video_handle,
                filename=path.name,
                content_type="video/mp4",
            )
            # [INT-c] FINITE total timeout here too (was total=None) — same
            # stalled-forever hazard as the download path.
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3600, connect=60, sock_read=600), connector=telegram_aiohttp_connector()) as session:
                return await self._json(session, "sendVideo", data=form)



async def find_storage_by_sha256(session, *, sha256: str):
    return await session.scalar(
        text(
            """
            SELECT id FROM storage_files
            WHERE status = 'READY'
              AND COALESCE(metadata->>'sha256', '') = :sha256
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
        raise TelegramMediaError("STORAGE_PROVIDER_MISSING", f"تأمین‌کننده ذخیره‌سازی {provider_code} یافت نشد.")

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

    # [FIX] SELECT-then-INSERT race on uq_storage_files_provider_unique: two
    # concurrent workers registering the same Telegram video could both pass
    # the pre-SELECT above and one died with an uncaught IntegrityError.
    # Mirror storage_ingest.ingest_storage_message: INSERT ... ON CONFLICT DO
    # NOTHING on the same constraint, then re-select the surviving row so both
    # callers observe the same storage_files.id.
    # NOTE: the physical column is `metadata` (the ORM attribute extra_data is
    # mapped_column("metadata", JSONB)); the previous raw INSERT named the
    # non-existent column `extra_data` and failed every registration with
    # UndefinedColumn.
    await session.execute(
        pg_insert(StorageFile).values(
            {
                StorageFile.id: uuid.uuid4(),
                StorageFile.provider_id: provider,
                StorageFile.chat_id: message.get("chat", {}).get("id"),
                StorageFile.message_id: message.get("message_id"),
                StorageFile.file_id: video.get("file_id"),
                StorageFile.file_unique_key: unique_key,
                StorageFile.filename: video.get("file_name"),
                StorageFile.mime_type: video.get("mime_type") or "video/mp4",
                StorageFile.size_bytes: video.get("file_size"),
                StorageFile.status: "READY",
                StorageFile.storage_scope: "PRODUCTION",
                StorageFile.verified_at: datetime.now(timezone.utc),
                StorageFile.extra_data: {
                    "source": "media_worker",
                    "job_id": str(source_job_id) if source_job_id else None,
                    "sha256": sha256,
                },
            }
        ).on_conflict_do_nothing(constraint="uq_storage_files_provider_unique")
    )
    stored_id = await session.scalar(
        select(StorageFile.id).where(
            StorageFile.provider_id == provider,
            StorageFile.file_unique_key == unique_key,
        )
    )
    if stored_id is None:
        # Unreachable unless the row vanished between insert and re-select;
        # fail loudly instead of returning None to mark_succeeded/attach.
        raise TelegramMediaError(
            "STORAGE_REGISTER_LOST",
            "ردیف ذخیره‌سازی پس از ثبت ناپدید شد.",
        )
    return stored_id


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
