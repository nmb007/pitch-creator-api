"""Forbidden titles and banner lines used by the deck audit."""

from __future__ import annotations

from src.decks.validate import is_banner

FORBIDDEN_TITLES = frozenset(
    {
        "the challenge",
        "the approach",
        "the outcome",
        "background",
        "challenges",
        "technology stack",
        "the technology stack",
        "tech stack",
        "general format",
        "what the company is?",
        "what we did?",
    }
)


def normalize_label(text: str) -> str:
    return text.strip().rstrip(":").strip().lower()


def is_forbidden_title(text: str) -> bool:
    return normalize_label(text) in FORBIDDEN_TITLES or is_banner(text)


def is_field_heading(text: str) -> bool:
    return normalize_label(text) in FORBIDDEN_TITLES

