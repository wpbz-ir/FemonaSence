from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy import text

from app.bot.session import make_bot
from app.core.config import settings
from app.runtime.db import session_scope
from app.services.notification_jobs import (
    mark_notification_failed,
    mark_notification_sent,
    render_notification,
)

logger = logging.getLogger("femona.notification_worker")

# Bounded batch: how many PENDING rows a single claim txn marks PROCESSING.
CLAIM_BATCH_SIZE = 20

# Delivery semantics: AT-LEAST-ONCE with a narrow duplicate window.
#
# Structure is collect-then-send:
#   txn 1 claims a bounded batch (FOR UPDATE SKIP LOCKED -> PROCESSING) and
#   COMMITS. The Telegram send then happens OUTSIDE any transaction (a network
#   call can hang for 30s x retries; holding a FOR UPDATE lock across it would
#   stall every other worker). txn 2 marks each job SENT (or back to
#   PENDING/FAILED honoring the retry cap).
#
# If the process crashes after bot.send_message but before txn 2 commits, the
# row stays PROCESSING until recover_stale_notifications() flips stale
# PROCESSING rows back to PENDING, at which point it may be sent a second
# time. That is the accepted at-least-once window; it is kept narrow by:
#   * an ownership/status re-check (still PROCESSING + locked_by = us) right
#     before every single send,
#   * the per-job retry cap in mark_notification_failed (max_attempts=3),
#   * the stale-PROCESSING recovery timeout (recover_stale_notifications).
# Exactly-once delivery would require a Telegram-side idempotency key, which
# the Bot API does not offer.
_CLAIM_BATCH_SQL = text(
    """
    WITH candidates AS (
        SELECT id
        FROM notification_jobs
        WHERE status = 'PENDING'
          AND run_at <= CURRENT_TIMESTAMP
        ORDER BY run_at ASC, created_at ASC
        FOR UPDATE SKIP LOCKED
        LIMIT :batch
    )
    UPDATE notification_jobs j
    SET status = 'PROCESSING',
        locked_by = :worker_id,
        locked_at = CURRENT_TIMESTAMP,
        last_attempt_at = CURRENT_TIMESTAMP,
        attempts = j.attempts + 1,
        updated_at = CURRENT_TIMESTAMP
    FROM candidates c
    WHERE j.id = c.id
    RETURNING j.id, j.user_id, j.notification_type, j.attempts
    """
)

_OWNERSHIP_SQL = text("SELECT status, locked_by FROM notification_jobs WHERE id = :id")

_TELEGRAM_ID_SQL = text("SELECT telegram_user_id FROM users WHERE id = :id")


async def _deliver_one(bot, worker_id: str, job: dict) -> None:
    """Render and send a single claimed notification, then finalize it.

    Never lets an Exception escape: one bad row must not kill the batch or the
    worker. The Telegram send runs OUTSIDE any database transaction.
    """
    job_id = job["id"]
    try:
        # Short read txn: ownership re-check + render + recipient lookup.
        async with session_scope() as session:
            row = (await session.execute(_OWNERSHIP_SQL, {"id": job_id})).mappings().first()
            if row is None or row["status"] != "PROCESSING" or row["locked_by"] != worker_id:
                logger.warning(
                    "notification job %s no longer owned by %s (status=%s); skipping",
                    job_id,
                    worker_id,
                    None if row is None else row["status"],
                )
                return
            body = await render_notification(
                session,
                job_id=job_id,
                user_id=job["user_id"],
                notification_type=job["notification_type"],
            )
            tg_id = await session.scalar(_TELEGRAM_ID_SQL, {"id": job["user_id"]})
        if tg_id is None:
            raise RuntimeError("User Telegram ID not found.")

        # Network call with NO open transaction (claim txn already committed).
        sent = await bot.send_message(tg_id, body)

        # txn 2: mark SENT (the UPDATE itself is guarded by locked_by).
        async with session_scope() as session:
            await mark_notification_sent(
                session,
                job_id=job_id,
                worker_id=worker_id,
                telegram_message_id=sent.message_id,
            )
    except Exception as exc:
        logger.exception("notification job %s failed", job_id)
        try:
            # txn 2 (failure path): FAILED attempt row + retry-cap aware flip
            # back to PENDING (with run_at delay) or terminal FAILED.
            async with session_scope() as session:
                await mark_notification_failed(
                    session,
                    job=job,
                    worker_id=worker_id,
                    error=str(exc),
                )
        except Exception:
            logger.exception("could not record failure for notification job %s", job_id)


async def run_notification_worker(*, poll_seconds: float = 1.0):
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not configured.")
    bot = make_bot()
    worker_id = os.getenv("NOTIFICATION_WORKER_ID", "notification-worker-1").strip() or "notification-worker-1"
    try:
        while True:
            # OUTER GUARD: the worker must NEVER die from one bad row, DB
            # blip, or Telegram network error. The entire per-iteration body
            # lives inside this try; only CancelledError (shutdown) escapes.
            try:
                # txn 1: claim a bounded batch -> PROCESSING, then commit.
                # No row locks survive past this block (collect-then-send).
                async with session_scope() as session:
                    jobs = (
                        (
                            await session.execute(
                                _CLAIM_BATCH_SQL,
                                {"batch": CLAIM_BATCH_SIZE, "worker_id": worker_id},
                            )
                        )
                        .mappings()
                        .all()
                    )
                if jobs:
                    for job in jobs:
                        # Per-message try/except lives inside _deliver_one:
                        # one failure never blocks the rest of the batch.
                        await _deliver_one(bot, worker_id, dict(job))
                    continue  # backlog exists: drain before sleeping again
                await asyncio.sleep(max(0.5, poll_seconds))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("notification worker iteration failed")
                await asyncio.sleep(max(0.5, poll_seconds))
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run_notification_worker())
