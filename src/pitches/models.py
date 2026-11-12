"""Pitch persistence models. Week 2 tables are created now so later work does not reshape them."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime
from sqlmodel import Field

from src.models import TimestampModel, UUIDModel, utcnow


class Pitch(UUIDModel, TimestampModel, table=True):
    __tablename__ = "pitches"

    created_by: UUID = Field(foreign_key="users.id")
    status: str = Field(default="collecting", max_length=32)
    prospect_text: str = Field(default="")
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),
    )


class PitchInput(UUIDModel, TimestampModel, table=True):
    __tablename__ = "pitch_inputs"

    pitch_id: UUID = Field(foreign_key="pitches.id", unique=True)
    kind: str = Field(max_length=16)
    body: str | None = None
    file_name: str | None = Field(default=None, max_length=500)
    storage_key: str | None = Field(default=None, max_length=500)
    created_by: UUID = Field(foreign_key="users.id")


class PitchMessage(UUIDModel, TimestampModel, table=True):
    __tablename__ = "pitch_messages"

    pitch_id: UUID = Field(foreign_key="pitches.id")
    role: str = Field(max_length=16)
    body: str


class PitchMatch(UUIDModel, table=True):
    __tablename__ = "pitch_matches"

    pitch_id: UUID = Field(foreign_key="pitches.id")
    study_id: UUID = Field(foreign_key="studies.id")
    study_version_id: UUID = Field(foreign_key="study_versions.id")
    rank: int
    reason: str
    source_quote: str
    selected: bool = Field(default=False)


class PitchRevision(UUIDModel, TimestampModel, table=True):
    __tablename__ = "pitch_revisions"

    pitch_id: UUID = Field(foreign_key="pitches.id")
    source: str = Field(max_length=16)
    created_by: UUID = Field(foreign_key="users.id")


class PitchPage(UUIDModel, table=True):
    __tablename__ = "pitch_pages"

    revision_id: UUID = Field(foreign_key="pitch_revisions.id")
    title: str = Field(default="")
    background: str = Field(default="")
    challenges: str = Field(default="")
    solution: str = Field(default="")
    key_highlights: str = Field(default="")
    impact: str = Field(default="")
    technology: str = Field(default="")
    prospect_logo_storage_key: str | None = Field(default=None, max_length=500)


class PitchPdf(UUIDModel, TimestampModel, table=True):
    __tablename__ = "pitch_pdfs"

    revision_id: UUID = Field(foreign_key="pitch_revisions.id")
    storage_key: str = Field(max_length=500)
