"""Shared study draft shapes used by parse and persist."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

FIELD_NAMES = ("background", "challenge", "approach", "solution", "outcome", "tech_stack")


class _ChunkSource(Protocol):
    raw_text: str
    background: str
    challenge: str
    approach: str
    solution: str
    outcome: str
    tech_stack: str


def chunk_texts(source: _ChunkSource) -> list[str]:
    """Non-empty fields in field order, otherwise raw_text, otherwise a placeholder."""
    parts = [getattr(source, name) for name in FIELD_NAMES if getattr(source, name)]
    if not parts:
        parts = [source.raw_text] if source.raw_text else ["Untitled study"]
    return parts


def image_role(index: int, client_name: str | None) -> str:
    """First picture is the client logo when the study names a client."""
    if index == 0 and client_name:
        return "logo"
    return "other"


@dataclass
class ImageDraft:
    data: bytes
    ext: str


@dataclass
class StudyDraft:
    section: str
    title: str
    client_name: str | None
    raw_text: str
    background: str
    challenge: str
    approach: str
    solution: str
    outcome: str
    tech_stack: str
    slide_numbers: list[int] = field(default_factory=list)
    images: list[ImageDraft] = field(default_factory=list)
