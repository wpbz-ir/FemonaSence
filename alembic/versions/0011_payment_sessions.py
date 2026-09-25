"""production payment sessions and wallet/top-up settlement fields

Revision ID: 0011_payment_sessions
Revises: 0010_production_content_controls
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0011_payment_sessions"
down_revision: Union[str, Sequence[str], None] = "0010_production_content_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plans", sa.Column("price_toman", sa.Numeric(18, 2), nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("amount_toman", sa.Numeric(18, 2), nullable=True))
    op.add_column("payment_attempts", sa.Column("requested_amount_toman", sa.Numeric(18, 2), nullable=True))
    op.add_column("payments", sa.Column("amount_toman", sa.Numeric(18, 2), nullable=True))
    op.create_table(
        "payment_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False, server_default="SUBSCRIPTION"),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_payment_sessions_token_hash"),
    )
    op.create_index("ix_payment_sessions_user_created", "payment_sessions", ["user_id", "created_at"])
    op.create_index("ix_payment_sessions_expires_at", "payment_sessions", ["expires_at"])

    op.create_table(
        "payment_webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("event_key", sa.String(255), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="RECEIVED"),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("provider", "event_key", name="uq_payment_webhook_provider_event"),
    )
    op.create_index("ix_payment_webhook_events_order_created", "payment_webhook_events", ["order_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_payment_webhook_events_order_created", table_name="payment_webhook_events")
    op.drop_table("payment_webhook_events")
    op.drop_index("ix_payment_sessions_expires_at", table_name="payment_sessions")
    op.drop_index("ix_payment_sessions_user_created", table_name="payment_sessions")
    op.drop_table("payment_sessions")
    op.drop_column("payments", "amount_toman")
    op.drop_column("payment_attempts", "requested_amount_toman")
    op.drop_column("orders", "amount_toman")
    op.drop_column("plans", "price_toman")
