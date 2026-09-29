from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MediaSettings:
    worker_id: str
    ffmpeg_bin: str
    ffprobe_bin: str
    work_root: Path
    worker_concurrency: int
    lease_seconds: int
    heartbeat_seconds: int
    poll_seconds: float
    bot_api_base_url: str
    storage_chat_id: int | str | None
    cleanup_temp: bool
    x264_preset: str
    audio_bitrate: str
    max_attempts: int
    ffmpeg_timeout_seconds: int
    # کش مشترک فایل منبع: چند jobِ کیفیتِ هم‌منبع، یک‌بار دانلود می‌کنند
    # (MEDIA_SOURCE_CACHE=0 برای غیرفعال‌سازی).
    source_cache: bool


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    """[INT-c] همان قرارداد _int برای مقادیر اعشاری: مقدار نامعتبر → پیش‌فرض.

    قبلاً float(os.getenv(...)) محافظت‌نشده در load_media_settings اجرا می‌شد و
    یک تایپو (مثلاً «1,0») فرایند MediaWorker/اسکریپت‌ها را در استارت‌آپ می‌کشت.
    """
    try:
        return float(os.getenv(name, str(default)).strip())
    except ValueError:
        logger.warning(
            "متغیر محیطی %s عدد اعشاری معتبر نیست؛ از پیش‌فرض %s استفاده می‌شود.",
            name,
            default,
        )
        return default


def _bin_exists(value: str) -> bool:
    """Existence check for a binary given as a bare name OR a path."""
    if not value:
        return False
    candidate = Path(value).expanduser()
    if candidate.parent != Path("."):
        # Contains a directory component -> treat as an explicit path.
        # (On Windows-style values like C:\ffmpeg\bin\ffmpeg.exe this is also true.)
        return candidate.exists()
    return shutil.which(value) is not None


def validate_bot_api_base_url(raw: str) -> str:
    """[INT-c] اعتبارسنجی TELEGRAM_BOT_API_BASE_URL (اشتراکی بین media_config و
    telegram_storage).

    فقط https مجاز است، یا http روی host محلی (localhost / 127.0.0.1 / ::1) برای
    Bot API سرور محلی. مقدار نامعتبر کرش نمی‌کند: یک ERROR لاگ می‌شود و مسیر
    ابری رسمی (https://api.telegram.org) برگردانده می‌شود — دانلود/آپلود رسانه
    هرگز به یک نشانی با scheme اشتباه (مثلاً «api.telegram.org» بدون scheme که
    aiohttp فقط هنگام درخواست InvalidURL می‌دهد) نمی‌رود.
    """
    fallback = "https://api.telegram.org"
    candidate = (raw or "").strip().rstrip("/") or fallback
    try:
        parts = urlsplit(candidate)
        scheme = (parts.scheme or "").lower()
        host = (parts.hostname or "").lower()
    except ValueError:
        scheme, host = "", ""
    if scheme == "https":
        return candidate
    if scheme == "http" and host in {"localhost", "127.0.0.1", "::1"}:
        return candidate
    logger.error(
        "TELEGRAM_BOT_API_BASE_URL=%r نامعتبر است (فقط https، یا http روی "
        "localhost/127.0.0.1/::1 مجاز است)؛ به %s بازگشت داده شد.",
        candidate,
        fallback,
    )
    return fallback


def load_media_settings() -> MediaSettings:
    root = Path(os.getenv("MEDIA_WORK_ROOT", ".media_work")).expanduser().resolve()
    raw_chat = (os.getenv("TELEGRAM_STORAGE_CHAT_ID", "").strip() or os.getenv("PRODUCTION_STORAGE_CHAT_ID", "").strip())
    if raw_chat:
        try:
            chat: int | str = int(raw_chat)
        except ValueError:
            chat = raw_chat
    else:
        chat = None

    concurrency = max(1, min(_int("MEDIA_WORKER_CONCURRENCY", 1), 8))

    # [INT-c] وجود باینری‌ها در LOAD بررسی می‌شود (ERROR لاگ، بدون کرش): غیبت
    # ffmpeg/ffprobe در استارتاپ دیده می‌شود، ولی پروسه بالا می‌ماند — خرابیِ
    # هر job همان‌جا به‌صورت تمیز مدیریت می‌شود (mark_failure مسیر موجود است).
    ffmpeg_bin = os.getenv("FFMPEG_BIN", "ffmpeg").strip() or "ffmpeg"
    ffprobe_bin = os.getenv("FFPROBE_BIN", "ffprobe").strip() or "ffprobe"
    for env_name, bin_value in (("FFMPEG_BIN", ffmpeg_bin), ("FFPROBE_BIN", ffprobe_bin)):
        if not _bin_exists(bin_value):
            logger.error(
                "MEDIA: %s=%r پیدا نشد (نه به‌صورت مسیر موجود و نه در PATH)؛ "
                "کارهای transcoding شکست می‌خورند تا مسیر درست تنظیم شود.",
                env_name,
                bin_value,
            )

    return MediaSettings(
        worker_id=os.getenv("MEDIA_WORKER_ID", "media-worker-1").strip() or "media-worker-1",
        ffmpeg_bin=ffmpeg_bin,
        ffprobe_bin=ffprobe_bin,
        work_root=root,
        worker_concurrency=concurrency,
        lease_seconds=max(60, _int("MEDIA_JOB_LEASE_SECONDS", 900)),
        heartbeat_seconds=max(15, _int("MEDIA_JOB_HEARTBEAT_SECONDS", 30)),
        poll_seconds=max(0.5, _float("MEDIA_JOB_POLL_SECONDS", 1.0)),
        bot_api_base_url=validate_bot_api_base_url(
            os.getenv("TELEGRAM_BOT_API_BASE_URL", "https://api.telegram.org")
        ),
        storage_chat_id=chat,
        cleanup_temp=_bool("MEDIA_CLEANUP_TEMP", True),
        x264_preset=os.getenv("MEDIA_X264_PRESET", "medium").strip(),
        audio_bitrate=os.getenv("MEDIA_AUDIO_BITRATE", "128k").strip(),
        max_attempts=max(1, min(_int("MEDIA_MAX_JOB_ATTEMPTS", 3), 10)),
        ffmpeg_timeout_seconds=max(300, _int("MEDIA_FFMPEG_TIMEOUT_SECONDS", 21600)),
        source_cache=_bool("MEDIA_SOURCE_CACHE", True),
    )
