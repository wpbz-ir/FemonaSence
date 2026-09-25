from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import NotificationJob, Subscription


REMINDER_SCHEDULE = (
    (7, "SUBSCRIPTION_EXPIRY_7D"),
    (1, "SUBSCRIPTION_EXPIRY_1D"),
)


async def schedule_subscription_reminders(
    session: AsyncSession,
    subscription: Subscription,
) -> None:
    """Create exactly-once reminder jobs at the subscription's expiry offsets."""
    for days, code in REMINDER_SCHEDULE:
        run_at = subscription.expires_at - timedelta(days=days)
        dedupe_key = f"subscription:{subscription.id}:{code}"
        exists = await session.scalar(
            select(NotificationJob.id).where(NotificationJob.dedupe_key == dedupe_key)
        )
        if exists:
            continue
        session.add(
            NotificationJob(
                user_id=subscription.user_id,
                subscription_id=subscription.id,
                notification_type=code,
                dedupe_key=dedupe_key,
                run_at=run_at,
                status="PENDING",
            )
        )


async def backfill_due_reminders(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> int:
    """Recover missing reminder jobs for active subscriptions after downtime."""
    current = now or datetime.now(timezone.utc)
    created = 0
    result = await session.execute(
        select(Subscription).where(
            Subscription.status == "ACTIVE",
            Subscription.expires_at > current,
            Subscription.expires_at <= current + timedelta(days=7),
        )
    )
    for subscription in result.scalars():
        for days, code in REMINDER_SCHEDULE:
            run_at = subscription.expires_at - timedelta(days=days)
            if run_at > current:
                continue
            dedupe_key = f"subscription:{subscription.id}:{code}"
            exists = await session.scalar(
                select(NotificationJob.id).where(NotificationJob.dedupe_key == dedupe_key)
            )
            if exists:
                continue
            session.add(
                NotificationJob(
                    user_id=subscription.user_id,
                    subscription_id=subscription.id,
                    notification_type=code,
                    dedupe_key=dedupe_key,
                    run_at=current,
                    status="PENDING",
                )
            )
            created += 1
    if created:
        await session.commit()
    return created
