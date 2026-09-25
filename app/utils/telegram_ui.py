"""ابزارهای مشترک UI تلگرام برای هندلرها."""
from __future__ import annotations

from aiogram.types import CallbackQuery


async def edit_or_send(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    """پیام را ویرایش می‌کند؛ اگر پیام عکسی باشد caption ویرایش می‌شود و در
    غیر این صورت پیام تازه برای کاربر ارسال می‌شود (جلوگیری از TelegramBadRequest)."""
    message = callback.message
    if message is None:
        return
    try:
        await message.edit_text(text, reply_markup=reply_markup)
        return
    except Exception:
        pass
    if getattr(message, "photo", None):
        try:
            await message.edit_caption(caption=text, reply_markup=reply_markup)
            return
        except Exception:
            pass
    try:
        await message.delete()
    except Exception:
        pass
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text=text,
        reply_markup=reply_markup,
    )
