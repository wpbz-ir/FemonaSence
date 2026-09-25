from __future__ import annotations

from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from app.bot.keyboards.main_menu import back_home_keyboard
from app.db.models import Favorite, Subscription, Title, Wallet, WatchHistory
from app.runtime.db import session_scope
from app.services.catalog import title_text
from app.services.user_account import ensure_user


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
            )
            .order_by(Subscription.expires_at.desc())
        )

    if sub:
        subscription_text = f"فعال تا <code>{sub.expires_at}</code>"
    else:
        subscription_text = "فعال نیست"

    await callback.message.edit_text(
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
    await callback.message.edit_text(
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
    await callback.message.edit_text(
        "<b>❤️ لیست من</b>\n\n"
        + ("موردی ذخیره نشده است." if not titles else "عنوان موردنظر را انتخاب کنید:"),
        reply_markup=markup,
    )


async def send_continue(callback: CallbackQuery):
    """منوی «ادامه تماشا» — عناوین نیمه‌تمام با موقعیت دقیق ذخیره‌شده."""
    await callback.answer()
    from app.services.watch_progress import list_watch_progress, progress_label

    async with session_scope() as session:
        user = await ensure_user(session, callback.from_user)
        items = await list_watch_progress(session, user_id=user.id, limit=20)

    buttons = []
    for item in items:
        name = item.get("title_fa") or item.get("title_en") or item.get("original_title") or "عنوان"
        label = progress_label(item) or "ادامه تماشا"
        buttons.append([
            InlineKeyboardButton(
                text=f"⏯️ {name[:40]} — {label}"[:64],
                callback_data=f"cv:continue:{item['title_id']}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")])
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(
        "<b>⏯️ ادامه تماشا</b>\n\n"
        + ("پخش از همان‌جایی که قطع کرده بودید ادامه پیدا می‌کند:" if items else "فعلاً پخش نیمه‌تمامی ثبت نشده است. یک عنوان را پخش کنید تا اینجا دیده شود."),
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
    await callback.message.edit_text(
        "<b>🕘 تاریخچه</b>\n\n"
        + ("تاریخچه‌ای ثبت نشده است." if not titles else "عنوان موردنظر را انتخاب کنید:"),
        reply_markup=markup,
    )
