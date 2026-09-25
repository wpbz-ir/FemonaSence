from __future__ import annotations

import asyncio
import logging
import os

from app.bot.app import create_bot
from app.workers.maintenance import maintenance_loop
from app.services.notification_jobs import recover_stale_notifications
from app.runtime.db import session_scope
from app.workers.notification_worker import run_notification_worker

logger = logging.getLogger("femona.runtime_worker")


async def run_runtime() -> None:
    bot, _dp = create_bot()
    interval = max(5, int(os.getenv("MAINTENANCE_INTERVAL_SECONDS", "15")))
    async def recovery_loop():
        while True:
            try:
                async with session_scope() as session:
                    await recover_stale_notifications(
                        session,
                        stale_seconds=max(300, interval * 20),
                    )
                    await session.commit()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("notification recovery loop failed")
            await asyncio.sleep(max(30, interval * 4))
    notification_task = asyncio.create_task(run_notification_worker(poll_seconds=1.0))
    maintenance_task = asyncio.create_task(maintenance_loop(bot, interval_seconds=interval))
    recovery_task = asyncio.create_task(recovery_loop())
    try:
        await asyncio.gather(notification_task, maintenance_task, recovery_task)
    finally:
        for task in (notification_task, maintenance_task, recovery_task):
            task.cancel()
        await asyncio.gather(notification_task, maintenance_task, recovery_task, return_exceptions=True)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run_runtime())
