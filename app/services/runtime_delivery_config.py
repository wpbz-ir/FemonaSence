"""پیکربندی زمان‌اجرای تحویل فایل — فعلاً فقط «اعتبار لینک دانلود».

[TTL-DL] مدت اعتبار دکمه‌های دانلود (ثانیه)؛ ادمین از پنل تغییرش می‌دهد.
الگوی ذخیره‌سازی اتمیک (tmp + os.replace) از runtime_payment_config پیروی می‌کند
تا نیم‌نوشته/خراب‌شده‌ی JSON هرگز پیکربندی را نکشد.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.core.config import settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.getenv("CINEMAVAULT_RUNTIME_DIR", str(PROJECT_ROOT / "data" / "runtime"))) / "delivery.json"

_TTL_MIN = 0
_TTL_MAX = 86400


def _clamp(value: int) -> int:
    return max(_TTL_MIN, min(int(value), _TTL_MAX))


def load_delivery_config() -> dict:
    """اعتبار فعلی لینک دانلود (ثانیه)؛ ۰ یعنی بدون انقضا."""
    default = _clamp(settings.download_link_ttl_seconds)
    if not CONFIG_PATH.exists():
        return {"download_link_ttl_seconds": default}
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"download_link_ttl_seconds": default}
    ttl = default
    if isinstance(raw, dict):
        try:
            ttl = int(raw.get("download_link_ttl_seconds", default))
        except (TypeError, ValueError):
            ttl = default
    return {"download_link_ttl_seconds": _clamp(ttl)}


def validate_delivery_config(value: dict) -> dict:
    try:
        ttl = int(value.get("download_link_ttl_seconds"))
    except (TypeError, ValueError) as exc:
        raise ValueError("مدت اعتبار لینک باید عدد صحیح باشد.") from exc
    if ttl < _TTL_MIN or ttl > _TTL_MAX:
        raise ValueError("مدت اعتبار باید بین ۰ تا ۸۶۴۰۰ ثانیه باشد (۰ یعنی بدون انقضا).")
    return {"download_link_ttl_seconds": _clamp(ttl)}


def save_delivery_config(value: dict) -> dict:
    clean = validate_delivery_config(value)
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
    return clean
