from __future__ import annotations

import json

from aiogram.types import Update
from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import text

from app.bot.app import create_bot
from app.core.config import settings
from app.runtime.db import session_scope

router = APIRouter(prefix="/telegram", tags=["telegram"])


@router.post("/webhook", include_in_schema=False)
async def webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    if settings.telegram_mode != "webhook":
        raise HTTPException(status_code=404, detail="Webhook mode is disabled.")
    if len(settings.telegram_webhook_secret) < 16:
        raise HTTPException(status_code=404, detail="Not found")
    if not _constant_time_equal(x_telegram_bot_api_secret_token or "", settings.telegram_webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    raw = await request.body()
    try:
        payload = json.loads(raw.decode("utf-8"))
        update = Update.model_validate(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Telegram update.") from exc

    bot, dp = create_bot()
    update_id = int(update.update_id)
    bot_scope = settings.bot_username or "default"

    async with session_scope() as session:
        inserted = await session.execute(
            text(
                """
                INSERT INTO telegram_updates (id, update_id, bot_scope, status, payload, received_at)
                VALUES (gen_random_uuid(), :update_id, :bot_scope, 'RECEIVED', CAST(:payload AS jsonb), CURRENT_TIMESTAMP)
                ON CONFLICT (update_id, bot_scope) DO NOTHING
                RETURNING id
                """
            ),
            {"update_id": update_id, "bot_scope": bot_scope, "payload": json.dumps(payload, ensure_ascii=False)},
        )
        ledger_id = inserted.scalar_one_or_none()
        if ledger_id is None:
            existing = (
                await session.execute(
                    text(
                        """
                        SELECT id, status, received_at
                        FROM telegram_updates
                        WHERE update_id=:update_id AND bot_scope=:bot_scope
                        FOR UPDATE
                        """
                    ),
                    {"update_id": update_id, "bot_scope": bot_scope},
                )
            ).mappings().first()
            if existing and existing["status"] == "PROCESSED":
                await session.commit()
                await bot.session.close()
                return {"ok": True, "duplicate": True}
            # FAILED/stale RECEIVED updates are deliberately replayable.
            await session.execute(
                text(
                    """
                    UPDATE telegram_updates
                    SET status='RECEIVED', payload=CAST(:payload AS jsonb),
                        received_at=CURRENT_TIMESTAMP, processed_at=NULL, error_message=NULL
                    WHERE update_id=:update_id AND bot_scope=:bot_scope
                    """
                ),
                {"update_id": update_id, "bot_scope": bot_scope, "payload": json.dumps(payload, ensure_ascii=False)},
            )
            ledger_id = existing["id"] if existing else None
        await session.commit()

    if ledger_id is None:
        await bot.session.close()
        return {"ok": True, "duplicate": True}

    try:
        await dp.feed_update(bot, update)
    except Exception as exc:
        async with session_scope() as session:
            await session.execute(
                text(
                    """
                    UPDATE telegram_updates
                    SET status='FAILED', error_message=:error, processed_at=CURRENT_TIMESTAMP
                    WHERE id=:id
                    """
                ),
                {"id": ledger_id, "error": str(exc)[:4000]},
            )
            await session.commit()
        await bot.session.close()
        raise HTTPException(status_code=500, detail="Update processing failed.") from exc

    async with session_scope() as session:
        await session.execute(
            text(
                "UPDATE telegram_updates SET status='PROCESSED', processed_at=CURRENT_TIMESTAMP WHERE id=:id"
            ),
            {"id": ledger_id},
        )
        await session.commit()

    await bot.session.close()
    return {"ok": True}


def _constant_time_equal(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
