"""ads system and continue-watching support

Revision ID: 0017_ads_system
Revises: 0016_commerce_discounts
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0017_ads_system"
down_revision: Union[str, Sequence[str], None] = "0016_commerce_discounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ad_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("singleton", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("channel_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("channel_username", sa.String(length=255), nullable=True),
        sa.Column("rates_text", sa.Text(), nullable=True),
        sa.Column("instructions_text", sa.Text(), nullable=True),
        sa.Column("contact_text", sa.Text(), nullable=True),
        sa.Column("auto_channel_publish", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint("singleton", name="uq_ad_settings_singleton"),
    )

    op.create_table(
        "ad_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content_type", sa.String(length=32), nullable=False, server_default="TEXT"),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("telegram_file_id", sa.String(length=255), nullable=True),
        sa.Column("source_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("source_message_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("admin_note", sa.Text(), nullable=True),
        sa.Column("published_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("published_message_id", sa.BigInteger(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_ad_requests_status_created", "ad_requests", ["status", "created_at"])
    op.create_index("ix_ad_requests_user", "ad_requests", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_ad_requests_user", table_name="ad_requests")
    op.drop_index("ix_ad_requests_status_created", table_name="ad_requests")
    op.drop_table("ad_requests")
    op.drop_table("ad_settings")
