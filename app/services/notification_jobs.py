from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text

from app.db.models import NotificationAttempt, NotificationJob, NotificationTemplate, User


DEFAULT_MESSAGES = {
    "SUBSCRIPTION_EXPIRY_7D": (
        "⏳ <b>{name}</b> گرامی،\n\n"
        "اشتراک شما <b>۷ روز دیگر</b> به پایان می‌رسد. برای ادامه استفاده، می‌توانید اشتراک خود را تمدید کنید."
    ),
    "SUBSCRIPTION_EXPIRY_1D": (
        "⚠️ <b>{name}</b> گرامی،\n\n"
        "اشتراک شما <b>۱ روز دیگر</b> به پایان می‌رسد. برای قطع نشدن دسترسی، تمدید را انجام دهید."
    ),
}


async def claim_notification_job(session, *, worker_id: str):
    row = await session.scalar(
        text(
            """
            SELECT id
            FROM notification_jobs
            WHERE status = 'PENDING'
              AND run_at <= CURRENT_TIMESTAMP
            ORDER BY run_at ASC, created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        )
    )
    if row is None:
        return None
    data = await session.execute(
        text(
            """
            UPDATE notification_jobs
            SET status = 'PROCESSING', locked_by = :worker_id,
                locked_at = CURRENT_TIMESTAMP, last_attempt_at = CURRENT_TIMESTAMP,
                attempts = attempts + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = :id AND status = 'PENDING'
            RETURNING id, user_id, template_id, notification_type, attempts
            """
        ),
        {"id": row, "worker_id": worker_id},
    )
    return data.mappings().first()


async def render_notification(session, *, job_id, user_id, notification_type: str) -> str:
    user = await session.get(User, user_id)
    name = ((user.first_name if user else None) or (user.username if user else None) or "کاربر").strip()
    template = await session.scalar(
        text(
            """
            SELECT body FROM notification_templates
            WHERE code = :code AND active IS TRUE
            LIMIT 1
            """
        ),
        {"code": notification_type},
    )
    body = template or DEFAULT_MESSAGES.get(notification_type, "🔔 اعلان جدید از فمونا سنس")
    return body.replace("{name}", name)


async def mark_notification_sent(session, *, job_id, worker_id: str, telegram_message_id: int | None):
    attempt = NotificationAttempt(
        notification_job_id=job_id,
        attempt_number=1,
        status="SENT",
        telegram_message_id=telegram_message_id,
        created_at=datetime.now(timezone.utc),
    )
    # Determine attempt number safely from DB to preserve uniqueness on retry.
    attempt_number = await session.scalar(
        text("SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM notification_attempts WHERE notification_job_id=:id"),
        {"id": job_id},
    )
    attempt.attempt_number = int(attempt_number or 1)
    session.add(attempt)
    await session.execute(
        text(
            """
            UPDATE notification_jobs
            SET status='SENT', sent_at=CURRENT_TIMESTAMP,
                locked_by=NULL, locked_at=NULL, last_error=NULL,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=:id AND locked_by=:worker
            """
        ),
        {"id": job_id, "worker": worker_id},
    )


async def mark_notification_failed(session, *, job, worker_id: str, error: str, max_attempts: int = 3):
    attempt_number = await session.scalar(
        text("SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM notification_attempts WHERE notification_job_id=:id"),
        {"id": job["id"]},
    )
    session.add(
        NotificationAttempt(
            notification_job_id=job["id"],
            attempt_number=int(attempt_number or 1),
            status="FAILED",
            error_message=error[:4000],
            created_at=datetime.now(timezone.utc),
        )
    )
    attempts = int(job["attempts"] or 0)
    if attempts >= max_attempts:
        status = "FAILED"
        run_at = "CURRENT_TIMESTAMP"
    else:
        status = "PENDING"
        run_at = "CURRENT_TIMESTAMP + INTERVAL '5 minutes'"
    await session.execute(
        text(
            f"""
            UPDATE notification_jobs
            SET status=:status,
                run_at={run_at},
                locked_by=NULL,
                locked_at=NULL,
                last_error=:error,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=:id AND locked_by=:worker
            """
        ),
        {"status": status, "error": error[:4000], "id": job["id"], "worker": worker_id},
    )


async def recover_stale_notifications(session, *, stale_seconds: int = 900) -> int:
    result = await session.execute(
        text(
            """
            UPDATE notification_jobs
            SET status='PENDING', locked_by=NULL, locked_at=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE status='PROCESSING'
              AND locked_at < CURRENT_TIMESTAMP - make_interval(secs => :seconds)
            """
        ),
        {"seconds": int(stale_seconds)},
    )
    return int(result.rowcount or 0)
