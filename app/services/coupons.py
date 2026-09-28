from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update

from app.db.models import Coupon, CouponRedemption, CouponTargetUser, User
from app.services.pricing import discounted_amount

RESERVATION_TTL = timedelta(minutes=30)


def normalize_coupon_code(value: str) -> str:
    return "".join((value or "").strip().upper().split())[:64]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _payable_toman(amount: Decimal, discount_type: str, value: Decimal) -> Decimal:
    """Post-discount PAYABLE price in Toman (NOT the discount amount itself).

    [P0-1] The discount actually granted is amount - payable:
    PERCENT -> amount - discounted_amount(amount, pct); FIXED_TOMAN -> min(value, amount).
    """
    if discount_type == "PERCENT":
        return discounted_amount(amount, value)
    return max(Decimal("0"), amount - value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _payable_stars(amount: int, value: Decimal) -> int:
    """Post-discount PAYABLE price in Stars (NOT the discount amount itself)."""
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


async def _count_active_redemptions(
    session, coupon_id: UUID, now: datetime, *, user_id: UUID | None = None
) -> int:
    """Count coupon slots consumed: REDEEMED redemptions + still-ACTIVE holds.

    [P1-13] TTL-aware quota semantics (shared by reserve_coupon and
    preview_coupon — read-only, so preview cannot mutate): a RESERVED row stops
    counting once it outlives RESERVATION_TTL (30 min). Expired reservations are
    squatting garbage that reserve_coupon flips to RELEASED opportunistically
    (_release_stale_reservations); counting them would let one user hold the last
    slot of a scarce coupon forever by re-reserving unpaid orders. REDEEMED rows
    always count.
    """
    cutoff = now - RESERVATION_TTL
    conditions = [
        CouponRedemption.coupon_id == coupon_id,
        or_(
            CouponRedemption.status == "REDEEMED",
            and_(
                CouponRedemption.status == "RESERVED",
                CouponRedemption.reserved_at >= cutoff,
            ),
        ),
    ]
    if user_id is not None:
        conditions.append(CouponRedemption.user_id == user_id)
    return int(await session.scalar(
        select(func.count()).select_from(CouponRedemption).where(*conditions)
    ) or 0)


async def _assert_coupon_usable(
    session, coupon: Coupon, *, user_id: UUID, plan_id: UUID, now: datetime
) -> None:
    """[P1-13] THE single eligibility gate (validity window, scope, usage limits)
    shared by reserve_coupon AND preview_coupon, so a preview can never promise a
    discount that reserve/settle would later refuse (canonical P1-13 / 4-G01-c
    finding 4). Must stay in sync with what the settle path enforces.

    max_uses / per_user_limit semantics (see _count_active_redemptions): slots are
    consumed by REDEEMED redemptions and by RESERVED holds still inside
    RESERVATION_TTL; expired reservations never count.
    """
    if coupon.valid_from and coupon.valid_from > now:
        raise ValueError("زمان شروع این کد تخفیف هنوز نرسیده است.")
    if coupon.valid_until and coupon.valid_until < now:
        raise ValueError("اعتبار این کد تخفیف تمام شده است.")
    if not await _scope_ok(session, coupon, user_id=user_id, plan_id=plan_id):
        raise ValueError("این کد تخفیف برای این کاربر یا پلن قابل استفاده نیست.")
    used = await _count_active_redemptions(session, coupon.id, now)
    if coupon.max_uses is not None and used >= coupon.max_uses:
        raise ValueError("سقف استفاده از این کد تخفیف تکمیل شده است.")
    user_used = await _count_active_redemptions(session, coupon.id, now, user_id=user_id)
    if user_used >= coupon.per_user_limit:
        raise ValueError("این کد تخفیف قبلاً برای این کاربر استفاده یا رزرو شده است.")


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
    # [P1-13] Shared gate (validity/scope/max_uses/per_user_limit) — same checks
    # preview_coupon runs, so preview and reserve can never disagree. The stale
    # release above already cleared expired RESERVED rows, so the TTL-aware count
    # below matches exactly what is left active.
    await _assert_coupon_usable(session, coupon, user_id=user_id, plan_id=plan_id, now=now)

    if coupon.discount_type == "FIXED_TOMAN" and amount_toman <= 0:
        raise ValueError("کد مبلغ ثابت فقط برای پرداخت تومانی قابل استفاده است.")
    # [P0-1] discount_toman/discount_stars are TRUE discount amounts (base - payable):
    # PERCENT -> amount - discounted_amount(amount, pct); FIXED_TOMAN -> min(value, amount).
    discount_toman = (
        amount_toman - _payable_toman(amount_toman, coupon.discount_type, coupon.value)
        if amount_toman > 0
        else Decimal("0")
    )
    discount_stars = (
        amount_stars - _payable_stars(amount_stars, coupon.value)
        if coupon.discount_type == "PERCENT" and amount_stars > 0
        else 0
    )
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
    """Settle-time coupon redemption (exactly once per order).

    [5-INT-b / 5-G13-c] Accounting-bypass fix: the 30-min TTL sweep
    (_release_stale_reservations) may flip a STILL-PAYABLE order's row to
    RELEASED before the user actually pays (Stars invoices stay payable
    indefinitely). That row was legitimately reserved at order-creation time,
    so settle must count it: RESERVED→REDEEMED *and* RELEASED→REDEEMED are both
    accepted. Row ownership is guaranteed by the WHERE order_id == :order_id
    lookup (uq_coupon_redemption_order keeps one row per (coupon, order)).
    Second settle call sees status REDEEMED → no-op (idempotent).

    [5-INT-b / 5-G13-c] Settle-time re-validation (fail-closed): the coupon must
    still be active and inside its validity window at the moment of settlement —
    a stale invoice paid after admin deactivation/expiry must NOT count a
    redemption. Raise ValueError so the settle caller treats it as failure
    (callers: billing.settle_star_payment / winapay_billing.settle_winapay_order).
    """
    row = await session.scalar(
        select(CouponRedemption).where(CouponRedemption.order_id == order_id).with_for_update()
    )
    if row is None or row.status == "REDEEMED":
        return
    if row.status not in ("RESERVED", "RELEASED"):
        return
    coupon = await session.scalar(select(Coupon).where(Coupon.id == row.coupon_id))
    now = _now()
    if coupon is None or not coupon.active:
        raise ValueError("این کد تخفیف غیرفعال شده است؛ استفاده از آن در زمان تسویه ثبت نشد.")
    if coupon.valid_until and coupon.valid_until < now:
        raise ValueError("اعتبار این کد تخفیف در زمان تسویه تمام شده بود؛ تخفیف ثبت نشد.")
    row.status = "REDEEMED"
    row.redeemed_at = now
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
    # [P1-13] Preview enforces the SAME eligibility gate (validity, scope,
    # max_uses, per_user_limit) that reserve_coupon enforces — a preview can
    # never promise a discount that the settle path would refuse.
    await _assert_coupon_usable(session, coupon, user_id=user_id, plan_id=plan_id, now=now)
    # [P0-1] TRUE discount amounts (base - payable), same contract as reserve_coupon.
    discount_toman = (
        amount_toman - _payable_toman(amount_toman, coupon.discount_type, coupon.value)
        if amount_toman > 0
        else Decimal("0")
    )
    discount_stars = (
        amount_stars - _payable_stars(amount_stars, coupon.value)
        if coupon.discount_type == "PERCENT" and amount_stars > 0
        else 0
    )
    return {"code": coupon.code, "name_fa": coupon.name_fa, "discount_toman": discount_toman, "discount_stars": discount_stars, "discount_type": coupon.discount_type}
