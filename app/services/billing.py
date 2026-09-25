from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from aiogram.types import LabeledPrice
from sqlalchemy import select

from app.db.models import Order, Payment, PaymentAttempt, Plan, Subscription
from app.services.subscription_reminders import schedule_subscription_reminders


def plan_stars(plan: Plan) -> int:
    features = plan.features or {}
    value = features.get("telegram_stars", features.get("stars", 0))
    return max(0, int(value or 0))


async def create_star_order(session, *, user_id, plan: Plan):
    stars = plan_stars(plan)
    if stars <= 0:
        raise ValueError("برای این پلن قیمت Telegram Stars تنظیم نشده است.")

    import secrets
    order_number = f"FMS-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{secrets.token_hex(6).upper()}"
    order = Order(
        order_number=order_number,
        user_id=user_id,
        plan_id=plan.id,
        amount_irr=Decimal(stars),
        currency="XTR",
        status="CREATED",
        description=f"اشتراک فمونا سنس: {plan.name_fa}",
        extra_data={"currency": "XTR", "telegram_stars": stars},
    )
    session.add(order)
    await session.flush()

    attempt = PaymentAttempt(
        order_id=order.id,
        provider="TELEGRAM_STARS",
        requested_amount_irr=Decimal(stars),
        status="CREATED",
        provider_invoice_id=order_number,
        raw_callback={},
    )
    session.add(attempt)
    await session.flush()

    payload = json.dumps(
        {
            "v": 1,
            "order_id": str(order.id),
            "order_number": order_number,
            "plan_id": str(plan.id),
        },
        separators=(",", ":"),
    )
    return order, attempt, payload, stars


async def settle_star_payment(session, *, order, attempt, successful_payment):
    # Lock the order row so two concurrent successful_payment updates cannot
    # both activate a subscription for the same order.
    locked_order = await session.scalar(
        select(Order).where(Order.id == order.id).with_for_update()
    )
    if locked_order is None:
        raise ValueError("سفارش پیدا نشد.")
    order = locked_order
    attempt = await session.scalar(
        select(PaymentAttempt)
        .where(
            PaymentAttempt.order_id == order.id,
            PaymentAttempt.provider == "TELEGRAM_STARS",
        )
        .order_by(PaymentAttempt.created_at.desc())
        .with_for_update()
    )
    if attempt is None:
        raise ValueError("تلاش پرداخت پیدا نشد.")

    charge_id = successful_payment.telegram_payment_charge_id
    existing = await session.scalar(
        select(Payment).where(
            Payment.provider == "TELEGRAM_STARS",
            Payment.provider_reference == charge_id,
        )
    )
    if existing:
        order.status = "PAID"
        attempt.status = "PAID"
        return existing
    if order.status == "PAID":
        existing_for_order = await session.scalar(
            select(Payment).where(Payment.order_id == order.id).order_by(Payment.created_at.desc())
        )
        if existing_for_order:
            return existing_for_order
        raise ValueError("سفارش قبلاً Paid شده ولی Payment record پیدا نشد.")

    stars = int(successful_payment.total_amount)
    expected = int((order.extra_data or {}).get("telegram_stars") or int(attempt.requested_amount_irr))
    if successful_payment.currency != "XTR":
        raise ValueError("واحد پرداخت Telegram Stars نامعتبر است.")
    if stars != expected:
        raise ValueError("مبلغ پرداخت‌شده با سفارش مطابقت ندارد.")

    payment = Payment(
        order_id=order.id,
        payment_attempt_id=attempt.id,
        provider="TELEGRAM_STARS",
        provider_reference=charge_id,
        amount_irr=Decimal(stars),
        status="PAID",
        paid_at=datetime.now(timezone.utc),
        extra_data={
            "currency": "XTR",
            "telegram_stars": stars,
            "telegram_payment_charge_id": charge_id,
            "provider_payment_charge_id": successful_payment.provider_payment_charge_id,
        },
    )
    session.add(payment)

    order.status = "PAID"
    attempt.status = "PAID"
    attempt.raw_callback = {
        "currency": successful_payment.currency,
        "total_amount": successful_payment.total_amount,
        "telegram_payment_charge_id": charge_id,
    }

    plan = await session.get(Plan, order.plan_id)
    if plan is None:
        raise ValueError("Plan سفارش پیدا نشد.")

    now = datetime.now(timezone.utc)
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
            extra_data={"source": "TELEGRAM_STARS"},
        )
        session.add(subscription)
        await session.flush()

    await schedule_subscription_reminders(session, subscription)
    return payment
