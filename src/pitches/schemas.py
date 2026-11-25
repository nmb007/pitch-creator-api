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
    has_page: bool = False


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


class SelectionRequest(BaseModel):
    study_ids: list[UUID] = Field(min_length=1)


class PageStudyResponse(BaseModel):
    study_id: UUID
    rank: int
    section: str
    title: str
    background: str = ""
    challenge: str = ""
    approach: str = ""
    solution: str = ""
    outcome: str = ""
    tech_stack: str = ""
    # Only filled when every structured section above is empty.
    raw_text: str = ""


class PageResponse(BaseModel):
    title: str
    body: str
    studies: list[PageStudyResponse]


class PageUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)
