from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Deployment docs run this as a direct path (`python scripts/set_webhook.py`);
# bootstrap the repo root so `import app` works regardless of invocation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from app.bot.session import make_bot
from app.core.config import settings, validate_settings


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--delete", action="store_true")
    args = parser.parse_args()
    validate_settings(strict=False)

    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN is not configured.")

    # make_bot() is the central factory (app/bot/session.py): it honors
    # TELEGRAM_PROXY_URL so webhook setup also works behind blocked Telegram,
    # same Bot lifecycle as before (session closed in the finally block below).
    bot = make_bot()
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
