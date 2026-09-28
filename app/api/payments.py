from __future__ import annotations

import html
import json
import os
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, text

from app.core.config import settings
# [FIX-C] Plan/User حذف شدند — در این ماژول استفاده نمی‌شدند (rg-verified).
from app.db.models import Order, PaymentAttempt
from app.runtime.db import session_scope
from app.services.payment_sessions import resolve_payment_session
from app.services.rate_limit import RateLimitExceeded, RateLimitUnavailable, enforce
from app.services.winapay_billing import prepare_winapay_payment, settle_winapay_order

router = APIRouter(prefix="/payments", tags=["payments"])


@router.get("/winapay/start/{token}")
async def start_winapay_payment(token: str):
    if not settings.public_base_url.startswith("https://") and settings.app_env == "production":
        raise HTTPException(status_code=503, detail="PUBLIC_BASE_URL is not configured for secure payment callbacks.")
    try:
        await enforce(f"payment-start:{token[:24]}", limit=20, window_seconds=60)
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail="تعداد درخواست‌ها بیش از حد مجاز است.") from exc
    except RateLimitUnavailable as exc:
        raise HTTPException(status_code=503, detail="سامانه محدودکننده درخواست در دسترس نیست.") from exc

    async with session_scope() as session:
        payment_session = await resolve_payment_session(session, token)
        if payment_session is None or payment_session.provider != "WINAPAY":
            raise HTTPException(status_code=410, detail="نشست پرداخت منقضی شده است.")
        order = await session.get(Order, payment_session.order_id)
        attempt = await session.scalar(
            select(PaymentAttempt)
            .where(
                PaymentAttempt.order_id == payment_session.order_id,
                PaymentAttempt.provider == "WINAPAY",
            )
            .order_by(PaymentAttempt.created_at.desc())
        )
        if order is None or attempt is None:
            raise HTTPException(status_code=404, detail="سفارش پرداخت پیدا نشد.")
        if order.user_id != payment_session.user_id:
            raise HTTPException(status_code=403, detail="نشست پرداخت با کاربر تطبیق ندارد.")
        if order.status == "PAID":
            return _payment_page("✅", "این سفارش قبلاً پرداخت شده و سرویس شما فعال است.")
        callback = f"{settings.public_base_url}/payments/winapay/callback"
        if attempt.payment_url and attempt.status == "PENDING":
            url = attempt.payment_url
        else:
            # [P0-8] Provider prepare errors must never surface as an unhandled 500:
            # persist the PaymentAttempt as FAILED, then answer 502 with a Persian message.
            try:
                url = await prepare_winapay_payment(
                    session,
                    order=order,
                    attempt=attempt,
                    callback_url=callback,
                )
            except Exception as exc:
                attempt.status = "FAILED"
                attempt.error_message = (str(exc) or exc.__class__.__name__)[:4000]
                await session.commit()
                raise HTTPException(
                    status_code=502,
                    detail="خطا در ارتباط با درگاه پرداخت ویناپی. لطفاً چند لحظه دیگر دوباره تلاش کنید.",
                ) from exc
        order.status = "PENDING"
        await session.commit()
    return RedirectResponse(url, status_code=303)


@router.post("/winapay/callback", response_class=HTMLResponse, include_in_schema=False)
@router.get("/winapay/callback", response_class=HTMLResponse, include_in_schema=False)
async def winapay_callback(request: Request):
    # WinaPay currently documents POST callbacks; GET is retained as a tolerant return endpoint.
    payload: dict[str, str] = {}
    if request.method == "POST":
        from urllib.parse import parse_qsl
        body = (await request.body()).decode("utf-8", "replace")
        payload = {str(k): str(v) for k, v in parse_qsl(body, keep_blank_values=True)}
    else:
        payload = {k: v for k, v in request.query_params.items()}

    # [P1-13] This endpoint is unauthenticated: rate-limit per client IP (60/min)
    # BEFORE any DB work, so forged hammering cannot pile up webhook-event rows and
    # provider verify round-trips (canonical P1-13 / 4-G08-c F5).
    client_host = request.client.host if request.client else "unknown"
    try:
        await enforce(f"winapay-cb:{client_host}", limit=60, window_seconds=60)
    except RateLimitExceeded:
        return _payment_page(
            "⚠️",
            "تعداد درخواست‌های پرداخت بیش از حد مجاز است. لطفاً کمی بعد دوباره تلاش کنید.",
            status_code=429,
        )
    except RateLimitUnavailable:
        # Fail-open on limiter outage: blocking a genuine gateway callback because
        # Redis is down would stall real money settlement (the start endpoint keeps
        # its stricter 503 behaviour — there the human user can simply retry).
        pass

    # [P1-13] DataError guard: Authority/InvoiceID land in event_key (String(255))
    # and in DB lookups — validate + truncate to 255 BEFORE any write/lookup
    # (an oversized forged field used to raise a 500 outside the try block).
    invoice_id = payload.get("InvoiceID", "").strip()[:255]
    authority = payload.get("Authority", "").strip()[:255]
    status = payload.get("PaymentStatus", "").strip()[:255]
    if not invoice_id or not authority:
        return _payment_page("❌", "اطلاعات Callback ناقص است.")
    event_key = f"{authority}:{invoice_id}:{status}"[:255]

    async with session_scope() as session:
        # [P1-13] Pre-flight txn WITHOUT row locks: the order FOR UPDATE lock now
        # lives ONLY in settle_winapay_order's final txn, so the 30s gateway verify
        # never runs while holding a lock (canonical P1-13 / 4-G08-c F3).
        order = await session.scalar(
            select(Order).where(Order.order_number == invoice_id)
        )
        if order is None:
            return _payment_page("❌", "سفارش پرداخت پیدا نشد.")

        stored_attempt = await session.scalar(
            select(PaymentAttempt)
            .where(
                PaymentAttempt.order_id == order.id,
                PaymentAttempt.provider == "WINAPAY",
            )
            .order_by(PaymentAttempt.created_at.desc())
        )

        # [P1-13] Authority cross-check (defense-in-depth, BEFORE any provider
        # verify call): settle re-verifies with the STORED attempt.authority anyway,
        # so a callback whose Authority differs from the attempt created for this
        # order is forged/corrupt — record the event (dedup table) as FAILED and
        # reject with 400 without ever touching the gateway.
        if (
            stored_attempt is not None
            and (stored_attempt.authority or "").strip()
            and stored_attempt.authority != authority
        ):
            await session.execute(
                text(
                    """
                    INSERT INTO payment_webhook_events
                        (id, provider, event_key, order_id, status, payload, error_message, created_at)
                    VALUES
                        (gen_random_uuid(), 'WINAPAY', :event_key, :order_id, 'FAILED', CAST(:payload AS jsonb), :error, CURRENT_TIMESTAMP)
                    ON CONFLICT (provider, event_key) DO NOTHING
                    """
                ),
                {
                    "event_key": f"AUTH_MISMATCH:{authority}:{invoice_id}:{status}"[:255],
                    "order_id": order.id,
                    "payload": json.dumps(payload, ensure_ascii=False),
                    "error": "Authority ارسالی با Authority ثبت‌شده برای این سفارش مطابقت ندارد.",
                },
            )
            await session.commit()
            return _payment_page("❌", "درخواست تأیید پرداخت نامعتبر است.", status_code=400)

        inserted = await session.execute(
            text(
                """
                INSERT INTO payment_webhook_events
                    (id, provider, event_key, order_id, status, payload, created_at)
                VALUES
                    (gen_random_uuid(), 'WINAPAY', :event_key, :order_id, 'RECEIVED', CAST(:payload AS jsonb), CURRENT_TIMESTAMP)
                ON CONFLICT (provider, event_key) DO NOTHING
                RETURNING id
                """
            ),
            {"event_key": event_key, "order_id": order.id, "payload": json.dumps(payload, ensure_ascii=False)},
        )
        event_id = inserted.scalar_one_or_none()
        if event_id is None and order.status == "PAID":
            # [5-INT-b / 5-G13-e #3] تکراری‌بودن callback + سفارشِ PAID: رویداد قبلاً
            # ثبت شده و تسویه انجام شده است — نباید تا ابد RECEIVED بماند؛ PROCESSED می‌شود.
            await session.execute(
                text(
                    """
                    UPDATE payment_webhook_events
                    SET status='PROCESSED', processed_at=CURRENT_TIMESTAMP
                    WHERE provider='WINAPAY' AND event_key=:event_key AND status <> 'PROCESSED'
                    """
                ),
                {"event_key": event_key},
            )
            await session.commit()
            return _payment_page("✅", "پرداخت شما قبلاً ثبت شده است.")

        if status != "OK":
            await session.execute(
                text("UPDATE payment_webhook_events SET status='IGNORED', processed_at=CURRENT_TIMESTAMP WHERE id=:id"),
                {"id": event_id},
            )
            await session.commit()
            return _payment_page("⚠️", "پرداخت توسط درگاه تأیید نشد یا لغو شده است.")

        # Commit the webhook-event bookkeeping so settle_winapay_order owns its own
        # txn boundaries and the gateway verify runs with NO open transaction.
        await session.commit()

        try:
            payment = await settle_winapay_order(
                session,
                order_id=order.id,
                callback_payload=payload,
            )
        except Exception as exc:
            # [P0-8] Provider verify errors: persist the PaymentAttempt as FAILED,
            # mark the webhook event FAILED, then answer 502 so the gateway retries
            # (webhook event dedup keeps retries idempotent).
            # [P1-13] settle may raise mid-transaction: roll the partial txn back
            # first so this session is reusable (otherwise PendingRollbackError
            # would turn the curated 502 into an unhandled 500). The webhook event
            # row is already committed above, so the FAILED update still lands.
            await session.rollback()
            failed_attempt = await session.scalar(
                select(PaymentAttempt)
                .where(
                    PaymentAttempt.order_id == order.id,
                    PaymentAttempt.provider == "WINAPAY",
                )
                .order_by(PaymentAttempt.created_at.desc())
            )
            if failed_attempt is not None:
                failed_attempt.status = "FAILED"
                failed_attempt.error_message = (str(exc) or exc.__class__.__name__)[:4000]
            if event_id:
                await session.execute(
                    text(
                        "UPDATE payment_webhook_events SET status='FAILED', error_message=:error, processed_at=CURRENT_TIMESTAMP WHERE id=:id"
                    ),
                    {"id": event_id, "error": str(exc)[:4000]},
                )
            await session.commit()
            raise HTTPException(
                status_code=502,
                detail="خطا در تأیید پرداخت با درگاه ویناپی. اطلاعات ثبت شده است و پس از تلاش مجدد درگاه بررسی خواهد شد.",
            ) from exc

        if event_id:
            if payment is not None:
                await session.execute(
                    text("UPDATE payment_webhook_events SET status='PROCESSED', processed_at=CURRENT_TIMESTAMP WHERE id=:id"),
                    {"id": event_id},
                )
            else:
                # [5-INT-b / 5-G13-e #3] settle None برگردانده (تأیید درگاه ناموفق /
                # عدم تطابق مبلغ) → رویداد PROCESSED نمی‌شود؛ FAILED ثبت می‌شود تا
                # state machine رویدادها صادق بماند و replay بعدی بر اساس FAILED انجام شود.
                await session.execute(
                    text(
                        "UPDATE payment_webhook_events SET status='FAILED', error_message=:error, processed_at=CURRENT_TIMESTAMP WHERE id=:id"
                    ),
                    {"id": event_id, "error": "settle_winapay_order نتیجه‌ای برنگرداند (تأیید ناموفق یا مبلغ مغایر)."[:4000]},
                )
        await session.commit()

    if payment:
        return _payment_page("✅", "پرداخت با موفقیت تأیید شد. سرویس شما فعال شده است.")
    # [FIX-C] مسیر شکستِ verify (settle None برگردانده → attempt FAILED + آزادسازی کد
    # تخفیف در winapay_billing) دیگر 200 برنمی‌گرداند؛ با 200 درگاه retry نمی‌کرد و
    # پرداختِ کسرشده تا ابد PENDING می‌ماند. 502 باعث تلاش مجدد درگاه می‌شود و
    # dedup رویدادها (payment_webhook_events) replay ها را idempotent نگه می‌دارد.
    # شاخه‌های AUTH_MISMATCH/missing-fields/!OK رفتار قبلی خود را دارند.
    return _payment_page(
        "⚠️",
        "خطا در تایید پرداخت؛ مبلغ کسر شده حداکثر تا ۷۲ ساعت به‌صورت خودکار تسویه می‌شود.",
        status_code=502,
    )


def _payment_page(icon: str, message: str, *, status_code: int = 200) -> HTMLResponse:
    safe = html.escape(message)
    return HTMLResponse(
        f"""<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>فمونا سنس</title><style>body{{font-family:Tahoma,Arial;background:#0b1020;color:#fff;display:grid;place-items:center;min-height:100vh;margin:0}}.box{{max-width:560px;padding:32px;background:#141d31;border:1px solid #2a3a5c;border-radius:20px;text-align:center}}.i{{font-size:48px}}</style></head><body><div class='box'><div class='i'>{icon}</div><h2>فمونا سنس</h2><p>{safe}</p><p>می‌توانید به ربات بازگردید.</p></div></body></html>""",
        status_code=status_code,
    )
