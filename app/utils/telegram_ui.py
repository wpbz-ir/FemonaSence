"""ابزارهای مشترک UI تلگرام برای هندلرها."""
from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

logger = logging.getLogger(__name__)


async def edit_or_send(callback: CallbackQuery, text: str, reply_markup=None) -> bool | None:
    """پیام را ویرایش می‌کند؛ اگر پیام عکسی باشد caption ویرایش می‌شود و در
    غیر این صورت پیام تازه برای کاربر ارسال می‌شود (جلوگیری از TelegramBadRequest).

    خروجی True یعنی «message is not modified» — پیام از قبل همین متن/کیبورد را
    داشت و عمداً هیچ تغییر/حذف/ارسالِ مجددی انجام نشده است.
    """
    message = callback.message
    if message is None:
        return
    try:
        await message.edit_text(text, reply_markup=reply_markup)
        return
    except Exception as err:
        # [P2] «message is not modified» خطای بی‌آزار است (همان متن/کیبورد)؛
        # پیام سالم است و نباید حذف/مجدد ارسال شود (پرش موقعیت اسکرول/گم‌شدن پیام).
        if isinstance(err, TelegramBadRequest) and "message is not modified" in str(err):
            return True
        logger.debug("edit_or_send: edit_text failed; falling back (%s)", err)
    if getattr(message, "photo", None):
        try:
            await message.edit_caption(caption=text, reply_markup=reply_markup)
            return
        except Exception as err:
            if isinstance(err, TelegramBadRequest) and "message is not modified" in str(err):
                return True
            logger.debug("edit_or_send: edit_caption failed; falling back (%s)", err)
    try:
        await message.delete()
    except Exception:
        pass
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text=text,
        reply_markup=reply_markup,
    )
