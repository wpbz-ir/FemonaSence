from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot.handlers import ads, catalog, menu, payments, releases, start, storage
from app.bot.handlers import membership  # باید اول از همه ثبت شود (دروازه عضویت)
from app.core.config import settings


def create_bot() -> tuple[Bot, Dispatcher]:
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN در .env تنظیم نشده است.")

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher()
    # ترتیب مهم است: دروازه عضویت قبل از همه روترهای محتوایی
    dp.include_router(membership.router)
    dp.include_router(start.router)
    dp.include_router(catalog.router)
    dp.include_router(releases.router)
    dp.include_router(payments.router)
    dp.include_router(ads.router)
    dp.include_router(menu.router)
    dp.include_router(storage.router)

    return bot, dp
