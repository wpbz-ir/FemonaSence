from __future__ import annotations

from datetime import datetime, timezone

from aiogram.types import Message
from sqlalchemy import select

from app.db.models import StorageFile, StorageProvider


async def ingest_storage_message(session, message: Message):
    provider = await session.scalar(
        select(StorageProvider).where(StorageProvider.code == "TELEGRAM")
    )
    if provider is None:
        raise RuntimeError("TELEGRAM storage provider پیدا نشد. Seed اولیه را اجرا کنید.")

    file_id = None
    file_unique_id = None
    filename = None
    mime_type = None
    size_bytes = None
    media_metadata: dict = {
        "source": "telegram_channel_post",
        "chat_title": message.chat.title,
    }

    if message.video:
        file_id = message.video.file_id
        file_unique_id = message.video.file_unique_id
        size_bytes = message.video.file_size
        mime_type = message.video.mime_type
        filename = message.video.file_name
        media_metadata.update({
            "width": message.video.width,
            "height": message.video.height,
            "duration": message.video.duration,
            "video_width": message.video.width,
            "video_height": message.video.height,
        })
    elif message.document:
        mime_type = message.document.mime_type
        if mime_type and not mime_type.startswith("video/"):
            return None
        file_id = message.document.file_id
        file_unique_id = message.document.file_unique_id
        size_bytes = message.document.file_size
        filename = message.document.file_name
    else:
        return None

    if not file_unique_id:
        return None

    existing = await session.scalar(
        select(StorageFile).where(
            StorageFile.provider_id == provider.id,
            StorageFile.file_unique_key == file_unique_id,
        )
    )
    if existing:
        return existing

    storage = StorageFile(
        provider_id=provider.id,
        chat_id=message.chat.id,
        message_id=message.message_id,
        file_id=file_id,
        file_unique_key=file_unique_id,
        filename=filename,
        mime_type=mime_type,
        size_bytes=size_bytes,
        status="READY",
        storage_scope="PRODUCTION",
        verified_at=datetime.now(timezone.utc),
        extra_data=media_metadata,
    )
    session.add(storage)
    await session.flush()
    return storage
