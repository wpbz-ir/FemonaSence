from __future__ import annotations

import asyncio
import logging
import os

from app.bot.session import make_bot
from app.core.config import settings
from app.runtime.db import session_scope
from app.services.notification_jobs import (
    claim_notification_job,
    mark_notification_failed,
    mark_notification_sent,
    render_notification,
)

logger = logging.getLogger("femona.notification_worker")


async def run_notification_worker(*, poll_seconds: float = 1.0):
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not configured.")
    bot = make_bot()
    worker_id = os.getenv("NOTIFICATION_WORKER_ID", "notification-worker-1").strip() or "notification-worker-1"
    try:
        while True:
            async with session_scope() as session:
                job = await claim_notification_job(session, worker_id=worker_id)
                if job is None:
                    await session.commit()
                else:
                    try:
                        text = await render_notification(
                            session,
                            job_id=job["id"],
                            user_id=job["user_id"],
                            notification_type=job["notification_type"],
                        )
                        tg_id = await session.scalar(
                            __import__("sqlalchemy").text("SELECT telegram_user_id FROM users WHERE id=:id"),
                            {"id": job["user_id"]},
                        )
                        if tg_id is None:
                            raise RuntimeError("User Telegram ID not found.")
                        sent = await bot.send_message(tg_id, text)
                        await mark_notification_sent(
                            session,
                            job_id=job["id"],
                            worker_id=worker_id,
                            telegram_message_id=sent.message_id,
                        )
                    except Exception as exc:
                        logger.exception("notification failed")
                        await mark_notification_failed(
                            session,
                            job=job,
                            worker_id=worker_id,
                            error=str(exc),
                        )
                    await session.commit()
            await asyncio.sleep(max(0.5, poll_seconds))
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run_notification_worker())
