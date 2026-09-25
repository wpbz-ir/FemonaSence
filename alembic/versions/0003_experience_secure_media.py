"""experience, engagement and secure media access

Revision ID: 0003_experience_secure_media
Revises: 0002_deliveries
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003_experience_secure_media"
down_revision = "0002_deliveries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_reactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("value", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["title_id"], ["titles.id"], ondelete="CASCADE"),
        sa.CheckConstraint("value IN (-1, 1)", name="ck_content_reactions_value"),
        sa.UniqueConstraint("user_id", "title_id", name="uq_content_reactions_user_title"),
    )
    op.create_index("ix_content_reactions_title", "content_reactions", ["title_id"])

    op.create_table(
        "content_comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PUBLISHED"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["title_id"], ["titles.id"], ondelete="CASCADE"),
        sa.CheckConstraint("status IN ('PUBLISHED', 'HIDDEN', 'DELETED')", name="ck_content_comments_status"),
    )
    op.create_index("ix_content_comments_title_status_created", "content_comments", ["title_id", "status", "created_at"])

    op.create_table(
        "media_access_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("token", sa.String(96), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["release_id"], ["releases.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token", name="uq_media_access_tokens_token"),
        sa.CheckConstraint("action IN ('STREAM', 'PREVIEW')", name="ck_media_access_tokens_action"),
    )
    op.create_index("ix_media_access_tokens_release_expires", "media_access_tokens", ["release_id", "expires_at"])

    op.create_table(
        "watch_parties",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("host_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invite_token", sa.String(96), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("current_position_seconds", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("is_playing", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["host_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["title_id"], ["titles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["release_id"], ["releases.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("invite_token", name="uq_watch_parties_invite_token"),
        sa.CheckConstraint("status IN ('ACTIVE', 'CLOSED', 'EXPIRED')", name="ck_watch_parties_status"),
    )
    op.create_index("ix_watch_parties_release_expires", "watch_parties", ["release_id", "expires_at"])

    op.create_table(
        "watch_party_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("party_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["party_id"], ["watch_parties.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("party_id", "user_id", name="uq_watch_party_members_party_user"),
    )
    op.create_index("ix_watch_party_members_party", "watch_party_members", ["party_id"])


def downgrade() -> None:
    op.drop_index("ix_watch_party_members_party", table_name="watch_party_members")
    op.drop_table("watch_party_members")
    op.drop_index("ix_watch_parties_release_expires", table_name="watch_parties")
    op.drop_table("watch_parties")
    op.drop_index("ix_media_access_tokens_release_expires", table_name="media_access_tokens")
    op.drop_table("media_access_tokens")
    op.drop_index("ix_content_comments_title_status_created", table_name="content_comments")
    op.drop_table("content_comments")
    op.drop_index("ix_content_reactions_title", table_name="content_reactions")
    op.drop_table("content_reactions")
