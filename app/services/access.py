from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import select
from app.db.models import AccessPolicy, Plan, Release, Subscription, User

async def can_access_release(session, user_id, release_id) -> tuple[bool, str]:
    status = await session.scalar(select(User.status).where(User.id == user_id))
    if status is None:
        return False, "کاربر پیدا نشد."
    if status != "ACTIVE":
        return False, "حساب کاربری شما فعال نیست."

    release_status = await session.scalar(select(Release.status).where(Release.id == release_id))
    if release_status is None:
        return False, "نسخه پیدا نشد."
    if str(release_status).upper() not in {"PUBLISHED", "ACTIVE", "PUBLIC"}:
        return False, "این نسخه هنوز برای پخش عمومی فعال نشده است."

    policy = await session.scalar(select(AccessPolicy).where(AccessPolicy.release_id == release_id))
    if policy is None:
        return True, ""
    if not policy.active:
        return False, "این نسخه در حال حاضر غیرفعال است."
    if not policy.requires_subscription:
        return True, ""

    subscription = await session.scalar(
        select(Subscription)
        .join(Plan, Plan.id == Subscription.plan_id)
        .where(
            Subscription.user_id == user_id,
            Subscription.status == "ACTIVE",
            Subscription.expires_at >= datetime.now(timezone.utc),
            Plan.active.is_(True),
        )
        .order_by(Subscription.expires_at.desc())
    )
    if subscription is None:
        return False, "برای دریافت این نسخه، اشتراک فعال لازم است."

    plan = await session.get(Plan, subscription.plan_id)
    if plan is None or int(plan.rank) < int(policy.minimum_plan_rank):
        return False, "سطح اشتراک شما برای این نسخه کافی نیست."
    return True, ""
