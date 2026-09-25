from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from app.db.models import Plan

ONE = Decimal("1")
HUNDRED = Decimal("100")


def normalize_percent(value: Decimal | int | float | str | None) -> Decimal:
    raw = Decimal(str(value or 0))
    return min(Decimal("100"), max(Decimal("0"), raw))


def discounted_amount(amount: Decimal | int | str, percent: Decimal | int | float | str | None) -> Decimal:
    base = max(Decimal("0"), Decimal(str(amount or 0)))
    pct = normalize_percent(percent)
    return (base * (HUNDRED - pct) / HUNDRED).quantize(ONE, rounding=ROUND_HALF_UP)


def plan_toman_price(plan: Plan) -> Decimal:
    return discounted_amount(plan.price_toman, getattr(plan, "discount_percent", 0))


def plan_stars_price(plan: Plan) -> int:
    features = plan.features or {}
    base = int(features.get("telegram_stars", features.get("stars", 0)) or 0)
    return max(0, int(discounted_amount(base, getattr(plan, "discount_percent", 0))))
