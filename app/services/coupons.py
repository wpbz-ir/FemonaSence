from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from sqlalchemy import and_, func, select, update

from app.db.models import Coupon, CouponRedemption, CouponTargetUser, User
from app.services.pricing import discounted_amount

RESERVATION_TTL = timedelta(minutes=30)


def normalize_coupon_code(value: str) -> str:
    return "".join((value or "").strip().upper().split())[:64]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _discount_toman(amount: Decimal, discount_type: str, value: Decimal) -> Decimal:
    if discount_type == "PERCENT":
        return discounted_amount(amount, value)
    return max(Decimal("0"), amount - value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _discount_stars(amount: int, value: Decimal) -> int:
    return int(discounted_amount(amount, value))


async def _release_stale_reservations(session, coupon_id: UUID, now: datetime) -> None:
    await session.execute(
        update(CouponRedemption)
        .where(
            CouponRedemption.coupon_id == coupon_id,
            CouponRedemption.status == "RESERVED",
            CouponRedemption.reserved_at < now - RESERVATION_TTL,
        )
        .values(status="RELEASED")
    )


async def _scope_ok(session, coupon: Coupon, *, user_id: UUID, plan_id: UUID) -> bool:
    if coupon.scope_type == "ALL":
        return True
    if coupon.scope_type == "PLAN":
        return coupon.scope_plan_id == plan_id
    if coupon.scope_type == "USERS":
        return (await session.scalar(
            select(func.count())
            .select_from(CouponTargetUser)
            .where(CouponTargetUser.coupon_id == coupon.id, CouponTargetUser.user_id == user_id)
        )) == 1
    return False


async def reserve_coupon(
    session,
    *,
    code: str,
    user_id: UUID,
    plan_id: UUID,
    order_id: UUID,
    amount_toman: Decimal,
    amount_stars: int,
) -> dict:
    normalized = normalize_coupon_code(code)
    if not normalized:
        raise ValueError("کد تخفیف خالی است.")
    coupon = await session.scalar(
        select(Coupon).where(Coupon.code == normalized).with_for_update()
    )
    if coupon is None or not coupon.active:
        raise ValueError("کد تخفیف معتبر یا فعال نیست.")
    now = _now()
    await _release_stale_reservations(session, coupon.id, now)
    if coupon.valid_from and coupon.valid_from > now:
        raise ValueError("زمان شروع این کد تخفیف هنوز نرسیده است.")
    if coupon.valid_until and coupon.valid_until < now:
        raise ValueError("اعتبار این کد تخفیف تمام شده است.")
    if not await _scope_ok(session, coupon, user_id=user_id, plan_id=plan_id):
        raise ValueError("این کد تخفیف برای این کاربر یا پلن قابل استفاده نیست.")

    used = int(await session.scalar(
        select(func.count()).select_from(CouponRedemption).where(
            CouponRedemption.coupon_id == coupon.id,
            CouponRedemption.status.in_(["RESERVED", "REDEEMED"]),
        )
    ) or 0)
    if coupon.max_uses is not None and used >= coupon.max_uses:
        raise ValueError("سقف استفاده از این کد تخفیف تکمیل شده است.")
    user_used = int(await session.scalar(
        select(func.count()).select_from(CouponRedemption).where(
            CouponRedemption.coupon_id == coupon.id,
            CouponRedemption.user_id == user_id,
            CouponRedemption.status.in_(["RESERVED", "REDEEMED"]),
        )
    ) or 0)
    if user_used >= coupon.per_user_limit:
        raise ValueError("این کد تخفیف قبلاً برای این کاربر استفاده یا رزرو شده است.")

    if coupon.discount_type == "FIXED_TOMAN" and amount_toman <= 0:
        raise ValueError("کد مبلغ ثابت فقط برای پرداخت تومانی قابل استفاده است.")
    discount_toman = _discount_toman(amount_toman, coupon.discount_type, coupon.value) if amount_toman > 0 else Decimal("0")
    discount_stars = _discount_stars(amount_stars, coupon.value) if coupon.discount_type == "PERCENT" and amount_stars > 0 else 0
    if coupon.discount_type == "FIXED_TOMAN" and amount_stars > 0:
        discount_stars = 0

    row = CouponRedemption(
        coupon_id=coupon.id,
        order_id=order_id,
        user_id=user_id,
        status="RESERVED",
        discount_amount_toman=discount_toman,
        discount_amount_stars=discount_stars,
        reserved_at=now,
    )
    session.add(row)
    await session.flush()
    return {
        "code": coupon.code,
        "name_fa": coupon.name_fa,
        "discount_type": coupon.discount_type,
        "value": str(coupon.value),
        "discount_toman": discount_toman,
        "discount_stars": discount_stars,
    }


async def redeem_coupon_for_order(session, *, order_id: UUID) -> None:
    row = await session.scalar(
        select(CouponRedemption).where(CouponRedemption.order_id == order_id).with_for_update()
    )
    if row is None or row.status == "REDEEMED":
        return
    if row.status == "RESERVED":
        row.status = "REDEEMED"
        row.redeemed_at = _now()
        await session.flush()


async def release_coupon_for_order(session, *, order_id: UUID) -> None:
    row = await session.scalar(
        select(CouponRedemption).where(CouponRedemption.order_id == order_id).with_for_update()
    )
    if row is None or row.status != "RESERVED":
        return
    row.status = "RELEASED"
    await session.flush()


async def preview_coupon(session, *, code: str, user_id: UUID, plan_id: UUID, amount_toman: Decimal, amount_stars: int) -> dict:
    normalized = normalize_coupon_code(code)
    coupon = await session.scalar(select(Coupon).where(Coupon.code == normalized))
    if coupon is None or not coupon.active:
        raise ValueError("کد تخفیف معتبر یا فعال نیست.")
    now = _now()
    if coupon.valid_from and coupon.valid_from > now:
        raise ValueError("زمان شروع این کد تخفیف هنوز نرسیده است.")
    if coupon.valid_until and coupon.valid_until < now:
        raise ValueError("اعتبار این کد تخفیف تمام شده است.")
    if not await _scope_ok(session, coupon, user_id=user_id, plan_id=plan_id):
        raise ValueError("این کد تخفیف برای این کاربر یا پلن قابل استفاده نیست.")
    discount_toman = _discount_toman(amount_toman, coupon.discount_type, coupon.value) if amount_toman > 0 else Decimal("0")
    discount_stars = _discount_stars(amount_stars, coupon.value) if coupon.discount_type == "PERCENT" and amount_stars > 0 else 0
    return {"code": coupon.code, "name_fa": coupon.name_fa, "discount_toman": discount_toman, "discount_stars": discount_stars, "discount_type": coupon.discount_type}
