from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.runtime.db import session_scope
from app.services.membership import (
    check_membership,
    gate_enabled,
    invalidate_cache,
    is_admin_user,
    load_gate_config,
)
from app.services.user_account import ensure_user
from app.utils.telegram_ui import edit_or_send

# این روتر باید «اول از همه» در create_bot ثبت شود تا قبل از بقیه هندلرها
# دروازه عضویت را اعمال کند. اگر کاربر عضو بود، SkipHandler اجرا شده و
# رخداد به روترهای بعدی (کاتالوگ، پرداخت و ...) می‌رسد.

router = Router(name="membership")

VERIFY_CALLBACK = "cv:mship:check"


def _gate_keyboard(missing) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for channel in missing:
        label = channel.title or (f"@{channel.username}" if channel.username else str(channel.telegram_chat_id))
        url = channel.invite_url or (
            f"https://t.me/{channel.username}" if channel.username else None
        )
        if url:
            rows.append([InlineKeyboardButton(text=f"🔗 عضویت در {label}"[:64], url=url)])
        # [P2] بدون username/invite_url هیچ لینکِ درستی قابل ساختن نیست؛
        # fallback قبلی (t.me/c/…) برای شناسه‌های غیر -100 پیوند خراب می‌ساخت —
        # به‌جای لینک شکسته، دکمه اصلاً ساخته نمی‌شود (بررسی عضویت همچنان هست).
    rows.append([InlineKeyboardButton(text="✅ بررسی عضویت", callback_data=VERIFY_CALLBACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _gate_text(missing) -> str:
    lines = ["🔐 <b>ورود به فمونا سنس</b>", "", "برای استفاده از امکانات ربات، ابتدا در کانال‌های زیر عضو شوید:", ""]
    for index, channel in enumerate(missing, start=1):
        label = channel.title or (f"@{channel.username}" if channel.username else "کانال")
        # [5-INT-b / 5-G15-e R5] عنوان کانال ادمی-کنترل است و sanitize نمی‌شود؛
        # بدون escape، یک < > & در عنوان، پیامِ دروازه‌ی همه‌ی کاربران را می‌شکند.
        lines.append(f"{index}️⃣ <b>{html.escape(label, quote=False)}</b>")
    lines += ["", "پس از عضویت، روی دکمه «✅ بررسی عضویت» بزنید."]
    return "\n".join(lines)


async def _present_gate(event: Message | CallbackQuery, missing) -> None:
    text = _gate_text(missing)
    keyboard = _gate_keyboard(missing)
    if isinstance(event, CallbackQuery):
        if event.message:
            try:
                await event.message.edit_text(text, reply_markup=keyboard)
            except Exception:
                await event.message.answer(text, reply_markup=keyboard)
        await event.answer("ابتدا در کانال‌های مشخص‌شده عضو شوید.", show_alert=True)
    else:
        await event.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == VERIFY_CALLBACK)
async def verify_membership(callback: CallbackQuery):
    """دکمه «بررسی عضویت» — بدون کش، بررسی تازه انجام می‌شود."""
    invalidate_cache(callback.from_user.id)
    async with session_scope() as session:
        await ensure_user(session, callback.from_user)
        admin = await is_admin_user(session, callback.from_user.id)
        enabled = await gate_enabled(session)
        if admin or not enabled:
            joined, missing = True, []
        else:
            joined, missing = await check_membership(
                callback.bot, session, callback.from_user.id, force_refresh=True
            )
    if joined:
        from app.bot.keyboards.main_menu import main_menu_keyboard
        from app.core.brand import WELCOME_TEXT

        await callback.answer("✅ عضویت شما تأیید شد. خوش آمدید!", show_alert=True)
        if callback.message:
            # [P1] ویرایش مستقیم edit_text روی پیامِ عکسی می‌میرد؛ edit_or_send مسیر
            # edit_caption/fallback را هم پوشش می‌دهد.
            await edit_or_send(
                callback,
                f"{WELCOME_TEXT}\n\n✅ عضویت شما تأیید شد. منوی اصلی:",
                reply_markup=main_menu_keyboard(is_admin=admin),
            )
            return
        await callback.message.answer(
            f"{WELCOME_TEXT}\n\n✅ عضویت شما تأیید شد. منوی اصلی:",
            reply_markup=main_menu_keyboard(is_admin=admin),
        )
        return
    await _present_gate(callback, missing)


@router.message()
async def membership_gate_message(message: Message, **_kwargs):
    """دروازه برای همه پیام‌ها: عضو یا ادمین → ادامه به روترهای بعدی؛ وگرنه نمایش دروازه."""
    if message.chat and message.chat.type not in {"private"}:
        raise SkipHandler
    from_user = message.from_user
    if not from_user:
        raise SkipHandler
    # [P1] fast-path بدون هیچ کار DB: وقتی دروازه خاموش است (حالت پیش‌فرض)،
    # ensure_user + is_admin_user روی «هر» پیام یعنی ۲-۳ رفت‌وبرگشت Neon + کامیت
    # بیهوده. load_gate_config کش mtime دارد و بدون DB است؛ فقط وقتی روشن است
    # منطق قبلی (ensure_user/bypass/بررسی عضویت) اجرا می‌شود. /start و دیپ‌لینک‌ها
    # در روترهای بعدی به‌طور عادی پردازش می‌شوند.
    if not load_gate_config().get("enabled"):
        raise SkipHandler
    async with session_scope() as session:
        await ensure_user(session, from_user)
        admin = await is_admin_user(session, from_user.id)
        enabled = await gate_enabled(session)
        if admin or not enabled:
            raise SkipHandler
        joined, missing = await check_membership(message.bot, session, from_user.id)
    if joined:
        raise SkipHandler
    await _present_gate(message, missing)


@router.callback_query()
async def membership_gate_callback(callback: CallbackQuery, **_kwargs):
    """دروازه برای همه callbackها به‌جز دکمه بررسی عضویت (که بالاتر هندل شده است)."""
    from_user = callback.from_user
    if not from_user:
        raise SkipHandler
    # [P1] fast-path بدون DB — توضیح در membership_gate_message.
    if not load_gate_config().get("enabled"):
        raise SkipHandler
    async with session_scope() as session:
        await ensure_user(session, from_user)
        admin = await is_admin_user(session, from_user.id)
        enabled = await gate_enabled(session)
        if admin or not enabled:
            raise SkipHandler
        joined, missing = await check_membership(callback.bot, session, from_user.id)
    if joined:
        raise SkipHandler
    await _present_gate(callback, missing)
