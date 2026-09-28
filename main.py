from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from app.bot.app import create_bot
from app.bot.session import make_bot
from app.core.config import settings, validate_settings
from app.core.logging import configure_logging
from app.workers.maintenance import maintenance_loop
from app.workers.notification_worker import run_notification_worker

logger = logging.getLogger("cinemavault.main")


async def setup_commands(bot: Bot) -> None:
    commands = [
        BotCommand(command="start", description="شروع"),
        BotCommand(command="help", description="راهنما"),
        BotCommand(command="subscription", description="اشتراک"),
        BotCommand(command="paysupport", description="پشتیبانی پرداخت"),
    ]
    # تلگرام موقتا در دسترس نبود؟ نباید کل ربات کرش کند - چند بار تلاش و بعد ادامه
    for attempt in range(1, 4):
        try:
            await bot.set_my_commands(commands)
            return
        except Exception:
            logger.warning(
                "set_my_commands failed (attempt %s/3) - is Telegram reachable?",
                attempt,
            )
            # [P3] بعد از تلاشِ آخر، صبرِ بی‌دلیل قبل از ادامه‌ی استارتاپ لازم نیست
            if attempt < 3:
                await asyncio.sleep(5)
    logger.warning("Telegram unreachable at startup - continuing without set_my_commands")


async def register_error_handler(dp) -> None:
    """خطاهای هندلرها را ثبت می‌کند و به کاربر پیام فارسی نشان می‌دهد.

    [5-INT-b] در aiogram 3، رویدادِ dp.errors یک ErrorEvent است (دارای
    update و exception) و خودش نه message دارد و نه bot — نسخه‌ی قبلی با
    event.bot/event.message مرده بود (AttributeError → except بی‌صدا).
    حالا: chat از event.update استخراج می‌شود (message یا
    callback_query.message) و bot از update (کانتکستِ aiogram) یا
    workflow_data گرفته می‌شود. ارسالِ پیامِ فارسی کاملاً محافظت‌شده است و
    هرگز traceback به کاربر نشان داده نمی‌شود.
    """

    @dp.errors()
    async def on_error(event, **_kwargs):
        exception = getattr(event, "exception", None)
        logger.error("خطای هندلر ربات", exc_info=exception)
        try:
            update = getattr(event, "update", None)
            message = getattr(update, "message", None) or getattr(update, "edited_message", None)
            if message is None:
                callback_query = getattr(update, "callback_query", None)
                message = getattr(callback_query, "message", None) if callback_query else None
            chat = getattr(message, "chat", None)
            bot = None
            try:
                # aiogram 3 رخدادِ در جریان را به bot می‌بندد (update.bot).
                bot = getattr(update, "bot", None)
            except Exception:
                bot = None
            if bot is None:
                # در run_polling هر تلاش، باتِ جاری در workflow_data ثبت می‌شود.
                bot = dp.workflow_data.get("bot")
            if chat is not None and bot is not None:
                await bot.send_message(
                    chat.id,
                    "⚠️ خطایی رخ داد. لطفاً دوباره تلاش کنید یا از /start استفاده کنید.",
                )
        except Exception:
            logger.warning("ارسال پیام خطا به کاربر ناموفق بود", exc_info=True)
        return True


async def _supervised(name: str, factory) -> None:
    """ورد را اجرا می‌کند؛ در صورت کرش، پس از تاخیر کوتاه مجدداً راه‌اندازی می‌شود."""
    while True:
        try:
            await factory()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Worker %s متوقف شد؛ راه‌اندازی مجدد پس از ۵ ثانیه", name)
            await asyncio.sleep(5)


async def run_polling(dp: Dispatcher) -> None:
    """هر تلاشِ polling: باتِ تازه + ثبت دستورات + start_polling.

    نکته‌ی مهم: در aiogram 3.x پیش‌فرض start_polling نشست بات را می‌بندد
    (close_bot_session=True)؛ در آن صورت پس از اولین خروج، همه‌ی تلاش‌های
    بعدی با نشستِ بسته شکست می‌خورند و حلقه‌ی بازیابی می‌میرد. برای همین:
    ۱) هر تلاش با make_bot() باتِ تازه می‌سازد (نشست قبلیِ بسته هرگز به
       start_polling نمی‌رسد)؛
    ۲) close_bot_session=False تا aiogram نشستِ باتِ جاری را نبندد و
       بسته‌شدن نشست را خودِ همین تابع در finally انجام می‌دهد.
    """
    bot = make_bot()
    try:
        # دیسپچر باید به باتِ تازه‌ی همین تلاش اشاره کند تا reference کهنه
        # (باتی با نشست بسته) در workflow data باقی نماند.
        dp.workflow_data["bot"] = bot
        # ثبت دستورات غیرمرگبار است (۳ بار تلاش و بعد ادامه) - در هر تلاش تکرار می‌شود
        await setup_commands(bot)
        # [PERF] فقط انواع رخدادِ استفاده‌شده از تلگرام درخواست شود؛ updateهای
        # اضافه (edited_message، message_reaction و ...) قبل از رسیدن به ربات
        # دور ریخته می‌شوند — روی اتصال پرتأخیر/پراکسی‌دار تفاوت محسوس دارد.
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            close_bot_session=False,
        )
    finally:
        # نشستِ باتِ همین تلاش را خودمان می‌بندیم تا نشت session رخ ندهد؛
        # تلاش بعدی (بعد از backoff در _supervised) باتِ تازه می‌سازد.
        await bot.session.close()


async def main() -> None:
    configure_logging()
    validate_settings(strict=settings.app_env == "production")

    if settings.telegram_mode != "polling":
        raise RuntimeError(
            "TELEGRAM_MODE=webhook است؛ در این حالت Bot را با polling اجرا نکنید. "
            "Webhook باید از app.api.main توسط Uvicorn سرو شود."
        )

    bot, dp = create_bot()
    try:
        await register_error_handler(dp)
        await setup_commands(bot)
        print("======================================")
        print("فمونا سنس")
        print("ربات در حال اجراست...")
        print("======================================")

        # polling هم supervision شده تا قطعی شبکه کل ربات را نکشد
        # هر تلاش polling باتِ تازه می‌سازد (run_polling) تا نشستِ بسته‌شده
        # باعث شکست همیشگی تلاش‌های بعدی نشود.
        tasks = [_supervised("polling", lambda: run_polling(dp))]
        if settings.run_maintenance_in_bot:
            tasks.append(
                _supervised(
                    "maintenance",
                    lambda: maintenance_loop(
                        bot,
                        interval_seconds=settings.maintenance_interval_seconds,
                    ),
                )
            )
        if settings.run_notifications_in_bot:
            tasks.append(
                _supervised(
                    "notifications",
                    lambda: run_notification_worker(poll_seconds=1.0),
                )
            )
        await asyncio.gather(*tasks)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
