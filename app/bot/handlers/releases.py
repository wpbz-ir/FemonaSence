from __future__ import annotations

from uuid import UUID

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from app.runtime.db import session_scope
from app.services.access import can_access_release
from app.services.experience import create_media_access_token, create_watch_party, public_media_url, public_watch_url
from app.services.release_matrix import delivery_target, get_release_variant, list_release_variants, release_badges, release_label, stream_source
from app.services.user_account import ensure_user
from app.services.watch_progress import get_watch_progress
from app.utils.telegram_ui import edit_or_send as _edit_or_send
from app.db.models import Title


router = Router(name="releases")


def _back_title(title_id) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="🔙 برگشت", callback_data=f"cv:title:{title_id}")]


def _content_title_id(row: dict):
    return row.get("content_title_id") or row.get("title_id")


def _release_button(row: dict, *, mode: str) -> InlineKeyboardButton:
    badges = " · ".join(release_badges(row))
    prefix = "⬇️" if mode == "download" else "▶️"
    suffix = f" · {badges}" if badges else ""
    return InlineKeyboardButton(text=f"{prefix} {release_label(row)}{suffix}"[:64], callback_data=f"cv:{mode}:{row['id']}")


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
    buttons = [[_release_button(row, mode="download")] for row in rows if delivery_target(row)]
    if not buttons:
        buttons = [[InlineKeyboardButton(text="⏳ هنوز نسخه قابل دانلود ثبت نشده است.", callback_data="cv:no-op")]]
    buttons.append([InlineKeyboardButton(text="▶️ پخش آنلاین", callback_data=f"cv:playselect:{title_id}")])
    buttons.append(_back_title(title_id))
    await _edit_or_send(
        callback,
        f"<b>⬇️ نسخه‌های «{title.title_fa or title.title_en or title.original_title}»</b>\n\n"
        "فقط نسخه‌هایی که فایل واقعی در Storage دارند نمایش داده می‌شوند:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data == "cv:no-op")
async def no_op(callback: CallbackQuery):
    await callback.answer("هنوز نسخه آماده دانلودی ثبت نشده است.")


@router.callback_query(F.data.regexp(r"^cv:playselect:.+$"))
async def play_select(callback: CallbackQuery):
    try:
        title_id = UUID(callback.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await callback.answer("شناسه عنوان نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    async with session_scope() as session:
        rows = await list_release_variants(session, title_id=title_id)
    buttons = [[_release_button(row, mode="play")] for row in rows if stream_source(row) or delivery_target(row)]
    if not buttons:
        buttons = [[InlineKeyboardButton(text="⏳ پخش آنلاین هنوز برای این عنوان فعال نشده است.", callback_data="cv:no-op")]]
    buttons.append(_back_title(title_id))
    await _edit_or_send(
        callback,
        "<b>▶️ انتخاب نسخه برای پخش آنلاین</b>\n\nنسخه دارای منبع پخش را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


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
        target = delivery_target(row)
    if not target:
        await callback.message.answer("فایل این نسخه هنوز به Telegram Storage متصل نیست.")
        return
    try:
        await callback.bot.copy_message(chat_id=callback.from_user.id, from_chat_id=target[0], message_id=target[1])
    except Exception:
        await callback.message.answer("ارسال فایل انجام نشد. وضعیت Storage را بررسی کنید.")
        return
    await callback.message.answer("نسخه انتخابی برای شما ارسال شد. ✅")


async def _issue_stream_link(callback: CallbackQuery, *, release_id: UUID):
    async with session_scope() as session:
        row = await get_release_variant(session, release_id=release_id)
        if not row:
            return None, "نسخه پیدا نشد."
        if not (stream_source(row) or delivery_target(row)):
            return None, "برای این نسخه هنوز منبع پخش آنلاین ثبت نشده است."
        user = await ensure_user(session, callback.from_user)
        allowed, reason = await can_access_release(session, user.id, release_id)
        if not allowed:
            return None, reason
        token = await create_media_access_token(
            session,
            user_id=user.id,
            release_id=release_id,
            action="STREAM",
        )
        progress = await get_watch_progress(session, user_id=user.id, title_id=_content_title_id(row))
    url = public_media_url(token)
    if not url:
        return None, "برای پخش آنلاین، PUBLIC_BASE_URL هنوز روی سرور تنظیم نشده است."
    return {"row": row, "url": url, "progress": progress}, None


@router.callback_query(F.data.regexp(r"^cv:play:.+$"))
async def play_release(callback: CallbackQuery):
    try:
        release_id = UUID(callback.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await callback.answer("شناسه نسخه نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    data, error = await _issue_stream_link(callback, release_id=release_id)
    if error:
        await callback.message.answer(error)
        return
    row = data["row"]
    await _edit_or_send(
        callback,
        "<b>▶️ پخش آنلاین آماده است</b>\n\n"
        "🔐 لینک دسترسی زمان‌دار است.\n"
        f"🎬 {release_label(row)}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="▶️ باز کردن پخش آنلاین", url=data["url"])],
                [InlineKeyboardButton(text="🔄 انتخاب نسخه دیگر", callback_data=f"cv:playselect:{_content_title_id(row)}")],
                _back_title(_content_title_id(row)),
            ]
        ),
    )


@router.callback_query(F.data.regexp(r"^cv:continue:.+$"))
async def continue_watching(callback: CallbackQuery):
    try:
        title_id = UUID(callback.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await callback.answer("شناسه عنوان نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    async with session_scope() as session:
        user = await ensure_user(session, callback.from_user)
        progress = await get_watch_progress(session, user_id=user.id, title_id=title_id)
    release_id = progress.get("release_id") if progress else None
    if not release_id:
        await callback.message.answer("نسخه قبلی برای ادامه تماشا در دسترس نیست.")
        return
    try:
        result = await _issue_stream_link(callback, release_id=UUID(str(release_id)))
    except (ValueError, TypeError):
        await callback.message.answer("نسخه قبلی برای ادامه تماشا نامعتبر است.")
        return
    data, error = result
    if error:
        await callback.message.answer(error)
        return
    row = data["row"]
    await _edit_or_send(
        callback,
        "<b>⏯️ ادامه تماشا</b>\n\n"
        "پخش از آخرین موقعیت ذخیره‌شده شما ادامه پیدا می‌کند.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="▶️ ادامه پخش", url=data["url"])],
            [InlineKeyboardButton(text="🔙 برگشت", callback_data=f"cv:title:{row['title_id']}")],
        ]),
    )


@router.callback_query(F.data.regexp(r"^cv:party:.+$"))
async def create_party(callback: CallbackQuery):
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
        if not (stream_source(row) or delivery_target(row)):
            await callback.message.answer("برای این نسخه هنوز منبع پخش آنلاین ثبت نشده است.")
            return
        user = await ensure_user(session, callback.from_user)
        allowed, reason = await can_access_release(session, user.id, release_id)
        if not allowed:
            await callback.message.answer(reason)
            return
        token = await create_watch_party(
            session,
            host_user_id=user.id,
            title_id=_content_title_id(row),
            release_id=release_id,
        )
    url = public_watch_url(token)
    if not url:
        await callback.message.answer("برای تماشای گروهی، PUBLIC_BASE_URL هنوز تنظیم نشده است.")
        return
    await _edit_or_send(
        callback,
        "<b>👥 اتاق تماشای گروهی ساخته شد</b>\n\n"
        "لینک را برای دوستانتان بفرستید. موقعیت پخش و توقف/شروع بین اعضای اتاق همگام می‌شود.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="▶️ ورود به اتاق تماشا", url=url)],
            [InlineKeyboardButton(text="📤 اشتراک‌گذاری لینک اتاق", url=url)],
            _back_title(_content_title_id(row)),
        ]),
    )
