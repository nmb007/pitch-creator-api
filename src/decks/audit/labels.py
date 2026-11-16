"""Forbidden titles and banner lines used by the deck audit."""

from __future__ import annotations

from src.decks.validate import DATE_LINE

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
    return normalize_label(text) in FORBIDDEN_TITLES or is_audit_banner(text)


def is_field_heading(text: str) -> bool:
    return normalize_label(text) in FORBIDDEN_TITLES


def is_audit_banner(text: str) -> bool:
    """Banners for audit, including Edly and Axim footers the import still misses."""
    lowered = text.strip().lower()
    if not lowered:
        return False
    if lowered in {
        "www.arbisoft.com",
        "confidential",
        "arbisoft case studies",
        "axim proposals",
        "edly.io",
    }:
        return True
    if lowered.startswith("arbisoft case studies"):
        return True
    if "edly.io" in lowered:
        return True
    if lowered.startswith("arbisoft proposal"):
        return True
    if lowered.startswith("edly proposal"):
        return True
    if "valid till" in lowered or lowered.startswith("proposal valid"):
        return True
    return bool(DATE_LINE.match(text.strip()))
