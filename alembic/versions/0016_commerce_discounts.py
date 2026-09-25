"""plans discounts and coupon campaigns

Revision ID: 0016_commerce_discounts
Revises: 0015_production_state
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0016_commerce_discounts"
down_revision: Union[str, Sequence[str], None] = "0015_production_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plans", sa.Column("discount_percent", sa.Numeric(5, 2), nullable=False, server_default="0"))
    op.create_check_constraint(
        "ck_plans_discount_percent",
        "plans",
        "discount_percent >= 0 AND discount_percent <= 100",
    )

    op.create_table(
        "coupons",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name_fa", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("discount_type", sa.String(length=16), nullable=False, server_default="PERCENT"),
        sa.Column("value", sa.Numeric(18, 2), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("per_user_limit", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("scope_type", sa.String(length=16), nullable=False, server_default="ALL"),
        sa.Column("scope_plan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["scope_plan_id"], ["plans.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("code", name="uq_coupons_code"),
        sa.CheckConstraint("discount_type IN ('PERCENT','FIXED_TOMAN')", name="ck_coupons_discount_type"),
        sa.CheckConstraint("value >= 0", name="ck_coupons_value_nonnegative"),
        sa.CheckConstraint("per_user_limit >= 1", name="ck_coupons_per_user_limit"),
        sa.CheckConstraint("max_uses IS NULL OR max_uses >= 1", name="ck_coupons_max_uses"),
        sa.CheckConstraint("scope_type IN ('ALL','PLAN','USERS')", name="ck_coupons_scope_type"),
    )
    op.create_index("ix_coupons_active_dates", "coupons", ["active", "valid_from", "valid_until"])
    op.create_index("ix_coupons_scope_plan", "coupons", ["scope_plan_id", "active"])

    op.create_table(
        "coupon_target_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("coupon_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["coupon_id"], ["coupons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("coupon_id", "user_id", name="uq_coupon_target_user"),
    )
    op.create_index("ix_coupon_target_users_user", "coupon_target_users", ["user_id"])

    op.create_table(
        "coupon_redemptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("coupon_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="RESERVED"),
        sa.Column("discount_amount_toman", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("discount_amount_stars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["coupon_id"], ["coupons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("coupon_id", "order_id", name="uq_coupon_redemption_order"),
        sa.CheckConstraint("status IN ('RESERVED','REDEEMED','RELEASED')", name="ck_coupon_redemptions_status"),
    )
    op.create_index("ix_coupon_redemptions_coupon_status", "coupon_redemptions", ["coupon_id", "status"])
    op.create_index("ix_coupon_redemptions_user_status", "coupon_redemptions", ["user_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_coupon_redemptions_user_status", table_name="coupon_redemptions")
    op.drop_index("ix_coupon_redemptions_coupon_status", table_name="coupon_redemptions")
    op.drop_table("coupon_redemptions")
    op.drop_index("ix_coupon_target_users_user", table_name="coupon_target_users")
    op.drop_table("coupon_target_users")
    op.drop_index("ix_coupons_scope_plan", table_name="coupons")
    op.drop_index("ix_coupons_active_dates", table_name="coupons")
    op.drop_table("coupons")
    op.drop_constraint("ck_plans_discount_percent", "plans", type_="check")
    op.drop_column("plans", "discount_percent")
