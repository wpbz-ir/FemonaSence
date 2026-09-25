"""hashed media access tokens and revocation metadata

Revision ID: 0008_secure_playback
Revises: 0007_admin_operations
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008_secure_playback"
down_revision: Union[str, Sequence[str], None] = "0007_admin_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.add_column("media_access_tokens", sa.Column("token_hash", sa.String(length=64), nullable=True))
    op.add_column("media_access_tokens", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("media_access_tokens", sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("media_access_tokens", sa.Column("last_used_ip", sa.String(length=64), nullable=True))
    op.add_column("media_access_tokens", sa.Column("last_used_user_agent", sa.String(length=255), nullable=True))
    op.execute(
        """
        UPDATE media_access_tokens
        SET token_hash = encode(digest(token, 'sha256'), 'hex')
        WHERE token_hash IS NULL
        """
    )
    op.execute("UPDATE media_access_tokens SET token = NULL WHERE token_hash IS NOT NULL")
    op.create_index("uq_media_access_tokens_token_hash", "media_access_tokens", ["token_hash"], unique=True,
                    postgresql_where=sa.text("token_hash IS NOT NULL"))
    op.alter_column("media_access_tokens", "token", existing_type=sa.String(length=96), nullable=True)


def downgrade() -> None:
    raise RuntimeError("0008_secure_playback is intentionally irreversible after token hashing.")
