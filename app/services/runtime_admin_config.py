from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.getenv("CINEMAVAULT_RUNTIME_DIR", str(PROJECT_ROOT / "data" / "runtime"))) / "admin_modules.json"

DEFAULTS = {
    "dashboard": {"enabled": True, "show_service_status": True, "allow_encoding_scan": True},
    "content": {"enabled": True, "show_technical_fields": False, "films": True, "series": True, "animations": True},
    "taxonomy": {"enabled": True, "genres": True, "people": True, "countries": True, "collections": True, "years": True},
    "series": {"enabled": True, "manage_seasons": True, "manage_episodes": True},
    "releases": {"enabled": True, "allow_status_change": True, "allow_storage_view": True},
    "users": {"enabled": True, "show_financial_history": True, "show_activity_history": True, "allow_manual_subscription": True, "allow_account_status": True, "allow_account_delete": True},
    "subscriptions": {"enabled": True, "allow_discount": True, "allow_media": True},
    "payments": {"enabled": True, "allow_gateway_settings": True, "allow_gateway_test": True},
    "matrix": {"enabled": True, "allow_advanced_options": True, "allow_auto_publish": False},
    "jobs": {"enabled": True, "allow_retry": True, "allow_cancel": True},
    "audit": {"enabled": True, "allow_encoding_repair": True},
}



def load_module_settings() -> dict:
    value = json.loads(json.dumps(DEFAULTS))
    if not CONFIG_PATH.exists():
        return value
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return value
    if not isinstance(raw, dict):
        return value
    for module, config in value.items():
        if isinstance(raw.get(module), dict):
            for key in config:
                if key in raw[module]:
                    config[key] = bool(raw[module][key])
    return value


def save_module_settings(value: dict) -> dict:
    current = load_module_settings()
    for module, config in current.items():
        incoming = value.get(module, {}) if isinstance(value, dict) else {}
        if isinstance(incoming, dict):
            for key in config:
                if key in incoming:
                    config[key] = bool(incoming[key])
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
