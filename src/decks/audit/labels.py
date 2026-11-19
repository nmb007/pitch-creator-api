"""Forbidden titles and banner lines used by the deck audit."""

from __future__ import annotations

from src.decks.validate import NON_CONTENT_LABELS, is_banner

FORBIDDEN_TITLES = frozenset(
    {
        "the challenge",
        "the approach",
        "the outcome",
        "background",
        "challenges",
        "challenge",
        "approach",
        "outcome",
        "solution",
        "solutions",
        "technology stack",
        "the technology stack",
        "tech stack",
        *NON_CONTENT_LABELS,
    }
)


def normalize_label(text: str) -> str:
    return text.strip().rstrip(":").strip().lower()


def is_forbidden_title(text: str) -> bool:
    return normalize_label(text) in FORBIDDEN_TITLES or is_banner(text)


def is_field_heading(text: str) -> bool:
    return normalize_label(text) in FORBIDDEN_TITLES
