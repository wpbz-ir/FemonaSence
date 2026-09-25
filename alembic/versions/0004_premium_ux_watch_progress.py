"""premium UX, watch progress and access metadata

Revision ID: 0004_premium_ux_watch_progress
Revises: 0003_experience_secure_media
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_premium_ux_watch_progress"
down_revision = "0003_experience_secure_media"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "watch_progress",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("release_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("position_seconds", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("duration_seconds", sa.Numeric(12, 3), nullable=True),
        sa.Column("completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_watched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["title_id"], ["titles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["release_id"], ["releases.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("user_id", "title_id", name="uq_watch_progress_user_title"),
        sa.CheckConstraint("position_seconds >= 0", name="ck_watch_progress_position_nonnegative"),
        sa.CheckConstraint("duration_seconds IS NULL OR duration_seconds > 0", name="ck_watch_progress_duration_positive"),
    )
    op.create_index("ix_watch_progress_user_last_watched", "watch_progress", ["user_id", "last_watched_at"])
    op.create_index("ix_watch_progress_title", "watch_progress", ["title_id"])

    op.add_column("media_access_tokens", sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_media_access_tokens_user_expires", "media_access_tokens", ["user_id", "expires_at"])


def downgrade() -> None:
    op.drop_index("ix_media_access_tokens_user_expires", table_name="media_access_tokens")
    op.drop_column("media_access_tokens", "last_used_at")
    op.drop_index("ix_watch_progress_title", table_name="watch_progress")
    op.drop_index("ix_watch_progress_user_last_watched", table_name="watch_progress")
    op.drop_table("watch_progress")
