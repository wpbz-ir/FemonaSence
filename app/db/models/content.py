from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


title_genres = Table(
    "title_genres",
    Base.metadata,
    Column("title_id", ForeignKey("titles.id", ondelete="CASCADE"), primary_key=True),
    Column("genre_id", ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True),
)

title_countries = Table(
    "title_countries",
    Base.metadata,
    Column("title_id", ForeignKey("titles.id", ondelete="CASCADE"), primary_key=True),
    Column("country_id", ForeignKey("countries.id", ondelete="CASCADE"), primary_key=True),
)

title_people = Table(
    "title_people",
    Base.metadata,
    Column("title_id", ForeignKey("titles.id", ondelete="CASCADE"), primary_key=True),
    Column("person_id", ForeignKey("people.id", ondelete="CASCADE"), primary_key=True),
    Column("role", String(32), primary_key=True),
)

title_collections = Table(
    "title_collections",
    Base.metadata,
    Column("title_id", ForeignKey("titles.id", ondelete="CASCADE"), primary_key=True),
    Column("collection_id", ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True),
)


class Title(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "titles"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_titles_slug"),
        Index("ix_titles_kind", "kind"),
        Index("ix_titles_release_year", "release_year"),
        Index("ix_titles_imdb_rating", "imdb_rating"),
        Index("ix_titles_status", "status"),
    )

    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # MOVIE | SERIES
    title_fa: Mapped[str] = mapped_column(String(255), nullable=False)
    title_en: Mapped[str | None] = mapped_column(String(255))
    original_title: Mapped[str | None] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(280), nullable=False)
    synopsis: Mapped[str | None] = mapped_column(Text)
    release_year: Mapped[int | None] = mapped_column(Integer)
    runtime_minutes: Mapped[int | None] = mapped_column(Integer)
    imdb_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    imdb_rating: Mapped[float | None] = mapped_column()
    imdb_votes: Mapped[int | None] = mapped_column(Integer)
    user_rating: Mapped[float | None] = mapped_column()
    poster_url: Mapped[str | None] = mapped_column(Text)
    backdrop_url: Mapped[str | None] = mapped_column(Text)
    trailer_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    published_at: Mapped[datetime | None] = mapped_column(nullable=True)
    rights_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rights_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)

    series: Mapped["Series | None"] = relationship(
        "Series", back_populates="title", uselist=False, cascade="all, delete-orphan"
    )
    genres: Mapped[list["Genre"]] = relationship(secondary=title_genres, back_populates="titles")
    countries: Mapped[list["Country"]] = relationship(secondary=title_countries, back_populates="titles")
    people: Mapped[list["Person"]] = relationship(secondary=title_people, back_populates="titles")
    collections: Mapped[list["Collection"]] = relationship(
        secondary=title_collections, back_populates="titles"
    )


class Series(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "series"

    title_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("titles.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    total_seasons: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ongoing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    title: Mapped["Title"] = relationship("Title", back_populates="series")
    seasons: Mapped[list["Season"]] = relationship(
        "Season", back_populates="series", cascade="all, delete-orphan", order_by="Season.season_number"
    )


class Season(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "seasons"
    __table_args__ = (UniqueConstraint("series_id", "season_number", name="uq_seasons_series_number"),)

    series_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), nullable=False)
    season_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    synopsis: Mapped[str | None] = mapped_column(Text)

    series: Mapped["Series"] = relationship("Series", back_populates="seasons")
    episodes: Mapped[list["Episode"]] = relationship(
        "Episode", back_populates="season", cascade="all, delete-orphan", order_by="Episode.episode_number"
    )


class Episode(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "episodes"
    __table_args__ = (UniqueConstraint("season_id", "episode_number", name="uq_episodes_season_number"),)

    season_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"), nullable=False)
    episode_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    synopsis: Mapped[str | None] = mapped_column(Text)
    runtime_minutes: Mapped[int | None] = mapped_column(Integer)
    air_date: Mapped[datetime | None] = mapped_column(nullable=True)

    season: Mapped["Season"] = relationship("Season", back_populates="episodes")


class Genre(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "genres"
    __table_args__ = (UniqueConstraint("slug", name="uq_genres_slug"),)

    name_fa: Mapped[str] = mapped_column(String(100), nullable=False)
    name_en: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    titles: Mapped[list["Title"]] = relationship(secondary=title_genres, back_populates="genres")


class Country(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "countries"
    __table_args__ = (UniqueConstraint("code", name="uq_countries_code"),)

    name_fa: Mapped[str] = mapped_column(String(100), nullable=False)
    name_en: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(String(8), nullable=False)

    titles: Mapped[list["Title"]] = relationship(secondary=title_countries, back_populates="countries")


class Person(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "people"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_people_slug"),
        Index("ix_people_name_en", "name_en"),
    )

    name_fa: Mapped[str | None] = mapped_column(String(160))
    name_en: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(180), nullable=False)
    biography: Mapped[str | None] = mapped_column(Text)
    profile_url: Mapped[str | None] = mapped_column(Text)

    titles: Mapped[list["Title"]] = relationship(secondary=title_people, back_populates="people")


class Collection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "collections"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_collections_slug"),
        Index("ix_collections_parent_id", "parent_id"),
    )

    name_fa: Mapped[str] = mapped_column(String(160), nullable=False)
    name_en: Mapped[str | None] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    poster_url: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("collections.id", ondelete="SET NULL")
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    parent: Mapped["Collection | None"] = relationship(
        "Collection", remote_side="Collection.id", back_populates="children"
    )
    children: Mapped[list["Collection"]] = relationship(
        "Collection", back_populates="parent", cascade="all, delete-orphan"
    )
    titles: Mapped[list["Title"]] = relationship(secondary=title_collections, back_populates="collections")
