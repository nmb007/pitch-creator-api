"""Shared study draft shapes used by parse and persist."""

from __future__ import annotations

from dataclasses import dataclass, field

FIELD_NAMES = ("background", "challenge", "approach", "solution", "outcome", "tech_stack")


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
