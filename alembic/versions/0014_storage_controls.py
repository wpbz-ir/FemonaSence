"""storage scope and provider health metadata

Revision ID: 0014_storage_controls
Revises: 0013_telegram_webhook
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0014_storage_controls"
down_revision: Union[str, Sequence[str], None] = "0013_telegram_webhook"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("storage_files", sa.Column("storage_scope", sa.String(length=24), nullable=False, server_default="PRODUCTION"))
    op.add_column("storage_files", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_storage_files_scope_status", "storage_files", ["storage_scope", "status"])


def downgrade() -> None:
    op.drop_index("ix_storage_files_scope_status", table_name="storage_files")
    op.drop_column("storage_files", "verified_at")
    op.drop_column("storage_files", "storage_scope")
