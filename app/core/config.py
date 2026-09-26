from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _int_or_none(value: str | None) -> int | None:
    if not value or not value.strip():
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError("ADMIN_USER_ID باید یک عدد صحیح باشد.") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development").strip()
    app_name: str = os.getenv("APP_NAME", "فمونا سنس").strip()
    log_level: str = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    bot_token: str = os.getenv("BOT_TOKEN", "").strip()
    admin_user_id: int | None = _int_or_none(os.getenv("ADMIN_USER_ID"))
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://cinema_vault:CHANGE_ME@localhost:5432/cinema_vault",
    ).strip()
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0").strip()
    main_channel_id: int | None = _int_or_none(os.getenv("MAIN_CHANNEL_ID"))
    main_channel_username: str = os.getenv("MAIN_CHANNEL_USERNAME", "").strip()
    production_storage_chat_id: int | None = _int_or_none(os.getenv("PRODUCTION_STORAGE_CHAT_ID"))
    test_storage_chat_id: int | None = _int_or_none(os.getenv("TEST_STORAGE_CHAT_ID"))
    winapay_merchant_id: str = os.getenv("WINAPAY_MERCHANT_ID", "").strip()
    winapay_sandbox: bool = os.getenv("WINAPAY_SANDBOX", "1").strip().lower() in {
        "1", "true", "yes", "on"
    }
    winapay_base_url: str = os.getenv(
        "WINAPAY_BASE_URL", "https://winapay.io/webservice/rest"
    ).strip()
    db_echo: bool = os.getenv("DB_ECHO", "0").strip().lower() in {"1", "true", "yes", "on"}
    admin_api_token: str = os.getenv("ADMIN_API_TOKEN", "").strip()
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    rate_limit_enabled: bool = os.getenv("RATE_LIMIT_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    rate_limit_fail_closed: bool = os.getenv("RATE_LIMIT_FAIL_CLOSED", "0").strip().lower() in {"1", "true", "yes", "on"}
    media_upstream_allowlist: str = os.getenv("MEDIA_UPSTREAM_ALLOWLIST", "").strip()
    media_allow_any_https: bool = os.getenv("MEDIA_ALLOW_ANY_HTTPS", "0").strip().lower() in {"1", "true", "yes", "on"}
    telegram_mode: str = os.getenv("TELEGRAM_MODE", "polling").strip().lower()
    telegram_webhook_secret: str = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
    telegram_proxy_url: str = os.getenv("TELEGRAM_PROXY_URL", "").strip()
    bot_username: str = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
    run_maintenance_in_bot: bool = os.getenv("RUN_MAINTENANCE_IN_BOT", "1").strip().lower() in {"1", "true", "yes", "on"}
    run_notifications_in_bot: bool = os.getenv("RUN_NOTIFICATIONS_IN_BOT", "1").strip().lower() in {"1", "true", "yes", "on"}
    maintenance_interval_seconds: int = max(5, int(os.getenv("MAINTENANCE_INTERVAL_SECONDS", "15")))
    allowed_hosts: tuple[str, ...] = tuple(x.strip() for x in os.getenv("ALLOWED_HOSTS", "").split(",") if x.strip())
    content_rights_required: bool = os.getenv("CONTENT_RIGHTS_REQUIRED", "1").strip().lower() in {"1", "true", "yes", "on"}
    telegram_storage_required: bool = os.getenv("TELEGRAM_STORAGE_REQUIRED", "1").strip().lower() in {"1", "true", "yes", "on"}
    startup_validate: bool = os.getenv("STARTUP_VALIDATE", "1").strip().lower() in {"1", "true", "yes", "on"}
    # رشد و عملیات دوره‌ای
    free_daily_download_limit: int = max(0, int(os.getenv("FREE_DAILY_DOWNLOAD_LIMIT", "5") or "0"))
    referral_reward_irr: int = max(0, int(os.getenv("REFERRAL_REWARD_IRR", "100000") or "0"))
    winback_coupon_code: str = os.getenv("WINBACK_COUPON_CODE", "").strip()
    auto_backup_enabled: bool = os.getenv("AUTO_BACKUP_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    auto_backup_chat_id: int | None = _int_or_none(os.getenv("AUTO_BACKUP_CHAT_ID"))
    auto_backup_hour: int = max(0, min(23, int(os.getenv("AUTO_BACKUP_HOUR", "3") or "3")))


settings = Settings()


def validate_settings(*, strict: bool = False) -> list[str]:
    problems: list[str] = []
    if settings.app_env == "production":
        if not settings.bot_token: problems.append("BOT_TOKEN is missing")
        if not settings.admin_api_token or len(settings.admin_api_token) < 32: problems.append("ADMIN_API_TOKEN must be at least 32 characters")
        if not settings.public_base_url.startswith("https://"): problems.append("PUBLIC_BASE_URL must use https:// in production")
        if not settings.redis_url: problems.append("REDIS_URL is required in production")
        if not settings.rate_limit_enabled: problems.append("RATE_LIMIT_ENABLED must be 1 in production")
        if not settings.allowed_hosts: problems.append("ALLOWED_HOSTS must be configured in production")
        if settings.run_maintenance_in_bot or settings.run_notifications_in_bot: problems.append("RUN_MAINTENANCE_IN_BOT and RUN_NOTIFICATIONS_IN_BOT must be 0 in production")
        if settings.media_allow_any_https and settings.media_upstream_allowlist: problems.append("MEDIA_ALLOW_ANY_HTTPS and MEDIA_UPSTREAM_ALLOWLIST should not both be enabled")
        if settings.telegram_mode == "webhook" and len(settings.telegram_webhook_secret) < 16: problems.append("TELEGRAM_WEBHOOK_SECRET is required for webhook mode")
        if settings.telegram_mode not in {"polling", "webhook"}: problems.append("TELEGRAM_MODE must be polling or webhook")
        if settings.telegram_storage_required and not (settings.production_storage_chat_id or os.getenv("TELEGRAM_STORAGE_CHAT_ID")):
            problems.append("Production Telegram storage chat is not configured")
        if settings.content_rights_required and not os.getenv("CONTENT_RIGHTS_ACK"):
            problems.append("CONTENT_RIGHTS_ACK must be set when content rights gate is enabled")
    if strict and problems:
        raise RuntimeError("Production configuration invalid: " + "; ".join(problems))
    return problems
