from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Subscription
from app.services.subscription_reminders import schedule_subscription_reminders


async def create_subscription(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    plan_id: uuid.UUID,
    duration_days: int,
    starts_at: datetime | None = None,
    auto_renew: bool = False,
) -> Subscription:
    """Create a subscription and its 7-day/1-day reminder jobs atomically."""
    start = starts_at or datetime.now(timezone.utc)
    expires = start + timedelta(days=duration_days)
    subscription = Subscription(
        user_id=user_id,
        plan_id=plan_id,
        starts_at=start,
        expires_at=expires,
        status="ACTIVE",
        auto_renew=auto_renew,
        extra_data={},
    )
    session.add(subscription)
    await session.flush()
    await schedule_subscription_reminders(session, subscription)
    # [FIX-B] No mid-service commit: session_scope owns the txn boundary (commit
    # on clean exit / rollback on exception). The old commit+refresh here split
    # the atomic unit (subscription + reminders) for no in-app caller.
    return subscription
