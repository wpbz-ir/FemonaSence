from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class StorageProvider(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "storage_providers"
    __table_args__ = (UniqueConstraint("code", name="uq_storage_providers_code"),)

    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    files: Mapped[list["StorageFile"]] = relationship("StorageFile", back_populates="provider")


class StorageFile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "storage_files"
    __table_args__ = (
        UniqueConstraint("provider_id", "file_unique_key", name="uq_storage_files_provider_unique"),
        Index("ix_storage_files_message", "chat_id", "message_id"),
        Index("ix_storage_files_status", "status"),
    )

    provider_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("storage_providers.id", ondelete="RESTRICT"), nullable=False
    )
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    file_id: Mapped[str | None] = mapped_column(Text)
    file_unique_key: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    extra_data: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="READY")
    storage_scope: Mapped[str] = mapped_column(String(24), nullable=False, default="PRODUCTION")
    verified_at: Mapped[datetime | None] = mapped_column(nullable=True)

    provider: Mapped["StorageProvider"] = relationship("StorageProvider", back_populates="files")
    release_files: Mapped[list["ReleaseFile"]] = relationship("ReleaseFile", back_populates="storage_file")


class Release(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "releases"
    __table_args__ = (
        CheckConstraint(
            "(title_id IS NOT NULL AND episode_id IS NULL) OR "
            "(title_id IS NULL AND episode_id IS NOT NULL)",
            name="single_parent",
        ),
        Index("ix_releases_title_status", "title_id", "status"),
        Index("ix_releases_episode_status", "episode_id", "status"),
        Index("ix_releases_lookup", "quality", "language", "subtitle_type", "status"),
    )

    title_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("titles.id", ondelete="CASCADE"))
    episode_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"))
    label: Mapped[str | None] = mapped_column(String(160))
    quality: Mapped[str] = mapped_column(String(32), nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    codec_video: Mapped[str | None] = mapped_column(String(64))
    codec_audio: Mapped[str | None] = mapped_column(String(64))
    container: Mapped[str | None] = mapped_column(String(16))
    fps: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    bitrate_kbps: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str | None] = mapped_column(String(64))
    dynamic_range: Mapped[str | None] = mapped_column(String(32))
    language: Mapped[str] = mapped_column(String(32), nullable=False, default="ORIGINAL")
    subtitle_type: Mapped[str] = mapped_column(String(32), nullable=False, default="NONE")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    technical_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    pipeline_key: Mapped[str | None] = mapped_column(String(220), nullable=True)

    title: Mapped["Title | None"] = relationship("Title")
    episode: Mapped["Episode | None"] = relationship("Episode")
    audio_tracks: Mapped[list["ReleaseAudioTrack"]] = relationship(
        "ReleaseAudioTrack", back_populates="release", cascade="all, delete-orphan"
    )
    subtitle_tracks: Mapped[list["ReleaseSubtitleTrack"]] = relationship(
        "ReleaseSubtitleTrack", back_populates="release", cascade="all, delete-orphan"
    )
    files: Mapped[list["ReleaseFile"]] = relationship(
        "ReleaseFile", back_populates="release", cascade="all, delete-orphan"
    )


class ReleaseAudioTrack(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "release_audio_tracks"
    __table_args__ = (
        UniqueConstraint("release_id", "language", "track_index", name="uq_release_audio_track"),
    )

    release_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("releases.id", ondelete="CASCADE"), nullable=False)
    language: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str | None] = mapped_column(String(100))
    codec: Mapped[str | None] = mapped_column(String(64))
    channels: Mapped[str | None] = mapped_column(String(16))
    track_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    release: Mapped["Release"] = relationship("Release", back_populates="audio_tracks")


class ReleaseSubtitleTrack(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "release_subtitle_tracks"
    __table_args__ = (
        UniqueConstraint("release_id", "language", "track_index", name="uq_release_subtitle_track"),
    )

    release_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("releases.id", ondelete="CASCADE"), nullable=False)
    language: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str | None] = mapped_column(String(100))
    format: Mapped[str | None] = mapped_column(String(16))
    track_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    release: Mapped["Release"] = relationship("Release", back_populates="subtitle_tracks")


class ReleaseFile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "release_files"
    __table_args__ = (
        UniqueConstraint("release_id", "storage_file_id", name="uq_release_file_link"),
    )

    release_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("releases.id", ondelete="CASCADE"), nullable=False)
    storage_file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("storage_files.id", ondelete="RESTRICT"), nullable=False
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    release: Mapped["Release"] = relationship("Release", back_populates="files")
    storage_file: Mapped["StorageFile"] = relationship("StorageFile", back_populates="release_files")


class TranscodeJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "transcode_jobs"
    __table_args__ = (
        Index("ix_transcode_jobs_status", "status"),
        Index("ix_transcode_jobs_source_file", "source_file_id"),
    )

    source_file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("storage_files.id", ondelete="RESTRICT"), nullable=False
    )
    target_release_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("releases.id", ondelete="SET NULL")
    )
    target_quality: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="QUEUED")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    extra_data: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class AccessPolicy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "access_policies"
    __table_args__ = (
        UniqueConstraint("release_id", name="uq_access_policies_release"),
    )

    release_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("releases.id", ondelete="CASCADE"), nullable=False)
    access_type: Mapped[str] = mapped_column(String(32), nullable=False, default="PUBLIC")
    requires_subscription: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    minimum_plan_rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    extra_data: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class MembershipChannel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "membership_channels"
    __table_args__ = (UniqueConstraint("telegram_chat_id", name="uq_membership_channels_chat"),)

    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    invite_url: Mapped[str | None] = mapped_column(Text)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class MembershipRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "membership_rules"
    __table_args__ = (
        CheckConstraint(
            "(title_id IS NOT NULL) OR (release_id IS NOT NULL)",
            name="target",
        ),
        UniqueConstraint(
            "membership_channel_id", "title_id", "release_id", name="uq_membership_rule_target"
        ),
    )

    membership_channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("membership_channels.id", ondelete="CASCADE"), nullable=False
    )
    title_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("titles.id", ondelete="CASCADE"))
    release_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("releases.id", ondelete="CASCADE"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
