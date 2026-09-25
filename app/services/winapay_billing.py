from __future__ import annotations

import hashlib
import json
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _order_number(prefix: str = "FMS") -> str:
    stamp = _now().strftime("%Y%m%d%H%M%S")
    return f"{prefix}-{stamp}-{secrets.token_hex(4).upper()}"


async def create_winapay_subscription_order(session, *, user_id, plan: Plan, coupon_code: str | None = None):
    base_price = Decimal(plan_toman_price(plan))
    if base_price < 100:
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
    if amount < 100:
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
    if amount < 100:
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
    attempt.raw_callback = {"request": result.__dict__ if hasattr(result, "__dict__") else {}}
    await session.flush()
    return result.payment_url


async def settle_winapay_order(session, *, order_id, callback_payload: dict):
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

    amount_toman = Decimal(order.amount_toman or attempt.requested_amount_toman or 0)
    provider = WinaPayProvider()
    result = await provider.verify_payment(
        authority=attempt.authority,
        amount_toman=amount_toman,
        callback_payload=callback_payload,
    )
    attempt.raw_callback = callback_payload
    if not result.success:
        attempt.status = "FAILED"
        attempt.error_message = result.error_message or result.error_code
        await release_coupon_for_order(session, order_id=order.id)
        return None

    if result.amount is not None and Decimal(result.amount) != amount_toman:
        attempt.status = "FAILED"
        attempt.error_message = "مبلغ Verify شده با سفارش مطابقت ندارد."
        return None

    provider_reference = str(result.provider_reference or callback_payload.get("RefID") or "")
    if not provider_reference:
        raise ValueError("RefID ویناپی در Verify وجود ندارد.")

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

    existing = await session.scalar(
        select(Payment).where(
            Payment.provider == "WINAPAY",
            Payment.provider_reference == provider_reference,
        )
    )
    if existing:
        order.status = "PAID"
        attempt.status = "PAID"
        return existing

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

    await redeem_coupon_for_order(session, order_id=order.id)
    return payment
