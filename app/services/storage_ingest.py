from __future__ import annotations

import uuid
from datetime import datetime, timezone

from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

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

    # SELECT-then-INSERT race on uq_storage_files_provider_unique: two
    # concurrent ingests of the same file both passed the pre-SELECT above.
    # INSERT ... ON CONFLICT DO NOTHING makes exactly one row win (if the
    # conflicting txn is still open, this statement waits on the unique index
    # and skips once it commits), then we re-SELECT the surviving row so both
    # callers observe the same StorageFile instead of one crashing with an
    # uncaught IntegrityError. Dedupe semantics are unchanged.
    await session.execute(
        pg_insert(StorageFile).values(
            {
                StorageFile.id: uuid.uuid4(),
                StorageFile.provider_id: provider.id,
                StorageFile.chat_id: message.chat.id,
                StorageFile.message_id: message.message_id,
                StorageFile.file_id: file_id,
                StorageFile.file_unique_key: file_unique_id,
                StorageFile.filename: filename,
                StorageFile.mime_type: mime_type,
                StorageFile.size_bytes: size_bytes,
                StorageFile.status: "READY",
                StorageFile.storage_scope: "PRODUCTION",
                StorageFile.verified_at: datetime.now(timezone.utc),
                StorageFile.extra_data: media_metadata,
            }
        ).on_conflict_do_nothing(constraint="uq_storage_files_provider_unique")
    )
    return await session.scalar(
        select(StorageFile).where(
            StorageFile.provider_id == provider.id,
            StorageFile.file_unique_key == file_unique_id,
        )
    )
