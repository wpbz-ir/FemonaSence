from __future__ import annotations

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from app.core.config import settings


def create_bot_session() -> AiohttpSession:
    """نشست تلگرام؛ اگر TELEGRAM_PROXY_URL تنظیم شده باشد، ترافیک از پراکسی عبور می‌کند.

    پشتیبانی می‌شود: socks5://127.0.0.1:10808 و http://127.0.0.1:10809
    (برای socks5 باید aiohttp-socks نصب باشد - در requirements.txt هست).
    """
    proxy = (settings.telegram_proxy_url or "").strip()
    if proxy:
        return AiohttpSession(proxy=proxy)
    return AiohttpSession()


def make_bot(token: str | None = None) -> Bot:
    """کارخانه‌ی متمرکز ساخت Bot تا همه‌ی بخش‌ها (ربات، پنل ادمین، API) از پراکسی پیروی کنند."""
    resolved_token = (token or settings.bot_token or "").strip()
    if not resolved_token:
        raise RuntimeError("BOT_TOKEN در .env تنظیم نشده است.")

    return Bot(
        token=resolved_token,
        session=create_bot_session(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def telegram_aiohttp_connector():
    """Connector برای aiohttp.ClientSession های خام که مستقیم به api.telegram.org می‌روند.

    اگر TELEGRAM_PROXY_URL تنظیم نشده باشد None برمی‌گردد (رفتار پیش‌فرض aiohttp).
    برای socks5/http هر دو نیازمند aiohttp-socks هستیم که در requirements.txt هست.
    """
    proxy = (settings.telegram_proxy_url or "").strip()
    if not proxy:
        return None
    try:
        from aiohttp_socks import ProxyConnector
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "TELEGRAM_PROXY_URL is set but aiohttp-socks is not installed. "
            "Run: pip install aiohttp-socks"
        ) from exc
    return ProxyConnector.from_url(proxy)
