from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.services.text_normalization import repair_mojibake

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.getenv("CINEMAVAULT_RUNTIME_DIR", str(PROJECT_ROOT / "data" / "runtime"))) / "bot_menu.json"

DEFAULTS = {
    "movies": {"enabled": True, "label": "🎬 فیلم‌ها"},
    "series": {"enabled": True, "label": "📺 سریال‌ها"},
    "animation": {"enabled": True, "label": "🧿 انیمیشن"},
    "popular": {"enabled": True, "label": "🔥 محبوب‌ترین‌ها"},
    "new": {"enabled": True, "label": "🆕 تازه‌ها"},
    "imdb": {"enabled": True, "label": "⭐ برترین‌های IMDb"},
    "years": {"enabled": True, "label": "📅 سال تولید"},
    "genres": {"enabled": True, "label": "🎭 ژانرها"},
    "collections": {"enabled": True, "label": "🎬 مجموعه‌ها"},
    "actors": {"enabled": True, "label": "🎭 بازیگران"},
    "search": {"enabled": True, "label": "🔎 جستجو"},
    "continue": {"enabled": True, "label": "▶️ ادامه تماشا"},
    "ads": {"enabled": True, "label": "📣 تبلیغات"},
    "favorites": {"enabled": True, "label": "❤️ علاقه‌مندی‌ها"},
    "history": {"enabled": True, "label": "🕘 تاریخچه"},
    "account": {"enabled": True, "label": "👤 حساب کاربری"},
    "subscription": {"enabled": True, "label": "💎 خرید اشتراک"},
    "wallet": {"enabled": True, "label": "💰 کیف پول"},
    "admin": {"enabled": True, "label": "🛠 مدیریت", "admins_only": True},
}

def _copy_defaults() -> dict:
    return json.loads(json.dumps(DEFAULTS, ensure_ascii=False))

def load_bot_menu_settings() -> dict:
    current = _copy_defaults()
    if not CONFIG_PATH.exists():
        return current
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return current
    if not isinstance(raw, dict):
        return current
    for key, item in current.items():
        incoming = raw.get(key)
        if not isinstance(incoming, dict):
            continue
        if "enabled" in incoming:
            item["enabled"] = bool(incoming["enabled"])
        if isinstance(incoming.get("label"), str):
            label = repair_mojibake(incoming["label"].strip())
            if label:
                item["label"] = label[:80]
    return current

def save_bot_menu_settings(value: dict) -> dict:
    current = load_bot_menu_settings()
    if not isinstance(value, dict):
        value = {}
    for key, item in current.items():
        incoming = value.get(key)
        if not isinstance(incoming, dict):
            continue
        if "enabled" in incoming:
            item["enabled"] = bool(incoming["enabled"])
        if "label" in incoming and isinstance(incoming["label"], str):
            label = repair_mojibake(incoming["label"].strip())
            if label:
                item["label"] = label[:80]
    public_keys = [key for key in current if key != "admin"]
    if not any(current[key].get("enabled") for key in public_keys):
        current["account"]["enabled"] = True
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=CONFIG_PATH.parent, delete=False) as tmp:
        json.dump(current, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        temp_name = tmp.name
    try:
        os.replace(temp_name, CONFIG_PATH)
    finally:
        try:
            Path(temp_name).unlink(missing_ok=True)
        except OSError:
            pass
    return current
