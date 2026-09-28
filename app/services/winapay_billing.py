from __future__ import annotations

import dataclasses
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.db.models import (
    Order,
    Payment,
    PaymentAttempt,
    PaymentSession,
    Plan,
    Subscription,
    Wallet,
    WalletLedgerEntry,
)
from app.payments.winapay import WinaPayProvider
from app.services.payment_sessions import create_payment_session
from app.services.coupons import redeem_coupon_for_order, release_coupon_for_order, reserve_coupon
from app.services.pricing import plan_toman_price
from app.services.subscription_reminders import schedule_subscription_reminders

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _order_number(prefix: str = "FMS") -> str:
    stamp = _now().strftime("%Y%m%d%H%M%S")
    return f"{prefix}-{stamp}-{secrets.token_hex(4).upper()}"


async def create_winapay_subscription_order(session, *, user_id, plan: Plan, coupon_code: str | None = None):
    base_price = Decimal(plan_toman_price(plan))
    # [FIX-B] Decimal NaN/Infinity compare False against < 100, so the old
    # `base_price < 100` check silently passed NaN through to the gateway.
    if (not base_price.is_finite()) or base_price < 100:
        raise ValueError("قیمت نهایی پلن برای ویناپی باید حداقل 100 تومان باشد.")
    price = base_price
    order = Order(
        order_number=_order_number("FMS"),
        user_id=user_id,
        plan_id=plan.id,
        amount_irr=Decimal("0"),
        amount_toman=price,
        currency="TOMAN",
        status="CREATED",
        description=f"اشتراک فمونا سنس: {plan.name_fa}",
        extra_data={"purpose": "SUBSCRIPTION", "amount_toman": str(price), "base_amount_toman": str(base_price)},
    )
    session.add(order)
    await session.flush()
    if coupon_code:
        preview = await reserve_coupon(
            session, code=coupon_code, user_id=user_id, plan_id=plan.id, order_id=order.id,
            amount_toman=base_price, amount_stars=0,
        )
        # [P0-1] discount_toman is the TRUE discount amount (base - payable), so
        # base - discount is the payable price; the >= 100 Toman floor stays.
        price = base_price - Decimal(preview["discount_toman"] or 0)
        if price < 100:
            await release_coupon_for_order(session, order_id=order.id)
            raise ValueError("مبلغ پس از تخفیف باید حداقل 100 تومان باشد.")
        order.amount_toman = price
        order.extra_data.update({"amount_toman": str(price), "coupon_code": preview["code"], "coupon_discount_toman": str(preview["discount_toman"])})

    attempt = PaymentAttempt(
        order_id=order.id,
        provider="WINAPAY",
        requested_amount_irr=Decimal("0"),
        requested_amount_toman=price,
        status="CREATED",
        provider_invoice_id=order.order_number,
        raw_callback={},
    )
    session.add(attempt)
    await session.flush()
    token = await create_payment_session(
        session,
        order_id=order.id,
        user_id=user_id,
        provider="WINAPAY",
        purpose="SUBSCRIPTION",
    )
    return order, attempt, token


async def create_winapay_wallet_order(session, *, user_id, amount_toman: Decimal):
    amount = Decimal(amount_toman)
    # [FIX-B] is_finite guard: NaN < 100 is False → NaN used to pass to the gateway.
    if (not amount.is_finite()) or amount < 100:
        raise ValueError("حداقل مبلغ شارژ کیف پول 100 تومان است.")
    order = Order(
        order_number=_order_number("FMW"),
        user_id=user_id,
        plan_id=None,
        amount_irr=Decimal("0"),
        amount_toman=amount,
        currency="TOMAN",
        status="CREATED",
        description="شارژ کیف پول فمونا سنس",
        extra_data={"purpose": "WALLET_TOPUP", "amount_toman": str(amount)},
    )
    session.add(order)
    await session.flush()
    attempt = PaymentAttempt(
        order_id=order.id,
        provider="WINAPAY",
        requested_amount_irr=Decimal("0"),
        requested_amount_toman=amount,
        status="CREATED",
        provider_invoice_id=order.order_number,
        raw_callback={},
    )
    session.add(attempt)
    await session.flush()
    token = await create_payment_session(
        session,
        order_id=order.id,
        user_id=user_id,
        provider="WINAPAY",
        purpose="WALLET_TOPUP",
    )
    return order, attempt, token


async def prepare_winapay_payment(session, *, order: Order, attempt: PaymentAttempt, callback_url: str):
    amount = Decimal(order.amount_toman or attempt.requested_amount_toman or 0)
    # [FIX-B] is_finite guard: Postgres numeric can carry NaN/Infinity and the old
    # `amount < 100` check is False for NaN → NaN reached the gateway request.
    if (not amount.is_finite()) or amount < 100:
        raise ValueError("مبلغ سفارش ویناپی نامعتبر است.")
    provider = WinaPayProvider()
    result = await provider.create_payment(
        order_number=order.order_number,
        amount_toman=amount,
        description=order.description or "فمونا سنس",
        callback_url=callback_url,
    )
    if not result.success or not result.payment_url or not result.authority:
        attempt.status = "FAILED"
        attempt.error_message = result.error_message or result.error_code
        raise ValueError(result.error_message or "ایجاد تراکنش ویناپی ناموفق بود.")
    attempt.authority = result.authority
    attempt.payment_url = result.payment_url
    attempt.status = "PENDING"
    attempt.requested_amount_toman = amount
    # [P0-8] PaymentStartResult is frozen+slots=True -> instances have NO __dict__
    # (hasattr() was always False, so raw_callback persisted {}). asdict() reads
    # dataclass fields() and yields a JSON-safe dict for the jsonb column.
    attempt.raw_callback = {"request": dataclasses.asdict(result)}
    await session.flush()
    return result.payment_url


async def settle_winapay_order(session, *, order_id, callback_payload: dict):
    """[P1-13] Three-phase settle so the 30s gateway verify never runs inside a
    transaction that holds row locks (canonical P1-13 / 4-G08-c F3):

      Phase A — read order + attempt WITHOUT FOR UPDATE + sanity checks, then
                COMMIT (ends the read txn; nothing is locked during the HTTP call;
                app/api/payments.py no longer locks the order before calling us).
      Phase V — gateway verify OUTSIDE any transaction.
      Phase B — short write txn: re-SELECT order FOR UPDATE, re-check not already
                PAID (idempotent), then settle/credit/redeem exactly as before.

    All previous guards are preserved: verify amount compare, RefID dedupe via
    uq_payments_provider_reference, wallet-ledger unique external_reference,
    coupon redeem-once.
    """
    # ---- Phase A: lock-free read + sanity checks.
    order = await session.scalar(select(Order).where(Order.id == order_id))
    if order is None:
        raise ValueError("سفارش پیدا نشد.")
    if order.status == "PAID":
        paid = await session.scalar(
            select(Payment).where(Payment.order_id == order.id).order_by(Payment.created_at.desc())
        )
        return paid
    if order.status not in {"CREATED", "PENDING"}:
        raise ValueError("وضعیت سفارش برای Verify قابل قبول نیست.")

    attempt = await session.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.order_id == order.id, PaymentAttempt.provider == "WINAPAY")
        .order_by(PaymentAttempt.created_at.desc())
    )
    if attempt is None or not attempt.authority:
        raise ValueError("تلاش پرداخت معتبر پیدا نشد.")

    attempt_authority = attempt.authority
    amount_toman = Decimal(order.amount_toman or attempt.requested_amount_toman or 0)

    # End the Phase-A read txn: the HTTP verify below must run with NO open
    # transaction and NO row lock (pool-exhaustion / lock-stall guard).
    await session.commit()

    # ---- Phase V: gateway verify OUTSIDE any txn (30s timeout is normal here).
    provider = WinaPayProvider()
    result = await provider.verify_payment(
        authority=attempt_authority,
        amount_toman=amount_toman,
        callback_payload=callback_payload,
    )

    async def _verify_failure(message: str) -> Payment | None:
        """Persist a verify failure: FAILED attempt (with the raw callback payload)
        + release the coupon reservation so the per-user/max-uses quota is not
        leaked until the 30-min TTL (canonical P1-13 / 4-G01-c finding 2).

        [FIX-B] Concurrent duplicate callback guard: a duplicate callback can hit
        the gateway AFTER a parallel settle already consumed the Authority, so the
        re-verify returns non-100 for an order that is ALREADY PAID. Re-read the
        order inside this failure txn (FOR UPDATE — serializes against the other
        settle's Phase B) and NEVER downgrade a PAID order's attempt to FAILED:
        return its existing Payment instead, which the caller already treats as
        "settled" (webhook event PROCESSED + success page). Returns None for a
        genuine failure (caller keeps its FAILED/502 path)."""
        fresh_order = await session.scalar(
            select(Order).where(Order.id == order_id).with_for_update()
        )
        if fresh_order is not None and fresh_order.status == "PAID":
            return await session.scalar(
                select(Payment).where(Payment.order_id == order_id).order_by(Payment.created_at.desc())
            )
        fresh_attempt = await session.scalar(
            select(PaymentAttempt)
            .where(PaymentAttempt.order_id == order_id, PaymentAttempt.provider == "WINAPAY")
            .order_by(PaymentAttempt.created_at.desc())
            .with_for_update()
        )
        if fresh_attempt is not None:
            fresh_attempt.status = "FAILED"
            fresh_attempt.error_message = (message or "verify_failed")[:4000]
            fresh_attempt.raw_callback = callback_payload
        await release_coupon_for_order(session, order_id=order_id)
        return None

    if not result.success:
        return await _verify_failure(result.error_message or result.error_code or "verify_failed")

    # [P1-13][4-G08-c F7] Gateway Amount is MANDATORY in the verify response: a
    # missing amount is a verify FAILURE (never skip-compare), and any mismatch
    # now releases the coupon reservation (it used to leak the reservation).
    if result.amount is None:
        return await _verify_failure("پاسخ Verify ویناپی فاقد مبلغ است.")
    if Decimal(result.amount) != amount_toman:
        return await _verify_failure("مبلغ Verify شده با سفارش مطابقت ندارد.")

    # [P1-13][4-G08-c F4] provider_reference must come from the VERIFY RESPONSE
    # only — the user-controlled callback RefID fallback is REMOVED (a forged RefID
    # used to become the idempotency key against uq_payments_provider_reference).
    # Missing/blank RefID in a Status=100 response is a verify failure too.
    provider_reference = str(result.provider_reference or "").strip()
    if not provider_reference:
        return await _verify_failure("RefID ویناپی در پاسخ Verify وجود ندارد.")

    order = await session.scalar(
        select(Order).where(Order.id == order_id).with_for_update()
    )
    if order is None:
        raise ValueError("سفارش پیدا نشد.")
    if order.status == "PAID":
        existing_for_order = await session.scalar(
            select(Payment).where(Payment.order_id == order.id).order_by(Payment.created_at.desc())
        )
        if existing_for_order:
            return existing_for_order
        raise ValueError("سفارش Paid شده ولی Payment record پیدا نشد.")
    attempt = await session.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.order_id == order.id, PaymentAttempt.provider == "WINAPAY")
        .order_by(PaymentAttempt.created_at.desc())
        .with_for_update()
    )
    if attempt is None:
        raise ValueError("تلاش پرداخت معتبر پیدا نشد.")
    attempt.raw_callback = callback_payload

    # [FIX-B] Idempotency lookup is scoped to THIS order (mirror of the Stars
    # settle path): if this exact order already settled with this RefID, return
    # its Payment. A RefID is globally unique per gateway payment
    # (uq_payments_provider_reference), so it must never mark a DIFFERENT order
    # PAID from another order's payment row.
    existing = await session.scalar(
        select(Payment).where(
            Payment.provider == "WINAPAY",
            Payment.provider_reference == provider_reference,
            Payment.order_id == order.id,
        )
    )
    if existing:
        order.status = "PAID"
        attempt.status = "PAID"
        return existing
    # [FIX-B] Cross-order RefID collision: the same RefID already settled a
    # DIFFERENT order. Do NOT mark THIS order PAID (that would capture money
    # with no fulfillment) — raise so the caller's exception path marks the
    # attempt FAILED and the anomaly is visible to the operator.
    collision = await session.scalar(
        select(Payment.order_id).where(
            Payment.provider == "WINAPAY",
            Payment.provider_reference == provider_reference,
        )
    )
    if collision is not None:
        raise ValueError(
            f"RefID ویناپی {provider_reference} قبلاً برای سفارش دیگری ثبت شده است."
        )

    payment = Payment(
        order_id=order.id,
        payment_attempt_id=attempt.id,
        provider="WINAPAY",
        provider_reference=provider_reference,
        amount_irr=Decimal("0"),
        amount_toman=amount_toman,
        status="PAID",
        paid_at=_now(),
        extra_data={
            "provider": "WINAPAY",
            "ref_id": provider_reference,
            "payment_status": callback_payload.get("PaymentStatus"),
        },
    )
    session.add(payment)
    await session.flush()
    order.status = "PAID"
    attempt.status = "PAID"

    purpose = (order.extra_data or {}).get("purpose", "SUBSCRIPTION")
    if purpose == "WALLET_TOPUP":
        wallet = await session.scalar(
            select(Wallet).where(Wallet.user_id == order.user_id).with_for_update()
        )
        if wallet is None:
            wallet = Wallet(user_id=order.user_id, balance_irr=Decimal("0"), version=1)
            session.add(wallet)
            await session.flush()
        wallet_credit_irr = (amount_toman * Decimal("10")).quantize(Decimal("1"))
        wallet.balance_irr = Decimal(wallet.balance_irr or 0) + wallet_credit_irr
        wallet.version = int(wallet.version or 0) + 1
        session.add(
            WalletLedgerEntry(
                wallet_id=wallet.id,
                amount_irr=wallet_credit_irr,
                balance_after_irr=wallet.balance_irr,
                entry_type="CREDIT",
                external_reference=f"WINAPAY:{provider_reference}",
                description="شارژ کیف پول با ویناپی",
                extra_data={"order_id": str(order.id), "payment_id": str(payment.id)},
            )
        )
    else:
        plan = await session.get(Plan, order.plan_id)
        if plan is None:
            raise ValueError("Plan سفارش پیدا نشد.")
        # [FIX-B] Zero/negative-duration plans must never produce a PAID
        # zero-length (or worse, shortening) subscription — DB-level guard lives
        # in ck_plans_duration_days (migration 0018); settle-path backstop here.
        if plan.duration_days is None or plan.duration_days < 1:
            raise ValueError("مدت‌زمان اشتراک این پلن نامعتبر است.")
        now = _now()
        active = await session.scalar(
            select(Subscription)
            .where(
                Subscription.user_id == order.user_id,
                Subscription.status == "ACTIVE",
                Subscription.expires_at >= now,
            )
            .order_by(Subscription.expires_at.desc())
            .with_for_update()
        )
        starts_at = active.expires_at if active else now
        expires_at = starts_at + timedelta(days=plan.duration_days)
        if active:
            active.expires_at = expires_at
            active.plan_id = plan.id
            subscription = active
        else:
            subscription = Subscription(
                user_id=order.user_id,
                plan_id=plan.id,
                starts_at=starts_at,
                expires_at=expires_at,
                status="ACTIVE",
                auto_renew=False,
                extra_data={"source": "WINAPAY"},
            )
            session.add(subscription)
            await session.flush()
        await schedule_subscription_reminders(session, subscription)

    # [FIX-B] Same coupon-revalidation containment as the Stars settle path:
    # the bank payment is already verified at this point, so a deactivated/
    # expired coupon must not roll back the settlement (no Payment, no
    # fulfillment, WinaPay 502-retry loop). Payment stands; coupon not consumed;
    # operator sees the warning. Exactly-once happy path unchanged.
    try:
        await redeem_coupon_for_order(session, order_id=order.id)
    except ValueError as exc:
        logger.warning("coupon settle revalidation failed for order %s: %s", order.id, exc)
    return payment
