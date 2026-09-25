"""deduplicated Telegram webhook update ledger

Revision ID: 0013_telegram_webhook
Revises: 0012_notification_runtime
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0013_telegram_webhook"
down_revision: Union[str, Sequence[str], None] = "0012_notification_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_updates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("update_id", sa.BigInteger(), nullable=False),
        sa.Column("bot_scope", sa.String(64), nullable=False, server_default="default"),
        sa.Column("status", sa.String(24), nullable=False, server_default="RECEIVED"),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.UniqueConstraint("update_id", "bot_scope", name="uq_telegram_updates_update_scope"),
    )
    op.create_index("ix_telegram_updates_status_received", "telegram_updates", ["status", "received_at"])


def downgrade() -> None:
    op.drop_index("ix_telegram_updates_status_received", table_name="telegram_updates")
    op.drop_table("telegram_updates")
