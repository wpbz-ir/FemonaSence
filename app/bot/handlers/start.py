from __future__ import annotations

from uuid import UUID

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.keyboards.main_menu import main_menu_keyboard
from app.core.brand import WELCOME_TEXT
from app.db.models import Title
from app.runtime.db import session_scope
from app.services.user_account import ensure_user


router = Router(name="start")


HELP_TEXT = (
    "<b>📖 راهنما</b>\n\n"
    "🎬 از «منوی اصلی» فیلم، سریال و انیمیشن را ببینید.\n"
    "🔎 با «جستجوی حرفه‌ای» نام عنوان را بفرستید.\n"
    "⬇️ در صفحه هر عنوان، نسخه‌های دانلودی را مستقیم دریافت کنید.\n"
    "❤️ عناوین موردعلاقه را در «لیست من» ذخیره کنید.\n\n"
    "دستورها:\n"
    "/start — نمایش منوی اصلی\n"
    "/subscription — وضعیت اشتراک\n"
    "/paysupport — پشتیبانی پرداخت"
)


@router.message(Command("help"))
async def help_handler(message: Message):
    await message.answer(HELP_TEXT, reply_markup=main_menu_keyboard())


# نکته: /subscription و /paysupport در payments.py هندل می‌شوند — تکرار ممنوع.


@router.message(CommandStart())
async def start_handler(message: Message):
    payload = ""
    if message.text:
        parts = message.text.split(maxsplit=1)
        if len(parts) == 2:
            payload = parts[1].strip()

    async with session_scope() as session:
        user = await ensure_user(session, message.from_user)
        shared_title = None
        if payload.startswith("title_"):
            try:
                candidate = await session.get(Title, UUID(payload.removeprefix("title_")))
                if candidate and str(getattr(candidate, "status", "")).upper() in {"PUBLISHED", "ACTIVE", "PUBLIC"}:
                    shared_title = candidate
            except ValueError:
                shared_title = None

    if shared_title:
        name = shared_title.title_fa or shared_title.title_en or shared_title.original_title or "عنوان"
        buttons = [[InlineKeyboardButton(text="🎬 مشاهده صفحه عنوان", callback_data=f"cv:title:{shared_title.id}")], [InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")]]
        if getattr(shared_title, "poster_url", None):
            try:
                await message.answer_photo(
                    shared_title.poster_url,
                    caption=f"<b>🎬 {name}</b>\n\nاین عنوان با شما به اشتراک گذاشته شده است.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                )
                return
            except Exception:
                pass
        await message.answer(
            f"<b>🎬 {name}</b>\n\nاین عنوان با شما به اشتراک گذاشته شده است.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
        return

    await message.answer(
        f"{WELCOME_TEXT}\n\n"
        "فیلم، سریال و انیمیشن را با نسخه‌های مختلف پیدا کنید.\n"
        "نسخه دانلودی هر عنوان مستقیماً در چت تلگرام برای شما ارسال می‌شود و امکانات اجتماعی در یک صفحه در دسترس است.",
        reply_markup=main_menu_keyboard(),
    )
