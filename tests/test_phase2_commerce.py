from decimal import Decimal
from pathlib import Path

from app.db.models import Coupon, CouponRedemption, CouponTargetUser, Plan
from app.services.pricing import discounted_amount, plan_stars_price, plan_toman_price

ROOT = Path(__file__).resolve().parents[1]


def test_plan_discount_is_bounded_and_applied() -> None:
    plan = Plan(price_toman=Decimal("200000"), features={"telegram_stars": 100}, discount_percent=Decimal("15"))
    assert plan_toman_price(plan) == Decimal("170000")
    assert plan_stars_price(plan) == 85
    assert discounted_amount(Decimal("100000"), Decimal("100")) == Decimal("0")


def test_coupon_models_registered() -> None:
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
    assert 'discount_percent' in admin
    assert "کدهای تخفیف" in html
    assert "PaymentVerification" in html
