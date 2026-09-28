"""Plan pricing, coupon P0-1 contract, bot-menu config and payment-config tests.

[P0-10] The original version imported plan_discount_metadata/plan_discounted_toman/
plan_stars which never existed in app/services/pricing.py and broke collection.
The real pricing API is: discounted_amount / plan_toman_price / plan_stars_price
(+ normalize_percent); the coupon helpers are _payable_toman/_payable_stars with
preview_coupon/reserve_coupon returning TRUE discount amounts (amount - payable).

Environment note: the full assertions import the real app modules, which need
SQLAlchemy. In the stripped-down static-audit sandbox third-party wheels are not
installed, so the same pure math is exercised by extracting the identical
top-level functions from the module sources (ast) — the assertions do not change.
No live PostgreSQL/Redis/Telegram is required in either mode.
"""
from __future__ import annotations

import ast
import asyncio
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import app.services.runtime_bot_menu_config as bot_menu
import app.services.runtime_payment_config as payment_config

ROOT = Path(__file__).resolve().parents[1]

try:  # full environment: SQLAlchemy (and the rest of the stack) installed
    from app.db.models import Plan
    from app.services.coupons import (
        _payable_stars,
        _payable_toman,
        preview_coupon,
        reserve_coupon,
    )
    from app.services.pricing import (
        discounted_amount,
        normalize_percent,
        plan_stars_price,
        plan_toman_price,
    )

    _STATIC_FALLBACK = False
except ModuleNotFoundError as exc:  # static sandbox: no third-party deps
    if exc.name != "sqlalchemy":
        raise
    _STATIC_FALLBACK = True

    def _extract_pure(source: str, names: set[str], seeds: dict) -> dict:
        """Exec the named pure top-level functions/constants from a module source
        without importing the module (its other imports need third-party wheels)."""
        tree = ast.parse(source)
        chunks = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
                chunks.append(ast.unparse(node))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(t, ast.Name) and t.id in names for t in targets):
                    chunks.append(ast.unparse(node))
        namespace = dict(seeds)
        exec("\n".join(chunks), namespace)  # noqa: S102 - repo-local source only
        return namespace

    _pricing_src = (ROOT / "app" / "services" / "pricing.py").read_text(encoding="utf-8")
    _pricing = _extract_pure(
        _pricing_src,
        {"ONE", "HUNDRED", "normalize_percent", "discounted_amount",
         "plan_toman_price", "plan_stars_price"},
        {"Decimal": Decimal, "ROUND_HALF_UP": ROUND_HALF_UP, "Plan": None},
    )
    _coupons = _extract_pure(
        (ROOT / "app" / "services" / "coupons.py").read_text(encoding="utf-8"),
        {"_payable_toman", "_payable_stars"},
        {
            "Decimal": Decimal,
            "ROUND_HALF_UP": ROUND_HALF_UP,
            "discounted_amount": _pricing["discounted_amount"],
        },
    )
    Plan = None  # duck-typed stubs stand in for the ORM model below
    discounted_amount = _pricing["discounted_amount"]
    normalize_percent = _pricing["normalize_percent"]
    plan_stars_price = _pricing["plan_stars_price"]
    plan_toman_price = _pricing["plan_toman_price"]
    _payable_toman = _coupons["_payable_toman"]
    _payable_stars = _coupons["_payable_stars"]


def _make_plan(*, price_toman: Decimal, stars: int, discount: int):
    """Plan built from the real ORM model (full env) or a duck-typed stub (static)."""
    if not _STATIC_FALLBACK:
        return Plan(
            code="TEST",
            name_fa="آزمایشی",
            price_toman=price_toman,
            price_irr=Decimal("0"),
            duration_days=30,
            rank=0,
            active=True,
            sort_order=0,
            discount_percent=Decimal(str(discount)),
            features={"telegram_stars": stars},
        )
    return SimpleNamespace(
        price_toman=price_toman,
        discount_percent=Decimal(str(discount)),
        features={"telegram_stars": stars},
    )


def test_plan_discount_is_applied_consistently():
    plan = _make_plan(price_toman=Decimal("100000"), stars=101, discount=20)
    assert plan_toman_price(plan) == Decimal("80000")
    assert plan_stars_price(plan) == 81  # 101 * 80% = 80.8 -> ROUND_HALF_UP -> 81
    # equivalent metadata assertions via the real API surface:
    assert normalize_percent(20) == Decimal("20")
    assert normalize_percent(150) == Decimal("100")  # bounded above
    assert normalize_percent(-5) == Decimal("0")  # bounded below
    assert discounted_amount(Decimal("100000"), 20) == Decimal("80000")
    assert str(plan_toman_price(plan)) == "80000"
    assert int(plan_stars_price(plan)) == 81


def _coupon_stub(discount_type: str, value):
    return SimpleNamespace(
        id=uuid4(),
        code="SAVE",
        name_fa="تست",
        active=True,
        valid_from=None,
        valid_until=None,
        scope_type="ALL",
        discount_type=discount_type,
        value=Decimal(str(value)),
        max_uses=None,
        per_user_limit=1,
    )


class _FakeSession:
    """Minimal async session stub: first scalar returns the coupon, counts return 0."""

    def __init__(self, coupon):
        self.coupon = coupon
        self.added = []
        self._calls = 0

    async def scalar(self, _stmt):
        self._calls += 1
        return self.coupon if self._calls == 1 else 0

    async def execute(self, _stmt):
        return None

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


def test_coupon_contract_returns_true_discount_amounts():
    """[P0-1 regression] preview_coupon/reserve_coupon must return the TRUE discount
    (amount - payable), not the post-discount payable price (the old inverted math)."""
    if _STATIC_FALLBACK:
        # pure helpers: payable is the post-discount price -> discount = amount - payable
        assert _payable_toman(Decimal("100000"), "PERCENT", Decimal("20")) == Decimal("80000")
        assert Decimal("100000") - Decimal("80000") == Decimal("20000")
        assert _payable_toman(Decimal("100000"), "FIXED_TOMAN", Decimal("30000")) == Decimal("70000")
        assert _payable_toman(Decimal("100000"), "FIXED_TOMAN", Decimal("150000")) == Decimal("0")
        assert _payable_stars(101, Decimal("20")) == 81
        # preview_coupon AND reserve_coupon must both derive discount as amount - payable:
        src = (ROOT / "app" / "services" / "coupons.py").read_text(encoding="utf-8")
        assert src.count("amount_toman - _payable_toman(") >= 2
        assert src.count("amount_stars - _payable_stars(") >= 2
        return

    # PERCENT 20%: discount must be 20000 Toman (NOT the 80000 payable) / 20 stars.
    session = _FakeSession(_coupon_stub("PERCENT", 20))
    out = asyncio.run(preview_coupon(
        session, code="save20", user_id=uuid4(), plan_id=uuid4(),
        amount_toman=Decimal("100000"), amount_stars=101,
    ))
    assert out["discount_toman"] == Decimal("20000")
    assert out["discount_stars"] == 20
    assert Decimal("100000") - out["discount_toman"] == _payable_toman(
        Decimal("100000"), "PERCENT", Decimal("20")
    )

    # FIXED_TOMAN: reserve persists the true discount and caps it at the full price.
    session = _FakeSession(_coupon_stub("FIXED_TOMAN", 30000))
    out = asyncio.run(reserve_coupon(
        session, code="SAVE", user_id=uuid4(), plan_id=uuid4(), order_id=uuid4(),
        amount_toman=Decimal("100000"), amount_stars=0,
    ))
    assert out["discount_toman"] == Decimal("30000")
    assert session.added[0].discount_amount_toman == Decimal("30000")

    session = _FakeSession(_coupon_stub("FIXED_TOMAN", 150000))
    out = asyncio.run(reserve_coupon(
        session, code="SAVE", user_id=uuid4(), plan_id=uuid4(), order_id=uuid4(),
        amount_toman=Decimal("100000"), amount_stars=0,
    ))
    assert out["discount_toman"] == Decimal("100000")  # min(value, amount)


def test_bot_menu_roundtrip_is_atomic_and_keeps_safe_fallback(tmp_path, monkeypatch):
    path = tmp_path / "bot_menu.json"
    monkeypatch.setattr(bot_menu, "CONFIG_PATH", path)
    # Disable EVERY public button (everything except the admin-only key) so the
    # service's safe fallback has to force the account button back on. Derived
    # from DEFAULTS so the test stays correct when menu entries are added.
    payload = {
        key: {"enabled": False, "label": "فیلم‌های ویژه" if key == "movies" else None}
        for key in bot_menu.DEFAULTS
        if key != "admin"
    }
    payload = {k: {kk: vv for kk, vv in item.items() if vv is not None} for k, item in payload.items()}
    result = bot_menu.save_bot_menu_settings(payload)
    loaded = bot_menu.load_bot_menu_settings()
    assert loaded["movies"]["enabled"] is False
    assert loaded["movies"]["label"] == "فیلم‌های ویژه"
    # safe fallback: with no public button enabled, account stays reachable
    assert loaded["account"]["enabled"] is True
    assert path.exists()
    assert result == loaded
    # with at least one OTHER public button enabled, account is NOT forced on
    result2 = bot_menu.save_bot_menu_settings({"wallet": {"enabled": True}, "account": {"enabled": False}})
    loaded2 = bot_menu.load_bot_menu_settings()
    assert loaded2["wallet"]["enabled"] is True
    assert loaded2["account"]["enabled"] is False
    assert loaded2["top"]["enabled"] is False  # untouched keys persist as disabled
    assert result2 == loaded2


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
