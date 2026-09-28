from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv()


def _int_or_none(value: str | None, env_name: str) -> int | None:
    if not value or not value.strip():
        return None
    try:
        return int(value)
    except ValueError as exc:
        # [FIX-E] نام متغیر در پیام خطا پارامتری شد — پیام قبلی همیشه
        # ADMIN_USER_ID را مقصر می‌کرد حتی وقتی MAIN_CHANNEL_ID و… خراب بود.
        raise RuntimeError(f"{env_name} باید یک عدد صحیح باشد.") from exc


def _env_int(
    name: str,
    default: int,
    lo: int | None = None,
    hi: int | None = None,
    *,
    empty: int | None = None,
) -> int:
    """[FIX-E] خواندن env عددی بدون کرش در زمان import روی مقدار نامعتبر.

    - unset → ``default`` (همان قرارداد ``os.getenv(name, default)``).
    - مقدار غیرعددی → هشدار یک‌خطی روی stderr و بازگشت ``default``
      (به‌جای traceback بی‌موردِ ``int()`` در سطح ماژول).
    - «خالی صریح» (``VAR=``) → اگر ``empty`` داده شده باشد همان مقدار
      (قرارداد قبلی‌ی ``or "0"`` — مثلاً FREE_DAILY_DOWNLOAD_LIMIT= یعنی 0)،
      وگرنه ``default``.
    - در انتها clamping با lo/hi — دقیقاً همان مرزهای max/min قبلی.
    """
    raw = os.getenv(name)
    if raw is None:
        value = default
    elif raw.strip() == "":
        value = default if empty is None else empty
    else:
        try:
            value = int(raw.strip())
        except (TypeError, ValueError):
            print(
                f"[config] WARNING: {name}={raw!r} is not an integer — "
                f"using default {default}",
                file=sys.stderr,
            )
            value = default
    if lo is not None and value < lo:
        value = lo
    if hi is not None and value > hi:
        value = hi
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development").strip()
    app_name: str = os.getenv("APP_NAME", "فمونا سنس").strip()
    log_level: str = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    # [INT-c] field(repr=False) on every credential-bearing field: the dataclass
    # repr must never carry the bot token, admin token, DB password or webhook
    # secret into logs/tracebacks (repr(Settings()) omits these fields entirely).
    bot_token: str = field(
        default=os.getenv("BOT_TOKEN", "").strip(), repr=False
    )
    admin_user_id: int | None = _int_or_none(os.getenv("ADMIN_USER_ID"), "ADMIN_USER_ID")
    database_url: str = field(
        repr=False,
        default=os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://cinema_vault:CHANGE_ME@localhost:5432/cinema_vault",
        ).strip(),
    )
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0").strip()
    main_channel_id: int | None = _int_or_none(os.getenv("MAIN_CHANNEL_ID"), "MAIN_CHANNEL_ID")
    main_channel_username: str = os.getenv("MAIN_CHANNEL_USERNAME", "").strip()
    production_storage_chat_id: int | None = _int_or_none(
        os.getenv("PRODUCTION_STORAGE_CHAT_ID"), "PRODUCTION_STORAGE_CHAT_ID"
    )
    test_storage_chat_id: int | None = _int_or_none(os.getenv("TEST_STORAGE_CHAT_ID"), "TEST_STORAGE_CHAT_ID")
    winapay_merchant_id: str = os.getenv("WINAPAY_MERCHANT_ID", "").strip()
    winapay_sandbox: bool = os.getenv("WINAPAY_SANDBOX", "1").strip().lower() in {
        "1", "true", "yes", "on"
    }
    winapay_base_url: str = os.getenv(
        "WINAPAY_BASE_URL", "https://winapay.io/webservice/rest"
    ).strip()
    db_echo: bool = os.getenv("DB_ECHO", "0").strip().lower() in {"1", "true", "yes", "on"}
    admin_api_token: str = field(
        default=os.getenv("ADMIN_API_TOKEN", "").strip(), repr=False
    )
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    rate_limit_enabled: bool = os.getenv("RATE_LIMIT_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    rate_limit_fail_closed: bool = os.getenv("RATE_LIMIT_FAIL_CLOSED", "0").strip().lower() in {"1", "true", "yes", "on"}
    media_upstream_allowlist: str = os.getenv("MEDIA_UPSTREAM_ALLOWLIST", "").strip()
    media_allow_any_https: bool = os.getenv("MEDIA_ALLOW_ANY_HTTPS", "0").strip().lower() in {"1", "true", "yes", "on"}
    telegram_mode: str = os.getenv("TELEGRAM_MODE", "polling").strip().lower()
    telegram_webhook_secret: str = field(
        default=os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip(), repr=False
    )
    telegram_proxy_url: str = os.getenv("TELEGRAM_PROXY_URL", "").strip()
    bot_username: str = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
    run_maintenance_in_bot: bool = os.getenv("RUN_MAINTENANCE_IN_BOT", "1").strip().lower() in {"1", "true", "yes", "on"}
    run_notifications_in_bot: bool = os.getenv("RUN_NOTIFICATIONS_IN_BOT", "1").strip().lower() in {"1", "true", "yes", "on"}
    maintenance_interval_seconds: int = _env_int("MAINTENANCE_INTERVAL_SECONDS", 15, lo=5)
    allowed_hosts: tuple[str, ...] = tuple(x.strip() for x in os.getenv("ALLOWED_HOSTS", "").split(",") if x.strip())
    content_rights_required: bool = os.getenv("CONTENT_RIGHTS_REQUIRED", "1").strip().lower() in {"1", "true", "yes", "on"}
    telegram_storage_required: bool = os.getenv("TELEGRAM_STORAGE_REQUIRED", "1").strip().lower() in {"1", "true", "yes", "on"}
    startup_validate: bool = os.getenv("STARTUP_VALIDATE", "1").strip().lower() in {"1", "true", "yes", "on"}
    # رشد و عملیات دوره‌ای
    free_daily_download_limit: int = _env_int("FREE_DAILY_DOWNLOAD_LIMIT", 5, lo=0, empty=0)
    referral_reward_irr: int = _env_int("REFERRAL_REWARD_IRR", 100000, lo=0, empty=0)
    winback_coupon_code: str = os.getenv("WINBACK_COUPON_CODE", "").strip()
    auto_backup_enabled: bool = os.getenv("AUTO_BACKUP_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    auto_backup_chat_id: int | None = _int_or_none(os.getenv("AUTO_BACKUP_CHAT_ID"), "AUTO_BACKUP_CHAT_ID")
    auto_backup_hour: int = _env_int("AUTO_BACKUP_HOUR", 3, lo=0, hi=23, empty=3)


settings = Settings()

# [P1-20] طرح‌های مجاز برای TELEGRAM_PROXY_URL — همان چیزی که مصرف‌کننده‌ها
# می‌پذیرند: AiohttpSession(proxy=...) و aiohttp_socks.ProxyConnector.from_url
# (app/bot/session.py) از socks5/socks5h/socks4/http پشتیبانی می‌کنند و https
# پروکسی هم برای کلاینت‌های خام aiohttp معتبر است.
_PROXY_SCHEMES = ("socks5", "socks5h", "socks4", "http", "https")


def _redact_url_credentials(url: str) -> str:
    """اعتبارنامه‌ی user:pass در هر URL را قبل از چاپ در لاگ حذف می‌کند."""
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    _, _, hostport = rest.rpartition("@")
    return f"{scheme}://[REDACTED]@{hostport}" if scheme else "[REDACTED]@" + hostport


def _telegram_proxy_problem(url: str) -> str | None:
    """[P1-20] (1) TELEGRAM_PROXY_URL را اعتبارسنجی می‌کند.

    شرط‌ها: طرح ∈ {socks5, socks5h, socks4, http, https} + وجود host و در صورت
    حضور، port عددی معتبر. پراکسی خراب یعنی کل ترافیک تلگرام بی‌صدا می‌میرد
    (make_bot/AiohttpSession در اولین درخواست شکست می‌خورد)، پس باید در استارت‌آپ
    دیده شود، نه در اولین پیام کاربر.
    """
    try:
        parts = urlsplit(url)
        port = parts.port  # برای port غیرعددی یا خارج از محدوده ValueError می‌دهد
    except ValueError:
        return (
            "TELEGRAM_PROXY_URL is not a valid proxy URL "
            "(scheme://[user:pass@]host[:port]); got: "
            + _redact_url_credentials(url)
        )
    if parts.scheme not in _PROXY_SCHEMES:
        return (
            "TELEGRAM_PROXY_URL scheme must be one of "
            + ", ".join(_PROXY_SCHEMES)
            + f" (got scheme {parts.scheme!r})"
        )
    if not parts.hostname:
        return (
            "TELEGRAM_PROXY_URL must include a host[:port] "
            f"(e.g. socks5://127.0.0.1:10808); got: {_redact_url_credentials(url)}"
        )
    if port is not None and not (1 <= port <= 65535):
        return f"TELEGRAM_PROXY_URL port must be 1..65535 (got port {port})"
    return None


def _database_password_is_placeholder(url: str) -> bool:
    """[P1-20] (4) تشخیص پسورد پیش‌فرض CHANGE_ME در DATABASE_URL."""
    if ":CHANGE_ME@" in url:
        return True
    try:
        return (urlsplit(url).password or "") == "CHANGE_ME"
    except ValueError:
        return False


# [P1-20] هشدارها/اطلاعیه‌ها فقط یک بار در هر پروسس چاپ می‌شوند تا فراخوانی‌های
# مکرر (استارت‌آپ + /health/config + اسکریپت‌ها) لاگ را شلوغ نکنند.
_diagnostics_printed = False


def validate_settings(*, strict: bool = False) -> list[str]:
    """اعتبارسنجی پیکربندی؛ فهرست «problems» را برمی‌گرداند.

    قرارداد شدت [P1-20]:
    - موارد `problems` در PRODUCTION مرگبارند: هر دو دروازه‌ی استارت‌آپ
      (main.py برای Bot و app/api/main.py برای پنل/API) با
      ``validate_settings(strict=app_env == "production")`` صدا می‌زنند و
      strict=True همین‌جا RuntimeError می‌دهد => fail-closed در عملیات.
    - موارد هشدار/اطلاعیه (diagnostics) هرگز raise نمی‌کنند؛ یک بار روی stderr چاپ می‌شوند
      (هم dev هم prod) — سرویس کار می‌کند ولی کمیتی تنزل کرده است.
    - در dev همه‌چیز غیرمرگبار است (فقط هشدار/اطلاعیه چاپ می‌شود).
    """
    global _diagnostics_printed
    problems: list[str] = []
    diagnostics: list[str] = []
    production = settings.app_env == "production"

    if production:
        if not settings.bot_token: problems.append("BOT_TOKEN is missing")
        # [P1-20] (5) ADMIN_API_TOKEN کوتاه/خالی در production فATAL است —
        # این فهرست همان است که دروازه‌ی strict بالا RuntimeError می‌دهد
        # (main.py:100 و app/api/main.py:67). در dev این شاخه اجرا نمی‌شود،
        # پس dev محتاطانه آزاد می‌ماند.
        if not settings.admin_api_token or len(settings.admin_api_token) < 32: problems.append("ADMIN_API_TOKEN must be at least 32 characters")
        if not settings.public_base_url.startswith("https://"): problems.append("PUBLIC_BASE_URL must use https:// in production")
        # [P1-20] (4) پسورد جانگهدار CHANGE_ME در production مرگبار است؛
        # مقدار پیش‌فرض dev عمداً CHANGE_ME دارد و در dev مشکلی ندارد.
        if _database_password_is_placeholder(settings.database_url):
            problems.append("DATABASE_URL still uses the CHANGE_ME placeholder password — set a real database password in production")
        if not settings.rate_limit_enabled: problems.append("RATE_LIMIT_ENABLED must be 1 in production")
        if not settings.allowed_hosts: problems.append("ALLOWED_HOSTS must be configured in production")
        if settings.run_maintenance_in_bot or settings.run_notifications_in_bot: problems.append("RUN_MAINTENANCE_IN_BOT and RUN_NOTIFICATIONS_IN_BOT must be 0 in production")
        if settings.media_allow_any_https and settings.media_upstream_allowlist: problems.append("MEDIA_ALLOW_ANY_HTTPS and MEDIA_UPSTREAM_ALLOWLIST should not both be enabled")
        if settings.telegram_mode == "webhook" and len(settings.telegram_webhook_secret) < 16: problems.append("TELEGRAM_WEBHOOK_SECRET is required for webhook mode")
        if settings.telegram_mode not in {"polling", "webhook"}: problems.append("TELEGRAM_MODE must be polling or webhook")
        # [INT-c] WINAPAY_BASE_URL must be https in production — the runtime
        # payment config carries its own https gate (runtime_payment_config +
        # winapay.py), and the env-level base_url is validated here so a
        # misconfigured gateway fails at startup, not at the first payment.
        if not settings.winapay_base_url.startswith("https://"):
            problems.append("WINAPAY_BASE_URL must use https:// in production")
        if settings.telegram_storage_required and not (settings.production_storage_chat_id or os.getenv("TELEGRAM_STORAGE_CHAT_ID")):
            problems.append("Production Telegram storage chat is not configured")
        if settings.content_rights_required and not os.getenv("CONTENT_RIGHTS_ACK"):
            problems.append("CONTENT_RIGHTS_ACK must be set when content rights gate is enabled")
        # [P1-20] (2) BOT_USERNAME خالی در production فقط هشدار است (نه مرگبار):
        # ربات در چت ۱:۱ درست کار می‌کند، ولی دقیقاً این‌ها می‌شکنند —
        # دیپ‌لینک‌های /start ref_ معرفی (growth.py لینک t.me/<bot>?start=ref_…
        # می‌سازد) و دکمه‌های اشتراک‌گذاری/اینلاین (catalog.py ساخت لینک
        # ?start=title_… را در نبودش کلاً رد می‌کند).
        if not settings.bot_username:
            diagnostics.append(
                "WARNING: BOT_USERNAME is empty in production — referral /start ref_ deep links "
                "and inline/share features are degraded (bot still works in 1:1 chats)"
            )
        # [INT-c] Warnings below are NON-fatal (degraded/verbose behavior, not
        # broken security): they print once on stderr like the other diagnostics.
        if settings.winapay_sandbox:
            diagnostics.append(
                "WARNING: WINAPAY_SANDBOX=1 in production — the panel shows sandbox mode; "
                "real routing is decided by WINAPAY_BASE_URL, verify it points at the live gateway"
            )
        if settings.db_echo:
            diagnostics.append(
                "WARNING: DB_ECHO=1 in production — SQLAlchemy echo logs every statement "
                "including user data; disable it unless debugging"
            )
        if settings.free_daily_download_limit == 0:
            diagnostics.append(
                "WARNING: FREE_DAILY_DOWNLOAD_LIMIT=0 — free downloads are DISABLED for "
                "non-subscribers (0 means zero, not unlimited)"
            )

    # [P1-20] (1) اعتبارسنجی پراکسی در هر دو محیط: production مرگبار (fail-closed
    # — پراکسی خراب همه‌ی ترافیک تلگرام را بی‌صدا می‌کشد)، dev فقط هشدار.
    if settings.telegram_proxy_url:
        proxy_issue = _telegram_proxy_problem(settings.telegram_proxy_url)
        if proxy_issue:
            if production:
                problems.append(proxy_issue)
            else:
                diagnostics.append("WARNING (dev): " + proxy_issue)

    # [P1-20] (3) بررسی واقعی REDIS_URL — بررسی قبلی (``not settings.redis_url``)
    # پوچ بود چون دیتاکلاس وقتی متغیر env ناموجود است مقدار پیش‌فرض
    # «redis://localhost:6379/0» می‌گذارد و آن شاخه تقریباً هرگز آتش نمی‌گرفت.
    # مصرف‌کننده‌ی واقعی (app/services/rate_limit.py:hit) مستقیم از os.getenv
    # می‌خواند، پس همین‌جا هم env خام ملاک است. رفتار واقعی بدون ردیس (کد خوانده
    # شد، حدس «fallback به حافظه» نادرست است): اگر REDIS_URL خالی/تنظیم‌نشده
    # باشد hit() قبل از هر شمارشی کوتاه می‌آید — fail-open پیش‌فرض یعنی
    # RateLimitResult(True, 0) و «هر درخواستی مجاز» بدون هیچ شمارنده‌ای در هیچ
    # حافظه‌ای؛ با RATE_LIMIT_FAIL_CLOSED=1 به‌جایش RateLimitUnavailable برمی‌خیزد.
    # تنها مصرف‌کننده‌ی دیگر ردیس، چک سلامت /health در app/api/main.py است که
    # «redis: down» گزارش می‌کند. هیچ cache درون-حافظه‌ای وجود ندارد.
    redis_env = os.getenv("REDIS_URL", "").strip()
    if redis_env:
        if not redis_env.startswith(("redis://", "rediss://")):
            message = (
                "REDIS_URL must start with redis:// or rediss:// (got scheme "
                + repr(redis_env.split("://", 1)[0])
                + ")"
            )
            if production:
                problems.append(message)
            else:
                diagnostics.append("WARNING (dev): " + message)
    elif production:
        problems.append(
            "REDIS_URL is required in production (without it rate limiting fail-opens: every request is allowed)"
        )
    else:
        diagnostics.append(
            "INFO: REDIS_URL is not set — rate limiting fail-opens (every request allowed; "
            "no in-memory fallback, no counters) or raises RateLimitUnavailable when "
            "RATE_LIMIT_FAIL_CLOSED=1; the /health Redis check reports down"
        )

    if diagnostics and not _diagnostics_printed:
        for line in diagnostics:
            print("[config] " + line, file=sys.stderr)
        _diagnostics_printed = True
    if strict and problems:
        raise RuntimeError("Production configuration invalid: " + "; ".join(problems))
    return problems
