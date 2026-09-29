from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from aiogram import Router
from aiogram.types import Message

from app.core.config import settings
from app.runtime.db import session_scope
from app.services.storage_ingest import ingest_storage_message


router = Router(name="storage")

logger = logging.getLogger(__name__)


_URL_RE = re.compile(r"^STREAM_URL\s*:\s*(https?://\S+)\s*$", re.I | re.M)


def _safe_stream_url(caption: str | None) -> str | None:
    match = _URL_RE.search(caption or "")
    if not match:
        return None
    value = match.group(1).strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or any(ord(ch) < 32 for ch in value):
        return None
    return value[:2048]


def _media_kind(message: Message) -> str:
    if message.video:
        return "video"
    if message.document:
        return f"document:{message.document.mime_type or 'unknown'}"
    if message.photo:
        return "photo"
    if message.video_note:
        return "video_note"
    if message.animation:
        return "animation"
    return "none"


@router.channel_post()
async def storage_channel_post(message: Message):
    configured = settings.production_storage_chat_id
    if configured is None:
        # [P1 security] بدون کانال ذخیره‌سازیِ پیکربندی‌شده، قبلاً «هر» کانالی که
        # ربات در آن عضو بود به منبع ورود داده تبدیل می‌شد (سطح تزریق محتوا:
        # پست‌های کانالِ مهاجم به storage_files و stream_url قابل‌اتصال می‌شدند).
        # ورود داده مطلقاً رد می‌شود تا مقدار .env تنظیم شود.
        logger.warning(
            "STORAGE_INGEST_REFUSED no storage chat configured chat=%s title=%r media=%s — "
            "set TELEGRAM_STORAGE_CHAT_ID to your storage chat id to enable ingest",
            message.chat.id,
            message.chat.title,
            _media_kind(message),
        )
        return
    if message.chat.id != configured:
        # [DIAG] قبلاً ناسازگاریِ شناسه کانال «بی‌صدا» رد می‌شد و عیب‌یابیِ
        # «چرا فایل من در پنل نیست» غیرممکن بود. حالا هر پستِ نادیده‌گرفته با
        # شناسه‌ی واقعی کانال ثبت می‌شود تا مقدار .env بر همین اساس اصلاح شود.
        logger.warning(
            "CHANNEL_POST_IGNORED chat=%s title=%r media=%s (configured storage chat=%s) — "
            "if this is your storage channel, set TELEGRAM_STORAGE_CHAT_ID to exactly this chat id",
            message.chat.id,
            message.chat.title,
            _media_kind(message),
            configured,
        )
        return

    async with session_scope() as session:
        storage = await ingest_storage_message(session, message)
        if storage is None:
            # [DIAG] پستِ بدون ویدئو (عکس، ویدیونوت، فایل غیر ویدئویی) قبلاً بی‌صدا
            # رد می‌شد؛ حالا دلیلش در لاگ می‌آید.
            logger.warning(
                "CHANNEL_POST_SKIPPED chat=%s message=%s media=%s — "
                "only a video or a video/* document is ingested",
                message.chat.id,
                message.message_id,
                _media_kind(message),
            )
            return
        stream_url = _safe_stream_url(message.caption or message.text)
        if stream_url:
            current = storage.extra_data or {}
            current["stream_url"] = stream_url
            current["stream_url_source"] = "storage_caption"
            storage.extra_data = current

    if storage:
        # print() جایگزین شد — لاگ ساخت‌یافته با ماژول‌_logger (قابل فیلتر/جمع‌آوری)
        logger.info(
            "STORAGE_INGESTED chat=%s message=%s file_unique_id=%s",
            message.chat.id,
            message.message_id,
            storage.file_unique_key,
        )
