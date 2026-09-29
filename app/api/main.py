from __future__ import annotations

import hmac
import logging
import re
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from pathlib import Path
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.admin import router as admin_router
from app.api import admin_extended  # noqa: F401  (routes register on the shared admin router)
from app.api import admin_features  # noqa: F401  (ads + membership routes on the shared admin router)
from app.api.payments import router as payments_router
from app.api.telegram_webhook import router as telegram_webhook_router
from app.core.brand import BRAND_NAME_FA
from app.core.config import settings, validate_settings
from app.core.logging import configure_logging
from app.db.session import get_session
from app.runtime.db import session_scope
from app.services.heartbeats import heartbeat

configure_logging()

logger = logging.getLogger("app.api.main")

_ready_cache: dict = {"value": None, "at": 0.0}

# [P1-12] در محیط عملیاتی، مستندات تعاملی و اسکیمای OpenAPI غیرفعال می‌شوند.
_is_production = settings.app_env == "production"
app = FastAPI(
    title=BRAND_NAME_FA,
    version="1.0.0",
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    openapi_url=None if _is_production else "/openapi.json",
)
app.state.settings = settings
if settings.allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
elif _is_production:
    # [P1-12/P1-20] اگر ALLOWED_HOSTS در production تنظیم نشده باشد، استارت‌آپ
    # نباید کرش کند (پنل باید بالا بماند)؛ در عوض یک سیاست محافظه‌کار فقط-لوکال‌هاست
    # نصب می‌شود و هشدار واضح ثبت می‌گردد تا اپراتور آن را اصلاح کند.
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["localhost", "127.0.0.1"],
    )
    logger.warning("=" * 72)
    logger.warning(
        "SECURITY WARNING: ALLOWED_HOSTS is NOT set while APP_ENV=production. "
        "TrustedHostMiddleware was installed with conservative defaults "
        "['localhost', '127.0.0.1'] — external hostnames will be rejected with 400 "
        "until you set ALLOWED_HOSTS (comma-separated public hostnames) in .env "
        "and restart the service."
    )
    logger.warning("=" * 72)


@app.on_event("startup")
async def startup():
    if settings.startup_validate:
        validate_settings(strict=settings.app_env == "production")
    async with session_scope() as session:
        await heartbeat(
            session,
            service_name="web",
            metadata={"app_name": settings.app_name, "version": "1.0.0"},
        )
        await session.commit()


@app.on_event("shutdown")
async def shutdown():
    try:
        async with session_scope() as session:
            await heartbeat(
                session,
                service_name="web",
                status="DOWN",
                metadata={"app_name": settings.app_name},
            )
            await session.commit()
    except Exception:
        pass


# [P1-12] CSP سازگار با پنل ادمین (app/api/templates/admin.html): پنل فقط فونت را
# از fonts.googleapis.com (CSS) و fonts.gstatic.com (فایل فونت) بارگذاری می‌کند و
# اسکریپت/استایل داخلی دارد؛ پیوندهای winapay.io لینک ناوبری هستند و مشمول CSP
# منابع نمی‌شوند. همین CSP برای صفحه پرداخت (payments.py) نیز بی‌ضرر است.
_CSP_HEADER = (
    "default-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; "
    "script-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'"
)

# مسیرهای مستندات FastAPI (فقط غیر-production فعال‌اند) اسکریپت/استایل از
# cdn.jsdelivr.net بارگذاری می‌کنند؛ CSP پنل را روی آن‌ها اعمال نمی‌کنیم.
_CSP_EXEMPT_PATHS = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}


@app.middleware("http")
async def request_context(request: Request, call_next):
    # [FIX-C] مقدار x-request-id ارسالی کلاینت بدون اعتبارسنجی به هدر پاسخ بازتاب
    # داده می‌شد؛ کاراکترهای CR/LF باعث LocalProtocolError در h11 و ۵۰۰ می‌شدند.
    # فقط مقادیر امن [A-Za-z0-9\-_.]{1,64} پذیرفته می‌شوند، در غیر این صورت
    # شناسه‌ی سرور-تولیدشده جایگزین می‌شود.
    request_id = request.headers.get("x-request-id") or ""
    if not re.fullmatch(r"[A-Za-z0-9\-_.]{1,64}", request_id):
        request_id = uuid.uuid4().hex
    request.state.request_id = request_id
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if (response.headers.get("content-type") or "").lower().startswith("text/html") and request.url.path not in _CSP_EXEMPT_PATHS:
        response.headers["Content-Security-Policy"] = _CSP_HEADER
    if settings.app_env == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Response-Time-ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
    return response


app.include_router(admin_router)
app.include_router(payments_router)
app.include_router(telegram_webhook_router)


@app.get("/", include_in_schema=False)
async def root():
    return {"service": BRAND_NAME_FA, "status": "OK"}


_ADMIN_SECTIONS = {
    "dashboard", "users", "user360", "titles", "series", "taxonomy",
    "upload", "pipelines", "jobs", "plans", "coupons", "payments", "bot-menu",
    "audit", "settings", "membership", "ads",
}
_ADMIN_HTML = Path(__file__).parent / "templates" / "admin.html"


def _admin_response() -> FileResponse:
    return FileResponse(
        _ADMIN_HTML,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )


@app.get("/admin", include_in_schema=False)
async def admin_page():
    return _admin_response()


@app.get("/admin/{section}", include_in_schema=False)
async def admin_section_page(section: str):
    if section not in _ADMIN_SECTIONS:
        raise HTTPException(status_code=404, detail="صفحه پیدا نشد.")
    return _admin_response()


@app.get("/health/live", include_in_schema=False)
async def health_live():
    return {"status": "LIVE", "service": BRAND_NAME_FA}


async def _compute_ready_components() -> dict[str, bool]:
    # نتیجه برای ۳۰ ثانیه کش می‌شود تا این مسیر عمومی به تقویت‌کننده فراخوانی‌های
    # خروجی (get_me / Redis ping) تبدیل نشود.
    db_ok = False
    redis_ok = False
    bot_ok = False
    storage_ok = False

    try:
        async for session in get_session():
            await session.execute(text("SELECT 1"))
            db_ok = True
            break
    except SQLAlchemyError:
        db_ok = False

    if not settings.rate_limit_enabled and settings.app_env != "production":
        redis_ok = True
    else:
        try:
            redis = Redis.from_url(settings.redis_url, decode_responses=True)
            try:
                await redis.ping()
                redis_ok = True
            finally:
                await redis.aclose()
        except Exception:
            redis_ok = False

    if settings.bot_token:
        try:
            from app.bot.session import make_bot

            bot = make_bot()
            # [G04-e LK-1] close the per-probe session even when get_me fails
            # (Telegram down), otherwise every cache miss leaks a ClientSession.
            try:
                me = await bot.get_me()
                bot_ok = bool(me.id)
            finally:
                await bot.session.close()
        except Exception:
            bot_ok = False

    if settings.telegram_storage_required and settings.bot_token:
        try:
            from app.services.telegram_storage import check_telegram_storage
            storage = await check_telegram_storage()
            storage_ok = bool(storage.get("ok"))
        except Exception:
            storage_ok = False
    else:
        storage_ok = True

    return {"database": db_ok, "redis": redis_ok, "telegram": bot_ok, "storage": storage_ok}


@app.get("/health/ready", include_in_schema=False)
async def health_ready(request: Request, detail: bool = False):
    now = time.monotonic()
    components = _ready_cache.get("value")
    if components is None or now - _ready_cache.get("at", 0.0) >= 30:
        components = await _compute_ready_components()
        _ready_cache["value"] = components
        _ready_cache["at"] = now

    # [P1-12] payload عمومی فقط وضعیت کلی را برمی‌گرداند؛ توپولوژی زیرساخت
    # (database/redis/telegram/storage) فقط با توکن ادمین و ?detail=1 افشا می‌شود.
    payload = {"status": "READY" if all(components.values()) else "DEGRADED"}
    if detail and _admin_token_ok(request):
        payload.update({name: ("OK" if ok else "ERROR") for name, ok in components.items()})
    return payload


@app.get("/health/config", include_in_schema=False)
async def health_config(request: Request):
    # [P1-12] بدون توکن ادمین فقط نتیجه بولی و تعداد مشکلات برگردد؛ رشته‌های
    # problems (وضعیت BOT_TOKEN/ADMIN_API_TOKEN/...) فقط با توکن ادمین.
    problems = validate_settings(strict=False)
    payload = {
        "valid": not problems,
        "problems_count": len(problems),
    }
    if _admin_token_ok(request):
        payload["environment"] = settings.app_env
        payload["problems"] = problems
    return payload


def _admin_token_ok(request: Request) -> bool:
    """بررسی سبک توکن ادمین (الگوی همان constant-time مقایسه در app/api/admin_auth.py)

    برخلاف admin_gate خطا raise نمی‌کند؛ فقط True/False می‌دهد تا مسیرهای سلامت
    بتوانند بدون توکن، پاسخ حداقلی برگردانند.
    """
    expected = settings.admin_api_token
    if not expected:
        return False
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        return False
    provided = header[7:].strip()
    if not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
