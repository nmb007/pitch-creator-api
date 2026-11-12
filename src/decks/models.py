"""Deck and study persistence models."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Column, DateTime
from sqlalchemy.types import JSON
from sqlmodel import Field

from src.models import TimestampModel, UUIDModel, utcnow
from src.vectors import Vector384


class DeckImport(UUIDModel, table=True):
    __tablename__ = "deck_imports"

    uploaded_by: UUID = Field(foreign_key="users.id")
    file_name: str = Field(max_length=500)
    sha256: str = Field(max_length=64)
    imported_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),
    )
    status: str = Field(default="queued", max_length=32)


class Study(UUIDModel, TimestampModel, table=True):
    __tablename__ = "studies"

    section: str = Field(max_length=300)
    title: str = Field(max_length=500)
    client_name: str | None = Field(default=None, max_length=300)
    match_key: str = Field(unique=True, max_length=800)
    status: str = Field(default="active", max_length=32)


class StudyVersion(UUIDModel, table=True):
    __tablename__ = "study_versions"

    study_id: UUID = Field(foreign_key="studies.id")
    deck_import_id: UUID = Field(foreign_key="deck_imports.id")
    slide_numbers: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    content_hash: str = Field(max_length=64)
    background: str = Field(default="")
    challenge: str = Field(default="")
    approach: str = Field(default="")
    solution: str = Field(default="")
    outcome: str = Field(default="")
    tech_stack: str = Field(default="")
    raw_text: str = Field(default="")
    summary_embedding: list[float] = Field(sa_column=Column(Vector384()))
    is_current: bool = Field(default=True)


class StudyChunk(UUIDModel, table=True):
    __tablename__ = "study_chunks"

    study_version_id: UUID = Field(foreign_key="study_versions.id")
    position: int
    text: str
    embedding: list[float] = Field(sa_column=Column(Vector384()))


class StudyImage(UUIDModel, table=True):
    __tablename__ = "study_images"

    study_version_id: UUID = Field(foreign_key="study_versions.id")
    role: str = Field(max_length=16)
    storage_key: str = Field(max_length=500)
    sha256: str = Field(max_length=64)


class BrandAsset(UUIDModel, table=True):
    __tablename__ = "brand_assets"

    name: str = Field(unique=True, max_length=200)
    storage_key: str = Field(max_length=500)
