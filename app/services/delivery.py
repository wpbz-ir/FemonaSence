from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import Bot
from sqlalchemy import select

from app.db.models import Delivery, Release, ReleaseFile, StorageFile


async def deliver_release(
    session,
    bot: Bot,
    *,
    user_id,
    release_id,
    telegram_chat_id: int,
    ttl_seconds: int = 60,
):
    stmt = (
        select(StorageFile)
        .join(ReleaseFile, ReleaseFile.storage_file_id == StorageFile.id)
        .where(
            ReleaseFile.release_id == release_id,
            ReleaseFile.active.is_(True),
            StorageFile.provider_id.is_not(None),
            StorageFile.chat_id.is_not(None),
            StorageFile.message_id.is_not(None),
            StorageFile.status == "READY",
        )
        .order_by(ReleaseFile.is_primary.desc(), StorageFile.created_at.desc())
        .limit(1)
    )
    storage = await session.scalar(stmt)
    if storage is None:
        raise ValueError("برای این Release فایل Storage آماده وجود ندارد.")

    message = await bot.copy_message(
        chat_id=telegram_chat_id,
        from_chat_id=storage.chat_id,
        message_id=storage.message_id,
    )

    now = datetime.now(timezone.utc)
    delivery = Delivery(
        user_id=user_id,
        release_id=release_id,
        telegram_chat_id=telegram_chat_id,
        telegram_message_id=message.message_id,
        sent_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
        status="ACTIVE",
    )
    session.add(delivery)
    await session.flush()
    return delivery


async def expire_deliveries(session, bot: Bot, limit: int = 50):
    now = datetime.now(timezone.utc)
    rows = list(
        (
            await session.scalars(
                select(Delivery)
                .where(
                    Delivery.status == "ACTIVE",
                    Delivery.expires_at <= now,
                )
                .order_by(Delivery.expires_at.asc())
                .limit(limit)
            )
        ).all()
    )

    completed = 0
    for delivery in rows:
        try:
            await bot.delete_message(
                chat_id=delivery.telegram_chat_id,
                message_id=delivery.telegram_message_id,
            )
            delivery.status = "DELETED"
            delivery.delete_error = None
        except Exception as exc:
            delivery.status = "DELETE_FAILED"
            delivery.delete_error = str(exc)[:2000]
        completed += 1

    if completed:
        await session.commit()
    return completed
