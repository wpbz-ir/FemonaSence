from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from app.bot.keyboards.main_menu import back_home_keyboard
from app.db.models import Favorite, Subscription, Title, Wallet, WatchHistory
from app.runtime.db import session_scope
from app.services.catalog import title_text
from app.services.user_account import ensure_user
from app.utils.telegram_ui import edit_or_send

# [P2] نمایش تاریخ انقضا در منطقه‌ی زمانی تهران — همان الگوی releases.py:
# ایران DST ندارد → Asia/Tehran همیشه UTC+03:30 ثابت است؛ اگر tzdb در دسترس
# نباشد (مثلاً ویندوز بدون بسته‌ی tzdata)، همان آفست ثابت ساخته می‌شود.
try:
    TEHRAN_TZ = ZoneInfo("Asia/Tehran")
except ZoneInfoNotFoundError:  # pragma: no cover - وابسته به محیط استقرار
    TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))


async def send_account(callback: CallbackQuery):
    await callback.answer()
    async with session_scope() as session:
        user = await ensure_user(session, callback.from_user)
        wallet = await session.scalar(select(Wallet).where(Wallet.user_id == user.id))
        sub = await session.scalar(
            select(Subscription)
            .where(
                Subscription.user_id == user.id,
                Subscription.status == "ACTIVE",
                # [P2] رکورد ACTIVE با expires_at گذشته، اشتراک معتبر نیست؛
                # وگرنه «فعال تا <تاریخ گذشته>» برای اشتراک منقضی نشان داده می‌شد.
                Subscription.expires_at >= datetime.now(timezone.utc),
            )
            .order_by(Subscription.expires_at.desc())
        )

    if sub:
        expires_fa = escape(
            sub.expires_at.astimezone(TEHRAN_TZ).strftime("%Y-%m-%d %H:%M"),
            quote=False,
        )
        subscription_text = f"فعال تا <code>{expires_fa}</code>"
    else:
        subscription_text = "فعال نیست"

    await edit_or_send(
        callback,
        "<b>👤 حساب کاربری</b>\n\n"
        f"🆔 شناسه: <code>{callback.from_user.id}</code>\n"
        f"💰 کیف پول: <b>{wallet.balance_irr if wallet else 0}</b>\n"
        f"💎 اشتراک: {subscription_text}",
        reply_markup=back_home_keyboard(),
    )


async def send_wallet(callback: CallbackQuery):
    await callback.answer()
    async with session_scope() as session:
        user = await ensure_user(session, callback.from_user)
        wallet = await session.scalar(select(Wallet).where(Wallet.user_id == user.id))

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 شارژ 100,000 تومان", callback_data="cv:wallet:100000")],
        [InlineKeyboardButton(text="💳 شارژ 250,000 تومان", callback_data="cv:wallet:250000")],
        [InlineKeyboardButton(text="💳 شارژ 500,000 تومان", callback_data="cv:wallet:500000")],
        [InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")],
    ])
    await edit_or_send(
        callback,
        "<b>💰 کیف پول</b>\n\n"
        f"موجودی فعلی: <b>{wallet.balance_irr if wallet else 0}</b> ریال\n\n"
        "شارژ کیف پول از طریق درگاه بانکی انجام می‌شود.",
        reply_markup=markup,
    )


async def send_favorites(callback: CallbackQuery):
    await callback.answer()
    async with session_scope() as session:
        user = await ensure_user(session, callback.from_user)
        titles = list((await session.scalars(
            select(Title)
            .join(Favorite, Favorite.title_id == Title.id)
            .where(
                Favorite.user_id == user.id,
                Title.status.in_(("PUBLISHED", "ACTIVE", "PUBLIC")),
            )
            .order_by(Favorite.created_at.desc())
            .limit(30)
        )).all())

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🎬 {title_text(t)[:60]}", callback_data=f"cv:title:{t.id}")]
            for t in titles
        ] + [[InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")]]
    )
    await edit_or_send(
        callback,
        "<b>❤️ لیست من</b>\n\n"
        + ("موردی ذخیره نشده است." if not titles else "عنوان موردنظر را انتخاب کنید:"),
        reply_markup=markup,
    )


async def send_history(callback: CallbackQuery):
    await callback.answer()
    async with session_scope() as session:
        user = await ensure_user(session, callback.from_user)
        titles = list((await session.scalars(
            select(Title)
            .join(WatchHistory, WatchHistory.title_id == Title.id)
            .where(
                WatchHistory.user_id == user.id,
                Title.status.in_(("PUBLISHED", "ACTIVE", "PUBLIC")),
            )
            .order_by(WatchHistory.last_watched_at.desc())
            .limit(30)
        )).all())

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🎬 {title_text(t)[:60]}", callback_data=f"cv:title:{t.id}")]
            for t in titles
        ] + [[InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")]]
    )
    await edit_or_send(
        callback,
        "<b>🕘 تاریخچه</b>\n\n"
        + ("تاریخچه‌ای ثبت نشده است." if not titles else "عنوان موردنظر را انتخاب کنید:"),
        reply_markup=markup,
    )
