"""plans duration_days >= 1 check constraint (FIX-B)

Revision ID: 0018
Revises: 0017_ads_system
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0018"
down_revision: Union[str, Sequence[str], None] = "0017_ads_system"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # [FIX-B] A 0-day plan produced a PAID zero-length subscription and a
    # negative duration SHORTENED an active subscription on settle
    # (expires_at = starts_at + timedelta(days=duration_days)). Block such
    # plans at the DB level; the settle paths (billing.settle_star_payment /
    # winapay_billing.settle_winapay_order) also re-check as a backstop.
    op.create_check_constraint("ck_plans_duration_days", "plans", "duration_days >= 1")


def downgrade() -> None:
    op.drop_constraint("ck_plans_duration_days", "plans", type_="check")
