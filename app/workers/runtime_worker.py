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

_RESTART_BASE_DELAY = 3.0
_RESTART_MAX_DELAY = 60.0
_RESTART_RESET_AFTER = 120.0  # seconds a child must run stably to reset backoff


async def _supervised(name: str, child) -> None:
    """Per-child supervision: run `child()` forever, restart it on crash.

    A crashing child must never take the whole runtime process down. Every
    non-cancellation exception is logged and the child is restarted with
    capped exponential backoff (the backoff resets once the child has been
    running stably for a while). A clean return from a child that is supposed
    to loop forever is treated as anomalous and restarted too.

    Only asyncio.CancelledError propagates — that is the shutdown signal — so
    cancellation still unwinds each child's own finally/cleanup blocks.
    """
    delay = _RESTART_BASE_DELAY
    loop = asyncio.get_running_loop()
    while True:
        started = loop.time()
        try:
            await child()
        except asyncio.CancelledError:
            raise
        except Exception:
            ran_for = loop.time() - started
            logger.exception(
                "supervised child %r crashed after %.1fs; restarting in %.1fs",
                name,
                ran_for,
                delay,
            )
            if ran_for >= _RESTART_RESET_AFTER:
                delay = _RESTART_BASE_DELAY
        else:
            logger.warning("supervised child %r exited cleanly; restarting", name)
            delay = _RESTART_BASE_DELAY
        await asyncio.sleep(delay)
        delay = min(_RESTART_MAX_DELAY, max(_RESTART_BASE_DELAY, delay * 2))


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

    # Each child is wrapped in _supervised, so a crash restarts that child
    # instead of killing the process; the gather below additionally uses
    # return_exceptions=True so no single dead task can cancel the others.
    children = (
        ("notification-worker", lambda: run_notification_worker(poll_seconds=1.0)),
        ("maintenance-loop", lambda: maintenance_loop(bot, interval_seconds=interval)),
        ("notification-recovery", recovery_loop),
    )
    tasks = [
        asyncio.create_task(_supervised(name, factory), name=name)
        for name, factory in children
    ]
    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for task, result in zip(tasks, results):
            if isinstance(result, asyncio.CancelledError):
                continue
            if isinstance(result, BaseException):
                logger.error("supervised child %s ended with %r", task.get_name(), result)
    finally:
        # Shutdown: cancel every supervisor first, then wait for them to
        # unwind (CancelledError propagates through _supervised into each
        # child's own cleanup); return_exceptions=True keeps this gather from
        # raising while the shared bot session is closed.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run_runtime())
