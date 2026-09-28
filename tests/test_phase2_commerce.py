"""Phase-2 commerce contracts: plan discounts, coupon models, migration and admin UI.

Environment note: the model/pricing assertions run against the real modules when
SQLAlchemy is importable. In the static-audit sandbox (no third-party wheels)
the pure pricing math is exercised by extracting the identical top-level
functions from app/services/pricing.py (ast) and the model registration is
verified structurally over the source. No live PostgreSQL is required.
"""
from __future__ import annotations

import ast
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:  # full environment: SQLAlchemy installed
    from app.db.models import Coupon, CouponRedemption, CouponTargetUser
    from app.services.pricing import discounted_amount, plan_stars_price, plan_toman_price

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

    _pricing = _extract_pure(
        (ROOT / "app" / "services" / "pricing.py").read_text(encoding="utf-8"),
        {"ONE", "HUNDRED", "normalize_percent", "discounted_amount",
         "plan_toman_price", "plan_stars_price"},
        {"Decimal": Decimal, "ROUND_HALF_UP": ROUND_HALF_UP, "Plan": None},
    )
    discounted_amount = _pricing["discounted_amount"]
    plan_stars_price = _pricing["plan_stars_price"]
    plan_toman_price = _pricing["plan_toman_price"]


def _plan(price_toman: Decimal, stars: int, discount: Decimal):
    if not _STATIC_FALLBACK:
        from app.db.models import Plan

        return Plan(price_toman=price_toman, features={"telegram_stars": stars}, discount_percent=discount)
    from types import SimpleNamespace

    return SimpleNamespace(
        price_toman=price_toman,
        discount_percent=discount,
        features={"telegram_stars": stars},
    )


def test_plan_discount_is_bounded_and_applied() -> None:
    plan = _plan(Decimal("200000"), 100, Decimal("15"))
    assert plan_toman_price(plan) == Decimal("170000")
    assert plan_stars_price(plan) == 85
    assert discounted_amount(Decimal("100000"), Decimal("100")) == Decimal("0")
    assert discounted_amount(Decimal("200000"), Decimal("0")) == Decimal("200000")


def test_coupon_models_registered() -> None:
    if _STATIC_FALLBACK:
        src = (ROOT / "app" / "db" / "models" / "commerce.py").read_text(encoding="utf-8-sig")
        for cls, table in (
            ("Coupon", "coupons"),
            ("CouponTargetUser", "coupon_target_users"),
            ("CouponRedemption", "coupon_redemptions"),
        ):
            assert re.search(rf"class {cls}\b", src), cls
            assert f'__tablename__ = "{table}"' in src, table
        return
    assert Coupon.__tablename__ == "coupons"
    assert CouponTargetUser.__tablename__ == "coupon_target_users"
    assert CouponRedemption.__tablename__ == "coupon_redemptions"


def test_phase2_migration_is_current_head() -> None:
    path = ROOT / "alembic" / "versions" / "0016_commerce_discounts.py"
    text = path.read_text(encoding="utf-8-sig")
    assert 'revision: str = "0016_commerce_discounts"' in text
    assert 'down_revision: Union[str, Sequence[str], None] = "0015_production_state"' in text


def test_admin_exposes_coupon_and_plan_discount_controls() -> None:
    admin = (ROOT / "app" / "api" / "admin.py").read_text(encoding="utf-8-sig")
    html = (ROOT / "app" / "api" / "templates" / "admin.html").read_text(encoding="utf-8-sig")
    assert 'router.get("/coupons"' in admin
    assert 'router.post("/coupons"' in admin
    assert 'discount_percent' in admin  # PlanCreate/PlanUpdate accept it
    # [G05-a] GET /plans exposes the real payable price computed by pricing.py:
    assert 'effective_price_toman' in admin
    assert 'plan_toman_price' in admin
    assert "کدهای تخفیف" in html
    assert "PaymentVerification" in html
