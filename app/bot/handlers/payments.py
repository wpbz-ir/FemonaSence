from __future__ import annotations

import json
from decimal import Decimal
from uuid import UUID

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message, PreCheckoutQuery
from sqlalchemy import select

from app.core.brand import BRAND_NAME_FA
from app.core.config import settings
from app.db.models import Order, PaymentAttempt, Plan
from app.runtime.db import session_scope
from app.services.billing import create_star_order, plan_stars, settle_star_payment
from app.services.coupons import normalize_coupon_code, preview_coupon
from app.services.pricing import plan_toman_price
from app.services.user_account import ensure_user
from app.services.winapay_billing import create_winapay_subscription_order, create_winapay_wallet_order


router = Router(name="payments")


class CouponState(StatesGroup):
    code = State()


def _bank_payment_available() -> bool:
    return settings.public_base_url.startswith("https://") and bool(settings.winapay_merchant_id)


def _fa_num(value) -> str:
    try:
        return f"{int(value):,}".translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
    except (TypeError, ValueError):
        return str(value or "—")


def _toman(value: Decimal | int | str) -> str:
    return f"{int(Decimal(str(value or 0))):,}".translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")) + " تومان"


async def _coupon_code(state: FSMContext) -> str | None:
    data = await state.get_data()
    code = normalize_coupon_code(data.get("coupon_code", ""))
    return code or None


async def show_plans(target: CallbackQuery | Message, state: FSMContext | None = None):
    if isinstance(target, CallbackQuery):
        await target.answer()
    coupon_code = await _coupon_code(state) if state is not None else None

    async with session_scope() as session:
        plans = list((await session.scalars(
            select(Plan).where(Plan.active.is_(True)).order_by(Plan.sort_order.asc())
        )).all())
        previews = {}
        if coupon_code:
            user = await ensure_user(session, target.from_user)
            for plan in plans:
                try:
                    previews[str(plan.id)] = await preview_coupon(
                        session,
                        code=coupon_code,
                        user_id=user.id,
                        plan_id=plan.id,
                        amount_toman=plan_toman_price(plan),
                        amount_stars=plan_stars(plan),
                    )
                except ValueError:
                    previews[str(plan.id)] = None

    rows = []
    if coupon_code:
        rows.append([InlineKeyboardButton(text=f"🎟 کد فعال: {coupon_code} · حذف", callback_data="cv:coupon:clear")])
    else:
        rows.append([InlineKeyboardButton(text="🎟 وارد کردن کد تخفیف", callback_data="cv:coupon:enter")])
    for plan in plans:
        stars = plan_stars(plan)
        bank_amount = plan_toman_price(plan)
        preview = previews.get(str(plan.id)) if coupon_code else None
        if preview:
            stars = max(1, stars - int(preview["discount_stars"] or 0))
            bank_amount = max(Decimal("0"), bank_amount - Decimal(preview["discount_toman"] or 0))
        title = f"💎 {plan.name_fa}"
        if getattr(plan, "discount_percent", 0):
            title += f" · {int(plan.discount_percent)}٪ تخفیف پلن"
        rows.append([InlineKeyboardButton(text=title, callback_data="cv:no-op")])
        buttons = []
        if stars > 0:
            buttons.append(InlineKeyboardButton(text=f"⭐ {_fa_num(stars)} Stars", callback_data=f"cv:buyplan:{plan.id}"))
        if bank_amount >= 100 and _bank_payment_available():
            buttons.append(InlineKeyboardButton(text=f"💳 {_toman(bank_amount)}", callback_data=f"cv:buywinapay:{plan.id}"))
        if buttons:
            rows.append(buttons)
    rows.append([InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")])
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    text = (
        f"<b>💎 اشتراک {BRAND_NAME_FA}</b>\n\n"
        "اشتراک موردنظر را انتخاب کنید.\n"
        "پرداخت با Telegram Stars یا درگاه بانکی ویناپی انجام می‌شود.\n"
        + (f"\nکد تخفیف «{coupon_code}» برای گزینه‌های مجاز اعمال می‌شود." if coupon_code else "")
    )
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


@router.message(F.text == "/subscription")
async def subscription_command(message: Message, state: FSMContext):
    await state.clear()
    await show_plans(message, state)


@router.callback_query(F.data == "menu:subscription")
async def subscription_menu(callback: CallbackQuery, state: FSMContext):
    await show_plans(callback, state)


@router.callback_query(F.data == "cv:coupon:enter")
async def coupon_enter(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(CouponState.code)
    await callback.message.answer("🎟 کد تخفیف را ارسال کنید. برای لغو /cancel را بفرستید.")


@router.callback_query(F.data == "cv:coupon:clear")
async def coupon_clear(callback: CallbackQuery, state: FSMContext):
    await callback.answer("کد تخفیف حذف شد")
    await state.clear()
    await show_plans(callback, state)


@router.message(CouponState.code)
async def coupon_message(message: Message, state: FSMContext):
    if (message.text or "").strip() == "/cancel":
        await state.clear()
        await message.answer("لغو شد.")
        await show_plans(message, state)
        return
    code = normalize_coupon_code(message.text or "")
    if not code:
        await message.answer("کد تخفیف معتبر نیست. دوباره ارسال کنید.")
        return
    await state.update_data(coupon_code=code)
    await state.set_state(CouponState.code)
    await message.answer(f"✅ کد «{code}» ثبت شد. پلن موردنظر را انتخاب کنید:")
    await show_plans(message, state)


@router.callback_query(F.data.regexp(r"^cv:buyplan:.+$"))
async def buy_plan(callback: CallbackQuery, state: FSMContext):
    plan_id = callback.data.split(":", 2)[2]
    await callback.answer()
    coupon_code = await _coupon_code(state)
    try:
        async with session_scope() as session:
            plan = await session.get(Plan, UUID(plan_id))
            if plan is None or not plan.active:
                await callback.message.answer("این پلن در دسترس نیست.")
                return
            user = await ensure_user(session, callback.from_user)
            if getattr(user, "status", "ACTIVE") != "ACTIVE":
                await callback.message.answer("حساب کاربری شما فعال نیست.")
                return
            order, _attempt, payload, stars = await create_star_order(
                session, user_id=user.id, plan=plan, coupon_code=coupon_code,
            )
    except ValueError as exc:
        await callback.message.answer(str(exc))
        return
    await state.clear()
    await callback.message.answer_invoice(
        title=f"اشتراک {plan.name_fa}",
        description=plan.description or f"{plan.duration_days} روز اشتراک {BRAND_NAME_FA}",
        payload=payload,
        currency="XTR",
        prices=[LabeledPrice(label=plan.name_fa, amount=stars)],
        provider_token="",
    )


@router.callback_query(F.data.regexp(r"^cv:buywinapay:.+$"))
async def buy_winapay(callback: CallbackQuery, state: FSMContext):
    plan_id = callback.data.split(":", 2)[2]
    await callback.answer()
    coupon_code = await _coupon_code(state)
    if not _bank_payment_available():
        await callback.message.answer("پرداخت بانکی فقط پس از تنظیم Merchant ID و نشانی عمومی HTTPS فعال می‌شود.")
        return
    try:
        async with session_scope() as session:
            plan = await session.get(Plan, UUID(plan_id))
            if plan is None or not plan.active or not plan.price_toman:
                await callback.message.answer("قیمت بانکی این پلن تنظیم نشده است.")
                return
            user = await ensure_user(session, callback.from_user)
            if getattr(user, "status", "ACTIVE") != "ACTIVE":
                await callback.message.answer("حساب کاربری شما فعال نیست.")
                return
            order, _attempt, token = await create_winapay_subscription_order(
                session, user_id=user.id, plan=plan, coupon_code=coupon_code,
            )
            amount = Decimal(order.amount_toman or 0)
    except ValueError as exc:
        await callback.message.answer(str(exc))
        return
    await state.clear()
    url = f"{settings.public_base_url}/payments/winapay/start/{token}"
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 ورود به درگاه ویناپی", url=url)],
        [InlineKeyboardButton(text="↩️ اشتراک‌ها", callback_data="menu:subscription")],
    ])
    await callback.message.edit_text(
        f"<b>💳 پرداخت بانکی</b>\n\n{plan.name_fa}\nمبلغ نهایی: <b>{_toman(amount)}</b>\n\nبرای ادامه وارد درگاه شوید.",
        reply_markup=markup,
    )


@router.callback_query(F.data.regexp(r"^cv:wallet:\d+$"))
async def wallet_topup(callback: CallbackQuery):
    amount = int(callback.data.split(":", 2)[2])
    await callback.answer()
    if not _bank_payment_available():
        await callback.message.answer("پرداخت بانکی فقط پس از تنظیم Merchant ID و نشانی عمومی HTTPS فعال می‌شود.")
        return
    async with session_scope() as session:
        user = await ensure_user(session, callback.from_user)
        if getattr(user, "status", "ACTIVE") != "ACTIVE":
            await callback.message.answer("حساب کاربری شما فعال نیست.")
            return
        order, _attempt, token = await create_winapay_wallet_order(session, user_id=user.id, amount_toman=amount)
        await session.commit()
    url = f"{settings.public_base_url}/payments/winapay/start/{token}"
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 ورود به درگاه", url=url)],
        [InlineKeyboardButton(text="↩️ کیف پول", callback_data="menu:wallet")],
    ])
    await callback.message.edit_text(
        f"<b>💰 شارژ کیف پول</b>\n\nمبلغ: <b>{_toman(amount)}</b>\n\nبرای پرداخت روی دکمه زیر بزنید.",
        reply_markup=markup,
    )


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    try:
        payload = json.loads(query.invoice_payload)
        order_id = UUID(payload["order_id"])
    except (ValueError, KeyError, json.JSONDecodeError, TypeError):
        await query.answer(ok=False, error_message="فاکتور نامعتبر است.")
        return

    async with session_scope() as session:
        current_user = await ensure_user(session, query.from_user)
        order = await session.get(Order, order_id)
        if order is None or order.status != "CREATED":
            await query.answer(ok=False, error_message="سفارش معتبر نیست یا قبلاً پردازش شده است.")
            return
        if order.user_id != current_user.id:
            await query.answer(ok=False, error_message="این فاکتور برای حساب دیگری صادر شده است.")
            return
        if query.currency != "XTR":
            await query.answer(ok=False, error_message="واحد پرداخت نامعتبر است.")
            return
        attempt = await session.scalar(select(PaymentAttempt).where(PaymentAttempt.order_id == order.id, PaymentAttempt.provider == "TELEGRAM_STARS").order_by(PaymentAttempt.created_at.desc()))
        if attempt is None:
            await query.answer(ok=False, error_message="تلاش پرداخت پیدا نشد.")
            return
        expected = int((order.extra_data or {}).get("telegram_stars") or int(attempt.requested_amount_irr))
        if int(query.total_amount) != expected:
            await query.answer(ok=False, error_message="مبلغ فاکتور با سفارش مطابقت ندارد.")
            return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message):
    payment = message.successful_payment
    try:
        payload = json.loads(payment.invoice_payload)
        order_id = UUID(payload["order_id"])
    except (ValueError, KeyError, json.JSONDecodeError, TypeError):
        await message.answer("پرداخت دریافت شد اما Payload معتبر نیست. پشتیبانی را در جریان بگذارید.")
        return

    async with session_scope() as session:
        current_user = await ensure_user(session, message.from_user)
        order = await session.get(Order, order_id)
        if order is None:
            await message.answer("سفارش پیدا نشد. پشتیبانی را در جریان بگذارید.")
            return
        if order.user_id != current_user.id:
            await message.answer("شناسه پرداخت با سفارش مطابقت ندارد. پشتیبانی را در جریان بگذارید.")
            return
        if payment.currency != "XTR":
            await message.answer("واحد پرداخت نامعتبر است. پشتیبانی را در جریان بگذارید.")
            return
        attempt = await session.scalar(select(PaymentAttempt).where(PaymentAttempt.order_id == order.id, PaymentAttempt.provider == "TELEGRAM_STARS").order_by(PaymentAttempt.created_at.desc()))
        if attempt is None:
            await message.answer("تلاش پرداخت پیدا نشد. پشتیبانی را در جریان بگذارید.")
            return
        expected = int((order.extra_data or {}).get("telegram_stars") or int(attempt.requested_amount_irr))
        if int(payment.total_amount) != expected:
            await message.answer("مبلغ پرداخت‌شده با سفارش مطابقت ندارد. پشتیبانی را در جریان بگذارید.")
            return
        if order.status == "PAID":
            await message.answer("این پرداخت قبلاً ثبت شده است.")
            return
        await settle_star_payment(session, order=order, attempt=attempt, successful_payment=payment)

    await message.answer(
        "<b>✅ پرداخت با موفقیت ثبت شد</b>\n\nاشتراک شما فعال شد. تاریخ پایان را می‌توانید از «حساب کاربری» ببینید."
    )


@router.message(F.text == "/paysupport")
async def paysupport(message: Message):
    await message.answer(
        "<b>💬 پشتیبانی پرداخت</b>\n\n"
        "در صورت بروز مشکل، شماره سفارش و زمان پرداخت را برای پشتیبانی ارسال کنید."
    )
