"""production content controls and release publishing guards

Revision ID: 0010_production_content_controls
Revises: 0009_production_hardening
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0010_production_content_controls"
down_revision: Union[str, Sequence[str], None] = "0009_production_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("titles", sa.Column("rights_verified", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("titles", sa.Column("rights_reference", sa.String(length=255), nullable=True))
    op.create_index("ix_titles_rights_verified_status", "titles", ["rights_verified", "status"])


def downgrade() -> None:
    op.drop_index("ix_titles_rights_verified_status", table_name="titles")
    op.drop_column("titles", "rights_reference")
    op.drop_column("titles", "rights_verified")
