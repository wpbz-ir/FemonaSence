from __future__ import annotations

import html
import os
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, text

from app.core.config import settings
from app.db.models import Order, PaymentAttempt, Plan, User
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
            url = await prepare_winapay_payment(
                session,
                order=order,
                attempt=attempt,
                callback_url=callback,
            )
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

    invoice_id = payload.get("InvoiceID", "").strip()
    authority = payload.get("Authority", "").strip()
    status = payload.get("PaymentStatus", "").strip()
    if not invoice_id or not authority:
        return _payment_page("❌", "اطلاعات Callback ناقص است.")

    async with session_scope() as session:
        order = await session.scalar(
            select(Order).where(Order.order_number == invoice_id).with_for_update()
        )
        if order is None:
            return _payment_page("❌", "سفارش پرداخت پیدا نشد.")

        event_key = f"{authority}:{invoice_id}:{status}"
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
            {"event_key": event_key, "order_id": order.id, "payload": __import__("json").dumps(payload, ensure_ascii=False)},
        )
        event_id = inserted.scalar_one_or_none()
        if event_id is None and order.status == "PAID":
            await session.commit()
            return _payment_page("✅", "پرداخت شما قبلاً ثبت شده است.")

        if status != "OK":
            await session.execute(
                text("UPDATE payment_webhook_events SET status='IGNORED', processed_at=CURRENT_TIMESTAMP WHERE id=:id"),
                {"id": event_id},
            )
            await session.commit()
            return _payment_page("⚠️", "پرداخت توسط درگاه تأیید نشد یا لغو شده است.")

        try:
            payment = await settle_winapay_order(
                session,
                order_id=order.id,
                callback_payload=payload,
            )
        except Exception as exc:
            if event_id:
                await session.execute(
                    text(
                        "UPDATE payment_webhook_events SET status='FAILED', error_message=:error, processed_at=CURRENT_TIMESTAMP WHERE id=:id"
                    ),
                    {"id": event_id, "error": str(exc)[:4000]},
                )
            await session.commit()
            return _payment_page("❌", "تأیید پرداخت انجام نشد. اطلاعات سفارش ذخیره شده و نیاز به بررسی دارد.")

        if event_id:
            await session.execute(
                text("UPDATE payment_webhook_events SET status='PROCESSED', processed_at=CURRENT_TIMESTAMP WHERE id=:id"),
                {"id": event_id},
            )
        await session.commit()

    if payment:
        return _payment_page("✅", "پرداخت با موفقیت تأیید شد. سرویس شما فعال شده است.")
    return _payment_page("⚠️", "تأیید پرداخت ناموفق بود.")


def _payment_page(icon: str, message: str) -> HTMLResponse:
    safe = html.escape(message)
    return HTMLResponse(
        f"""<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>فمونا سنس</title><style>body{{font-family:Tahoma,Arial;background:#0b1020;color:#fff;display:grid;place-items:center;min-height:100vh;margin:0}}.box{{max-width:560px;padding:32px;background:#141d31;border:1px solid #2a3a5c;border-radius:20px;text-align:center}}.i{{font-size:48px}}</style></head><body><div class='box'><div class='i'>{icon}</div><h2>فمونا سنس</h2><p>{safe}</p><p>می‌توانید به ربات بازگردید.</p></div></body></html>"""
    )
