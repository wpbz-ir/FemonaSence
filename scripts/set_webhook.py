from __future__ import annotations

import argparse
import asyncio

from aiogram import Bot
from dotenv import load_dotenv

from app.core.config import settings, validate_settings


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--delete", action="store_true")
    args = parser.parse_args()
    validate_settings(strict=False)

    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN is not configured.")

    bot = Bot(settings.bot_token)
    try:
        if args.delete:
            await bot.delete_webhook(drop_pending_updates=False)
            print("WEBHOOK_DELETED")
            return

        if settings.telegram_mode != "webhook":
            raise SystemExit("Set TELEGRAM_MODE=webhook first.")
        if not settings.public_base_url.startswith("https://"):
            raise SystemExit("PUBLIC_BASE_URL must be HTTPS for webhook mode.")
        if len(settings.telegram_webhook_secret) < 16:
            raise SystemExit("TELEGRAM_WEBHOOK_SECRET is missing/too short.")

        url = f"{settings.public_base_url}/telegram/webhook"
        await bot.set_webhook(
            url=url,
            secret_token=settings.telegram_webhook_secret,
            drop_pending_updates=False,
            max_connections=40,
            allowed_updates=None,
        )
        info = await bot.get_webhook_info()
        print("WEBHOOK_SET")
        print("URL:", info.url)
        print("Pending:", info.pending_update_count)
        print("Last error:", info.last_error_message or "")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
