from __future__ import annotations

from uuid import UUID

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from app.core.config import settings
from app.runtime.db import session_scope
from app.services.access import can_access_release
from app.services.growth import daily_download_count, record_download
from app.services.release_matrix import delivery_target, get_release_variant, list_release_variants, release_badges, release_label
from app.services.rbac import is_super_admin
from app.services.user_account import ensure_user
from app.utils.telegram_ui import edit_or_send as _edit_or_send
from app.db.models import Subscription, Title


router = Router(name="releases")


def _back_title(title_id) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="🔙 برگشت", callback_data=f"cv:title:{title_id}")]


def _release_button(row: dict) -> InlineKeyboardButton:
    badges = " · ".join(release_badges(row))
    suffix = f" · {badges}" if badges else ""
    return InlineKeyboardButton(text=f"⬇️ {release_label(row)}{suffix}"[:64], callback_data=f"cv:download:{row['id']}")


@router.callback_query(F.data.regexp(r"^cv:releases:.+$"))
async def release_list(callback: CallbackQuery):
    try:
        title_id = UUID(callback.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await callback.answer("شناسه عنوان نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    async with session_scope() as session:
        title = await session.get(Title, title_id)
        if title and str(getattr(title, "status", "")).upper() in {"PUBLISHED", "ACTIVE", "PUBLIC"}:
            rows = await list_release_variants(session, title_id=title_id)
        else:
            rows = []
    if not title:
        await _edit_or_send(callback, "عنوان پیدا نشد.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_title(title_id)]))
        return
    if str(getattr(title, "status", "")).upper() not in {"PUBLISHED", "ACTIVE", "PUBLIC"}:
        await _edit_or_send(callback, "این عنوان در حال حاضر منتشر نشده است.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_title(title_id)]))
        return
    buttons = [[_release_button(row)] for row in rows if delivery_target(row)]
    if not buttons:
        buttons = [[InlineKeyboardButton(text="⏳ هنوز نسخه قابل دانلود ثبت نشده است.", callback_data="cv:no-op")]]
    buttons.append(_back_title(title_id))
    await _edit_or_send(
        callback,
        f"<b>⬇️ نسخه‌های «{title.title_fa or title.title_en or title.original_title}»</b>\n\n"
        "نسخه موردنظر را انتخاب کنید؛ فایل مستقیماً از تلگرام برای شما ارسال می‌شود:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data == "cv:no-op")
async def no_op(callback: CallbackQuery):
    await callback.answer("هنوز نسخه آماده‌ای ثبت نشده است.")


@router.callback_query(F.data.regexp(r"^cv:download:.+$"))
async def download_release(callback: CallbackQuery):
    try:
        release_id = UUID(callback.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await callback.answer("شناسه نسخه نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    async with session_scope() as session:
        row = await get_release_variant(session, release_id=release_id)
        if not row:
            await callback.message.answer("نسخه پیدا نشد.")
            return
        user = await ensure_user(session, callback.from_user)
        allowed, reason = await can_access_release(session, user.id, release_id)
        if not allowed:
            await callback.message.answer(reason)
            return
        # 🛡 سهمیه دانلود روزانه برای کاربران بدون اشتراک (ادمین و مشترک = نامحدود)
        limit = settings.free_daily_download_limit
        if limit and limit > 0 and not await is_super_admin(session, user.id):
            sub = await session.scalar(
                select(Subscription).where(
                    Subscription.user_id == user.id,
                    Subscription.status == "ACTIVE",
                )
            )
            if not sub:
                used = await daily_download_count(session, user_id=user.id)
                if used >= limit:
                    await callback.message.answer(
                        f"⛔ سهمیه دانلود رایگان امروز شما ({limit} مورد) به پایان رسیده است.\n"
                        "💎 با تهیه اشتراک، دانلود نامحدود خواهید داشت.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="💎 خرید اشتراک", callback_data="menu:subscription")],
                            [InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")],
                        ]),
                    )
                    return
        target = delivery_target(row)
    if not target:
        await callback.message.answer("فایل این نسخه هنوز به Telegram Storage متصل نیست.")
        return
    try:
        await callback.bot.copy_message(chat_id=callback.from_user.id, from_chat_id=target[0], message_id=target[1])
    except Exception:
        await callback.message.answer("ارسال فایل انجام نشد. وضعیت Storage را بررسی کنید.")
        return
    # ثبت آمار دانلود (پایه «پربازدیدها» و گزارش‌ها)
    async with session_scope() as session:
        await record_download(session, user_id=user.id, release_id=release_id)
    await callback.message.answer("نسخه انتخابی برای شما ارسال شد. ✅")
    # ⭐ امتیازدهی کیفیت
    await callback.message.answer(
        "نظرتان درباره کیفیت این نسخه چیست؟",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="👍 خوب بود", callback_data=f"cv:rate:{release_id}:1"),
            InlineKeyboardButton(text="👎 مشکل داشت", callback_data=f"cv:rate:{release_id}:0"),
        ]]),
    )
