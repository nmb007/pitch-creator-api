"""Pitch request and response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class PitchResponse(BaseModel):
    id: UUID
    status: str
    prospect_text: str
    created_at: datetime
    updated_at: datetime


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
