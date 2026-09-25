from __future__ import annotations

from uuid import UUID

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.core.config import settings
from app.runtime.db import session_scope
from app.services.ads import (
    ad_request_counts,
    create_ad_request,
    get_ad_settings,
    get_or_create_ad_settings,
    set_ad_request_status,
    super_admin_telegram_ids,
)
from app.services.membership import is_admin_user
from app.services.user_account import ensure_user

router = Router(name="ads")


class AdRequestState(StatesGroup):
    waiting_content = State()


def _ads_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📨 ارسال محتوای تبلیغ", callback_data="cv:ad:send")],
            [InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")],
        ]
    )


def _channel_target(row) -> str | None:
    if row is None:
        return None
    if row.channel_username:
        return f"@{row.channel_username}"
    if row.channel_chat_id:
        return str(row.channel_chat_id)
    return None


@router.callback_query(F.data == "menu:ads")
async def ads_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    async with session_scope() as session:
        row = await get_ad_settings(session)
        active = bool(row and row.active)
        counts = await ad_request_counts(session)
    if not active:
        await callback.message.edit_text(
            "<b>📣 تبلیغات</b>\n\nبخش تبلیغات در حال حاضر غیرفعال است.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")]]),
        )
        return

    lines = ["<b>📣 تبلیغات در فمونا سنس</b>", ""]
    if row and (row.rates_text or "").strip():
        lines += ["💰 <b>تعرفه‌ها:</b>", f"<i>{row.rates_text.strip()}</i>", ""]
    if row and (row.instructions_text or "").strip():
        lines += ["📋 <b>راهنما:</b>", f"<i>{row.instructions_text.strip()}</i>", ""]
    if row and (row.contact_text or "").strip():
        lines += ["📞 <b>ارتباط با ما:</b>", f"<i>{row.contact_text.strip()}</i>", ""]
    if row and _channel_target(row):
        lines.append(f"📢 کانال تبلیغات: {_channel_target(row)}")
        lines.append("")
    lines.append("می‌توانید پست یا محتوای تبلیغ خود را ارسال کنید تا پس از تأیید، در کانال تبلیغات منتشر شود.")
    await callback.message.edit_text("\n".join(lines), reply_markup=_ads_back_keyboard())


@router.callback_query(F.data == "cv:ad:send")
async def ads_send_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(AdRequestState.waiting_content)
    await callback.message.edit_text(
        "<b>📨 ارسال محتوای تبلیغ</b>\n\n"
        "محتوای تبلیغ خود را در همین چت ارسال کنید:\n"
        "• متن آگهی\n"
        "• عکس یا ویدئو همراه با توضیحات (کپشن)\n\n"
        "پس از بررسی ادمین، محتوای شما در کانال تبلیغات منتشر می‌شود.\n\n"
        "برای انصراف دکمه پایین را بزنید.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ انصراف", callback_data="cv:ad:cancel")],
        ]),
    )


@router.callback_query(F.data == "cv:ad:cancel")
async def ads_send_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer("انصراف ثبت شد.")
    await state.clear()
    await callback.message.edit_text("ارسال تبلیغ لغو شد.", reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📣 بازگشت به تبلیغات", callback_data="menu:ads")]]
    ))


@router.message(AdRequestState.waiting_content, F.text | F.photo | F.video | F.animation | F.document)
async def ads_receive_content(message: Message, state: FSMContext):
    content_type = "TEXT"
    file_id = None
    if message.photo:
        content_type, file_id = "PHOTO", message.photo[-1].file_id
    elif message.video:
        content_type, file_id = "VIDEO", message.video.file_id
    elif message.animation:
        content_type, file_id = "ANIMATION", message.animation.file_id
    elif message.document:
        content_type, file_id = "DOCUMENT", message.document.file_id
    text_body = message.text or message.caption

    async with session_scope() as session:
        user = await ensure_user(session, message.from_user)
        request = await create_ad_request(
            session,
            user_id=user.id,
            content_type=content_type,
            content_text=text_body,
            telegram_file_id=file_id,
            source_chat_id=message.chat.id,
            source_message_id=message.message_id,
        )
        admin_ids = await super_admin_telegram_ids(session)
    await state.clear()

    await message.answer(
        "✅ درخواست تبلیغ شما با موفقیت ثبت شد.\n\n"
        "کارشناسان ما آن را بررسی می‌کنند و نتیجه از طریق همین ربات اطلاع داده می‌شود.\n"
        f"شماره پیگیری: <code>{str(request.id)[:8]}</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")],
        ]),
    )

    # اطلاع‌رسانی به ادمین‌ها
    from_user = message.from_user
    who = "@".join(filter(None, [from_user.username])) or from_user.first_name or str(from_user.id)
    admin_text = (
        "<b>📣 درخواست تبلیغ جدید</b>\n\n"
        f"👤 کاربر: {who} (<code>{from_user.id}</code>)\n"
        f"🔖 نوع: {content_type}\n"
        f"🆔 درخواست: <code>{request.id}</code>"
    )
    notify_ids = set(admin_ids)
    if settings.admin_user_id:
        notify_ids.add(settings.admin_user_id)
    for admin_id in notify_ids:
        try:
            await _notify_admin(message.bot, admin_id, admin_text, request)
        except Exception:
            continue


async def _notify_admin(bot, admin_id: int, admin_text: str, request) -> None:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ تأیید و انتشار", callback_data=f"cv:ad:ok:{request.id}"),
            InlineKeyboardButton(text="❌ رد", callback_data=f"cv:ad:no:{request.id}"),
        ],
        [InlineKeyboardButton(text="📤 انتشار در کانال", callback_data=f"cv:ad:pub:{request.id}")],
    ])
    if request.content_type == "PHOTO" and request.telegram_file_id:
        await bot.send_photo(admin_id, request.telegram_file_id, caption=admin_text, reply_markup=keyboard)
    elif request.content_type == "VIDEO" and request.telegram_file_id:
        await bot.send_video(admin_id, request.telegram_file_id, caption=admin_text, reply_markup=keyboard)
    elif request.content_type == "ANIMATION" and request.telegram_file_id:
        await bot.send_animation(admin_id, request.telegram_file_id, caption=admin_text, reply_markup=keyboard)
    elif request.content_type == "DOCUMENT" and request.telegram_file_id:
        await bot.send_document(admin_id, request.telegram_file_id, caption=admin_text, reply_markup=keyboard)
    else:
        await bot.send_message(admin_id, admin_text, reply_markup=keyboard)


# ---------- اکشن‌های ادمین ----------

async def _require_admin(callback: CallbackQuery) -> bool:
    async with session_scope() as session:
        return await is_admin_user(session, callback.from_user.id)


@router.callback_query(F.data.regexp(r"^cv:ad:ok:.+$"))
async def ads_approve(callback: CallbackQuery):
    if not await _require_admin(callback):
        await callback.answer("این عملیات فقط برای ادمین مجاز است.", show_alert=True)
        return
    request_id = UUID(callback.data.split(":", 2)[2])
    async with session_scope() as session:
        row = await set_ad_request_status(
            session, request_id, status="APPROVED", admin_note=None,
            reviewer_user_id=await _reviewer_id(session, callback.from_user.id),
        )
        setting = await get_ad_settings(session)
        target = _channel_target(setting)
        auto = bool(setting and setting.auto_channel_publish)
    if row is None:
        await callback.answer("درخواست پیدا نشد.", show_alert=True)
        return
    published = False
    if auto and target:
        published = await _publish_to_channel(callback.bot, target, request_id)
    await callback.answer("✅ درخواست تأیید شد." + (" و در کانال منتشر شد." if published else ""))
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="📤 انتشار در کانال", callback_data=f"cv:ad:pub:{request.id}")]]
            ))
        except Exception:
            pass


@router.callback_query(F.data.regexp(r"^cv:ad:no:.+$"))
async def ads_reject(callback: CallbackQuery):
    if not await _require_admin(callback):
        await callback.answer("این عملیات فقط برای ادمین مجاز است.", show_alert=True)
        return
    request_id = UUID(callback.data.split(":", 2)[2])
    async with session_scope() as session:
        row = await set_ad_request_status(
            session, request_id, status="REJECTED", admin_note=None,
            reviewer_user_id=await _reviewer_id(session, callback.from_user.id),
        )
    if row is None:
        await callback.answer("درخواست پیدا نشد.", show_alert=True)
        return
    await callback.answer("❌ درخواست رد شد.")
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass


@router.callback_query(F.data.regexp(r"^cv:ad:pub:.+$"))
async def ads_publish(callback: CallbackQuery):
    if not await _require_admin(callback):
        await callback.answer("این عملیات فقط برای ادمین مجاز است.", show_alert=True)
        return
    request_id = UUID(callback.data.split(":", 2)[2])
    async with session_scope() as session:
        row = await get_ad_request(session, request_id)
        target = _channel_target(await get_or_create_ad_settings(session))
    if row is None or row.status == "REJECTED":
        await callback.answer("این درخواست قابل انتشار نیست.", show_alert=True)
        return
    if not target:
        await callback.answer("ابتدا کانال تبلیغات را در پنل ادمین تنظیم کنید.", show_alert=True)
        return
    if await _publish_to_channel(callback.bot, target, request_id):
        await callback.answer("📤 در کانال تبلیغات منتشر شد.")
    else:
        await callback.answer("انتشار انجام نشد؛ دسترسی ربات به کانال را بررسی کنید.", show_alert=True)


async def _reviewer_id(session, telegram_user_id: int):
    from sqlalchemy import select as _select

    from app.db.models import User as _User

    return await session.scalar(_select(_User.id).where(_User.telegram_user_id == telegram_user_id))


async def _publish_to_channel(bot, target: str, request_id) -> bool:
    """انتشار درخواست تأییدشده در کانال تبلیغات؛ کپی از پیام اصلی با fallback به file_id."""
    from datetime import datetime, timezone

    from app.db.models import AdRequest

    async with session_scope() as session:
        row = await session.get(AdRequest, request_id)
        if row is None:
            return False
        content_type = row.content_type
        file_id = row.telegram_file_id
        source_chat, source_message = row.source_chat_id, row.source_message_id
        content_text = row.content_text or "آگهی تبلیغاتی"

    sent = None
    if source_chat and source_message:
        try:
            sent = await bot.copy_message(chat_id=target, from_chat_id=source_chat, message_id=source_message)
        except Exception:
            sent = None
    if sent is None and file_id:
        try:
            if content_type == "PHOTO":
                sent = await bot.send_photo(target, file_id)
            elif content_type == "VIDEO":
                sent = await bot.send_video(target, file_id)
            elif content_type == "ANIMATION":
                sent = await bot.send_animation(target, file_id)
            elif content_type == "DOCUMENT":
                sent = await bot.send_document(target, file_id)
        except Exception:
            sent = None
    if sent is None and content_type == "TEXT":
        try:
            sent = await bot.send_message(target, content_text)
        except Exception:
            sent = None
    if sent is None:
        return False
    async with session_scope() as session:
        row = await session.get(AdRequest, request_id)
        if row is not None:
            row.status = "PUBLISHED"
            row.published_message_id = sent.message_id
            row.reviewed_at = row.reviewed_at or datetime.now(timezone.utc)
    return True
