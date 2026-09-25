from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
    return MediaSettings(
        worker_id=os.getenv("MEDIA_WORKER_ID", "media-worker-1").strip() or "media-worker-1",
        ffmpeg_bin=os.getenv("FFMPEG_BIN", "ffmpeg").strip() or "ffmpeg",
        ffprobe_bin=os.getenv("FFPROBE_BIN", "ffprobe").strip() or "ffprobe",
        work_root=root,
        worker_concurrency=concurrency,
        lease_seconds=max(60, _int("MEDIA_JOB_LEASE_SECONDS", 900)),
        heartbeat_seconds=max(15, _int("MEDIA_JOB_HEARTBEAT_SECONDS", 30)),
        poll_seconds=max(0.5, float(os.getenv("MEDIA_JOB_POLL_SECONDS", "1.0"))),
        bot_api_base_url=os.getenv("TELEGRAM_BOT_API_BASE_URL", "https://api.telegram.org").strip().rstrip("/"),
        storage_chat_id=chat,
        cleanup_temp=_bool("MEDIA_CLEANUP_TEMP", True),
        x264_preset=os.getenv("MEDIA_X264_PRESET", "medium").strip(),
        audio_bitrate=os.getenv("MEDIA_AUDIO_BITRATE", "128k").strip(),
        max_attempts=max(1, min(_int("MEDIA_MAX_JOB_ATTEMPTS", 3), 10)),
        ffmpeg_timeout_seconds=max(300, _int("MEDIA_FFMPEG_TIMEOUT_SECONDS", 21600)),
    )
