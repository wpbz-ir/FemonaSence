from __future__ import annotations

import os

from app.core.config import settings
from app.services.telegram_media import TelegramMediaClient, TelegramMediaError


async def check_telegram_storage() -> dict:
    base_url = os.getenv("TELEGRAM_BOT_API_BASE_URL", "https://api.telegram.org").rstrip("/")
    chat_id = settings.production_storage_chat_id or os.getenv("TELEGRAM_STORAGE_CHAT_ID")
    client = TelegramMediaClient(token=settings.bot_token, base_url=base_url)
    if not chat_id:
        return {"ok": False, "error": "TELEGRAM_STORAGE_CHAT_ID is not configured"}

    import aiohttp

    from app.bot.session import telegram_aiohttp_connector

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout, connector=telegram_aiohttp_connector()) as session:
        me = await client._json(session, "getMe")
        chat = await client._json(session, "getChat", params={"chat_id": chat_id})
        try:
            member = await client._json(
                session,
                "getChatMember",
                params={"chat_id": chat_id, "user_id": me["id"]},
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
        "error": None if membership_ok else "Bot is not an active member of the Telegram storage chat.",
    }
