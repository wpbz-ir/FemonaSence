from __future__ import annotations

from aiogram import Bot, Dispatcher

from app.bot.handlers import ads, catalog, growth, menu, payments, releases, start, storage
from app.bot.handlers import membership  # باید اول از همه ثبت شود (دروازه عضویت)
from app.bot.session import make_bot


def create_bot() -> tuple[Bot, Dispatcher]:
    bot = make_bot()

    dp = Dispatcher()
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
