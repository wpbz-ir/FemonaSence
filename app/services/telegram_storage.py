from __future__ import annotations

import os

from app.core.config import settings
from app.core.media_config import validate_bot_api_base_url
from app.services.telegram_media import TelegramMediaClient, TelegramMediaError


async def check_telegram_storage() -> dict:
    # [INT-c] Same scheme guard as the media worker (shared check in
    # app/core/media_config.py): https only, or http on localhost/127.0.0.1/::1
    # for a local Bot API server. Invalid values log an ERROR and fall back to
    # the official cloud URL instead of dying later with aiohttp InvalidURL
    # inside the health check.
    base_url = validate_bot_api_base_url(
        os.getenv("TELEGRAM_BOT_API_BASE_URL", "https://api.telegram.org")
    )
    chat_id = settings.production_storage_chat_id or os.getenv("TELEGRAM_STORAGE_CHAT_ID")
    client = TelegramMediaClient(token=settings.bot_token, base_url=base_url)
    if not chat_id:
        return {"ok": False, "error": "شناسه کانال ذخیره‌سازی تنظیم نشده است"}

    import aiohttp

    from app.bot.session import telegram_aiohttp_connector

    # [FIX] The session-level ClientTimeout(total=30) was dead code: each
    # client._json call passed timeout=self.timeout (total=3600) at the
    # request level, and an explicit request timeout fully REPLACES the
    # session one in aiohttp — so a hung endpoint could stall this health
    # check for an hour. _json now accepts a per-request timeout override and
    # every health-check call passes this short one explicitly.
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout, connector=telegram_aiohttp_connector()) as session:
        me = await client._json(session, "getMe", timeout=timeout)
        chat = await client._json(session, "getChat", params={"chat_id": chat_id}, timeout=timeout)
        try:
            member = await client._json(
                session,
                "getChatMember",
                params={"chat_id": chat_id, "user_id": me["id"]},
                timeout=timeout,
            )
        except TelegramMediaError as exc:
            member = {"error": str(exc)}

    membership_ok = bool(member.get("status")) and member.get("status") not in {"left", "kicked"}
    return {
        "ok": membership_ok,
        "api_base_url": base_url,
        "mode": "local_bot_api" if "api.telegram.org" not in base_url else "telegram_cloud_bot_api",
        "bot_id": me.get("id"),
        "chat_id": str(chat_id),
        "chat_title": chat.get("title"),
        "bot_membership": member,
        "error": None if membership_ok else "ربات عضو فعال کانال ذخیره‌سازی نیست.",
    }
