from __future__ import annotations

import time

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
from app.api.media_access import router as media_router
from app.api.payments import router as payments_router
from app.api.telegram_webhook import router as telegram_webhook_router
from app.core.brand import BRAND_NAME_FA
from app.core.config import settings, validate_settings
from app.core.logging import configure_logging
from app.db.models import User
from app.db.session import get_session
from app.runtime.db import session_scope
from app.services.heartbeats import heartbeat

configure_logging()

_ready_cache: dict = {"value": None, "at": 0.0}

app = FastAPI(
    title=BRAND_NAME_FA,
    version="1.0.0",
    docs_url="/docs" if settings.app_env != "production" else None,
    redoc_url="/redoc" if settings.app_env != "production" else None,
)
app.state.settings = settings
if settings.allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))


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


@app.middleware("http")
async def request_context(request: Request, call_next):
    import time
    import uuid

    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.request_id = request_id
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if settings.app_env == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Response-Time-ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
    return response


app.include_router(admin_router)
app.include_router(payments_router)
app.include_router(media_router)
app.include_router(telegram_webhook_router)


@app.get("/", include_in_schema=False)
async def root():
    return {"service": BRAND_NAME_FA, "status": "OK"}


_ADMIN_SECTIONS = {
    "dashboard", "users", "user360", "titles", "series", "taxonomy",
    "upload", "pipelines", "jobs", "plans", "payments", "bot-menu",
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


@app.get("/health/ready", include_in_schema=False)
async def health_ready():
    # نتیجه برای ۳۰ ثانیه کش می‌شود تا این مسیر عمومی به تقویت‌کننده فراخوانی‌های
    # خروجی (get_me / Redis ping) تبدیل نشود.
    now = time.monotonic()
    cached = _ready_cache.get("value")
    if cached is not None and now - _ready_cache.get("at", 0.0) < 30:
        return cached

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
            from aiogram import Bot
            bot = Bot(settings.bot_token)
            me = await bot.get_me()
            bot_ok = bool(me.id)
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

    ready = db_ok and redis_ok and bot_ok and storage_ok
    payload = {
        "status": "READY" if ready else "DEGRADED",
        "database": "OK" if db_ok else "ERROR",
        "redis": "OK" if redis_ok else "ERROR",
        "telegram": "OK" if bot_ok else "ERROR",
        "storage": "OK" if storage_ok else "ERROR",
    }
    _ready_cache["value"] = payload
    _ready_cache["at"] = now
    return payload


@app.get("/health/config", include_in_schema=False)
async def health_config():
    problems = validate_settings(strict=False)
    return {
        "environment": settings.app_env,
        "valid": not problems,
        "problems": problems,
    }
