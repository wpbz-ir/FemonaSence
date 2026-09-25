from decimal import Decimal

from app.db.models import Plan
import app.services.runtime_bot_menu_config as bot_menu
import app.services.runtime_payment_config as payment_config
from app.services.pricing import plan_discount_metadata, plan_discounted_toman, plan_stars


def test_plan_discount_is_applied_consistently():
    plan = Plan(
        code="TEST",
        name_fa="آزمایشی",
        price_toman=Decimal("100000"),
        price_irr=Decimal("0"),
        duration_days=30,
        rank=0,
        active=True,
        sort_order=0,
        features={"telegram_stars": 101, "discount_percent": 20, "discount_text": "پیشنهاد ویژه"},
    )
    assert plan_discounted_toman(plan) == Decimal("80000")
    assert plan_stars(plan) == 81
    meta = plan_discount_metadata(plan)
    assert meta["discount_percent"] == 20
    assert meta["discounted_toman"] == "80000"
    assert meta["discounted_stars"] == 81


def test_bot_menu_roundtrip_is_atomic_and_keeps_safe_fallback(tmp_path, monkeypatch):
    path = tmp_path / "bot_menu.json"
    monkeypatch.setattr(bot_menu, "CONFIG_PATH", path)
    result = bot_menu.save_bot_menu_settings({
        "movies": {"enabled": False, "label": "فیلم‌های ویژه"},
        "series": {"enabled": False},
        "animation": {"enabled": False},
        "popular": {"enabled": False},
        "new": {"enabled": False},
        "imdb": {"enabled": False},
        "years": {"enabled": False},
        "genres": {"enabled": False},
        "collections": {"enabled": False},
        "actors": {"enabled": False},
        "search": {"enabled": False},
        "favorites": {"enabled": False},
        "history": {"enabled": False},
        "account": {"enabled": False},
        "subscription": {"enabled": False},
        "wallet": {"enabled": False},
    })
    loaded = bot_menu.load_bot_menu_settings()
    assert loaded["movies"]["enabled"] is False
    assert loaded["movies"]["label"] == "فیلم‌های ویژه"
    assert loaded["account"]["enabled"] is True
    assert path.exists()
    assert result == loaded


def test_payment_config_public_view_never_exposes_merchant(tmp_path, monkeypatch):
    monkeypatch.setattr(payment_config, "CONFIG_PATH", tmp_path / "payment_gateway.json")
    monkeypatch.setattr(payment_config, "_defaults", lambda: {
        "enabled": False,
        "provider": "WINAPAY",
        "sandbox": True,
        "merchant_id": "SECRET-MERCHANT",
        "base_url": "https://winapay.io/webservice/rest",
        "timeout_seconds": 30,
    })
    payment_config.save_payment_config({
        "enabled": True,
        "sandbox": True,
        "merchant_id": "SECRET-MERCHANT",
        "base_url": "https://winapay.io/webservice/rest",
        "timeout_seconds": 30,
    })
    public = payment_config.public_payment_config()
    assert "merchant_id" not in public
    assert public["merchant_id_configured"] is True
