"""add persistent Telegram delivery expiration tracking

Revision ID: 0002_deliveries
Revises: 0001_initial
Create Date: 2026-09-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_deliveries"
down_revision: Union[str, Sequence[str], None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "deliveries",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("release_id", sa.UUID(as_uuid=True), sa.ForeignKey("releases.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
        sa.Column("delete_error", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_deliveries_expiry_status",
        "deliveries",
        ["expires_at", "status"],
    )
    op.create_index(
        "ix_deliveries_user_created",
        "deliveries",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_deliveries_user_created", table_name="deliveries")
    op.drop_index("ix_deliveries_expiry_status", table_name="deliveries")
    op.drop_table("deliveries")
