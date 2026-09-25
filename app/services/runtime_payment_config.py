from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

from app.core.config import settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.getenv("CINEMAVAULT_RUNTIME_DIR", str(PROJECT_ROOT / "data" / "runtime"))) / "payment_gateway.json"


def _defaults() -> dict:
    return {
        "enabled": bool(settings.winapay_merchant_id),
        "provider": "WINAPAY",
        "sandbox": bool(settings.winapay_sandbox),
        "merchant_id": settings.winapay_merchant_id,
        "base_url": settings.winapay_base_url,
        "timeout_seconds": 30,
    }


def _safe_defaults() -> dict:
    value = _defaults()
    value["merchant_id_configured"] = bool(value.pop("merchant_id"))
    return value


def load_payment_config() -> dict:
    if not CONFIG_PATH.exists():
        return _defaults()
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _defaults()
    value = _defaults()
    if isinstance(raw, dict):
        value.update({k: raw[k] for k in value if k in raw})
    return value


def public_payment_config() -> dict:
    value = load_payment_config()
    return {
        "enabled": bool(value["enabled"]),
        "provider": str(value["provider"]),
        "sandbox": bool(value["sandbox"]),
        "base_url": str(value["base_url"]),
        "timeout_seconds": int(value["timeout_seconds"]),
        "merchant_id_configured": bool(value.get("merchant_id")),
    }


def validate_payment_config(value: dict) -> dict:
    enabled = bool(value.get("enabled", False))
    provider = str(value.get("provider", "WINAPAY")).strip().upper()
    sandbox = bool(value.get("sandbox", True))
    merchant_id = str(value.get("merchant_id", "")).strip()
    base_url = str(value.get("base_url", settings.winapay_base_url)).strip().rstrip("/")
    try:
        timeout = int(value.get("timeout_seconds", 30))
    except (TypeError, ValueError) as exc:
        raise ValueError("مدت انتظار باید عدد صحیح باشد.") from exc

    if provider != "WINAPAY":
        raise ValueError("درگاه فعلی فقط WINAPAY را پشتیبانی می‌کند.")
    if not base_url.startswith("https://"):
        raise ValueError("نشانی پایه درگاه باید با HTTPS باشد.")
    parsed = urlsplit(base_url)
    if not parsed.netloc:
        raise ValueError("نشانی پایه درگاه معتبر نیست.")
    if not 5 <= timeout <= 120:
        raise ValueError("مدت انتظار باید بین ۵ تا ۱۲۰ ثانیه باشد.")
    if enabled and not sandbox and not merchant_id:
        raise ValueError("برای محیط واقعی، شناسه پذیرنده الزامی است.")

    return {
        "enabled": enabled,
        "provider": provider,
        "sandbox": sandbox,
        "merchant_id": merchant_id,
        "base_url": base_url,
        "timeout_seconds": timeout,
    }


def save_payment_config(value: dict) -> dict:
    clean = validate_payment_config(value)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=CONFIG_PATH.parent, delete=False) as tmp:
        json.dump(clean, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        temp_name = tmp.name
    try:
        os.replace(temp_name, CONFIG_PATH)
    finally:
        try:
            Path(temp_name).unlink(missing_ok=True)
        except OSError:
            pass
    return public_payment_config()
