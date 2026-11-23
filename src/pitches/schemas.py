"""Pitch request and response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class PitchResponse(BaseModel):
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime
    has_matches: bool = False


class MatchResponse(BaseModel):
    rank: int
    reason: str
    source_quote: str
    section: str
    title: str
    study_id: UUID
    selected: bool


class MessageResponse(BaseModel):
    id: UUID
    role: str
    body: str
    created_at: datetime


class FollowUpMessage(BaseModel):
    message: str = Field(min_length=1)
