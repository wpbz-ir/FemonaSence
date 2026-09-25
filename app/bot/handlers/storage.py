from __future__ import annotations

import re
from urllib.parse import urlparse

from aiogram import Router
from aiogram.types import Message

from app.core.config import settings
from app.runtime.db import session_scope
from app.services.storage_ingest import ingest_storage_message


router = Router(name="storage")


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


@router.channel_post()
async def storage_channel_post(message: Message):
    if settings.production_storage_chat_id is not None and message.chat.id != settings.production_storage_chat_id:
        return

    async with session_scope() as session:
        storage = await ingest_storage_message(session, message)
        if storage:
            stream_url = _safe_stream_url(message.caption or message.text)
            if stream_url:
                current = storage.extra_data or {}
                current["stream_url"] = stream_url
                current["stream_url_source"] = "storage_caption"
                storage.extra_data = current

    if storage:
        print(
            f"STORAGE_INGESTED chat={message.chat.id} "
            f"message={message.message_id} file_unique_id={storage.file_unique_key}"
        )
