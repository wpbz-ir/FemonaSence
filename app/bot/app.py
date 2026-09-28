from __future__ import annotations

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType

from app.bot.handlers import ads, catalog, growth, menu, payments, releases, start, storage
from app.bot.handlers import membership  # باید اول از همه ثبت شود (دروازه عضویت)
from app.bot.session import make_bot


def create_bot() -> tuple[Bot, Dispatcher]:
    bot = make_bot()

    dp = Dispatcher()
    # [PRIVATE-ONLY] ربات فقط در گفتگوی خصوصی پاسخ می‌دهد؛ در گروه/کانال هیچ
    # دکمه یا پیامی برای اعضای دیگر اجرا یا نمایش داده نمی‌شود (باگِ «اقدامات
    # همه برای همه اجرا می‌شود» از همین‌جا بود). توجه: channel_post مربوط به
    # ورود فایل از کانال ذخیره‌سازی، observer جداگانه است و از این فیلترها
    # متأثر نمی‌شود؛ pre_checkout_query هم بدون chat است و رد نمی‌شود.
    dp.message.filter(F.chat.type == ChatType.PRIVATE)
    dp.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
    # ترتیب مهم است: دروازه عضویت قبل از همه روترهای محتوایی
    dp.include_router(membership.router)
    dp.include_router(start.router)
    dp.include_router(catalog.router)
    dp.include_router(releases.router)
    dp.include_router(payments.router)
    dp.include_router(ads.router)
    dp.include_router(growth.router)
    dp.include_router(menu.router)
    dp.include_router(storage.router)

    return bot, dp
