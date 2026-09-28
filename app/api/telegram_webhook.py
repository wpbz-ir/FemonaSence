from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from aiogram.types import Update
from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import text

from app.bot.app import create_bot
from app.core.config import settings
from app.runtime.db import session_scope
from app.services.rate_limit import RateLimitExceeded, RateLimitUnavailable, enforce

router = APIRouter(prefix="/telegram", tags=["telegram"])

# [5-INT-b / 5-G12-c #6] سقف اندازه‌ی بدنه‌ی وبهوک تلگرام (۲۵۶ کیلوبایت).
MAX_WEBHOOK_BODY_BYTES = 256 * 1024
# [5-INT-b / 5-G12-c #3] پنجره‌ی مهلتِ تازه‌بودن یک ردیف RECEIVED در حال پردازش.
RECEIVED_REPLAY_GRACE = timedelta(minutes=5)


def _as_utc(value: datetime | None) -> datetime | None:
    """مقایسه‌ی امنِ زمانی: اگر راننده‌ی DB مقدار naive داد، UTC فرض می‌شود."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@router.post("/webhook", include_in_schema=False)
async def webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    # [5-INT-b / 5-G12-c #10] محدودیت نرخ per-IP روی خودِ مسیر وبهوک — در بالای
    # هندلر تا هرزدرخواست‌های ناشناس (۴۰۱) هم ارزان بمانند. شکستِ limiter
    # fail-open است: قطع Redis نباید درگاه تلگرام را ببندد.
    client_host = request.client.host if request.client else "unknown"
    try:
        await enforce(f"wh:{client_host}", limit=60, window_seconds=60)
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail="Too many requests") from exc
    except RateLimitUnavailable:
        pass

    if settings.telegram_mode != "webhook":
        raise HTTPException(status_code=404, detail="Webhook mode is disabled.")
    if len(settings.telegram_webhook_secret) < 16:
        raise HTTPException(status_code=404, detail="Not found")
    if not _constant_time_equal(x_telegram_bot_api_secret_token or "", settings.telegram_webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    # [5-INT-b / 5-G12-c #6] سقف اندازه‌ی بدنه — قبل از request.body() تا یک بدنه‌ی
    # غیرمتعارف بزرگ در حافظه بارگذاری نشود (Content-Length غایب = نادیده؛ تلگرام همیشه می‌فرستد).
    content_length = request.headers.get("content-length", "")
    # [FIX-C] isdigit() برای «²» هم True است ولی int() روی آن ValueError می‌دهد
    # (۵۰۰). هدر غایب/خالی مثل قبل نادیده گرفته می‌شود؛ مقدار غیرعددی ASCII → 400؛
    # مقدار عددی بالاتر از سقف → 413 (مطابق رفتار قبلی سقف بدنه).
    if content_length and not (content_length.isascii() and content_length.isdecimal()):
        raise HTTPException(status_code=400, detail="Invalid Content-Length")
    if content_length.isascii() and content_length.isdecimal() and int(content_length) > MAX_WEBHOOK_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")

    raw = await request.body()
    try:
        payload = json.loads(raw.decode("utf-8"))
        update = Update.model_validate(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Telegram update.") from exc

    bot, dp = create_bot()
    update_id = int(update.update_id)
    bot_scope = settings.bot_username or "default"

    # [P1-12 hardening] create_bot() یک ClientSession تازه می‌سازد؛ هر مسیر خروج
    # (خطای DB، خطای پردازش آپدیت، CancelledError یا موفقیت) باید session را ببندد.
    # بنابراین همه‌ی منطق در try/finally قرار گرفته است.
    try:
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
                    return {"ok": True, "duplicate": True}
                # [5-INT-b / 5-G12-c #3] مسابقه‌ی replay: ردیف RECEIVEDِ تازه یعنی
                # تحویلِ اول هنوز در حال پردازش است (dp.feed_update در جریان است) و
                # نباید دوباره feed شود. فقط FAILED، یا RECEIVEDِ قدیمی‌تر از ۵ دقیقه
                # (timezone-aware) دوباره تخصیص داده می‌شوند؛ مسیرِ سریعِ تحویلِ اول
                # (ledger_id is not None) دست‌نخورده باقی می‌ماند.
                if (
                    existing
                    and existing["status"] == "RECEIVED"
                ):
                    received_at = _as_utc(existing["received_at"])
                    if received_at is not None and (
                        datetime.now(timezone.utc) - received_at
                    ) <= RECEIVED_REPLAY_GRACE:
                        await session.commit()
                        return {"ok": True, "duplicate": True}
                # FAILED / RECEIVEDِ کهنه قابل تکرار هستند.
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
            raise HTTPException(status_code=500, detail="Update processing failed.") from exc

        async with session_scope() as session:
            await session.execute(
                text(
                    "UPDATE telegram_updates SET status='PROCESSED', processed_at=CURRENT_TIMESTAMP WHERE id=:id"
                ),
                {"id": ledger_id},
            )
            await session.commit()

        return {"ok": True}
    finally:
        try:
            await bot.session.close()
        except Exception:  # هرگز خطای بستن session را روی پاسخ اصلی سوار نکن
            pass


def _constant_time_equal(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
