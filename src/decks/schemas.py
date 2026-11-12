"""Deck request and response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class DeckResponse(BaseModel):
    id: UUID
    file_name: str
    status: str
    imported_at: datetime
    studies_added: int
    studies_updated: int
    studies_hidden: int


class StudyResponse(BaseModel):
    id: UUID
    section: str
    title: str
    client_name: str | None
    status: str
