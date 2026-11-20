"""Colored, readable ingest progress lines for worker logs."""

from __future__ import annotations

import logging
import os
from enum import StrEnum
from uuid import UUID

logger = logging.getLogger(__name__)

_RESET = "\033[0m"
# Bold blue: readable on light and dark terminal backgrounds.
_READ_COLOR = "\033[1;34m"
# Red for validation failures, retries, and other problem lines.
_ALERT_COLOR = "\033[31m"


class IngestPhase(StrEnum):
    READ = "read"
    OUTLINE = "outline"
    # Older log lines; keep so existing tests and greps still resolve.
    TITLES = "titles"
    FIELDS = "fields"
    PERSIST = "persist"
    EMBED = "embed"
    HIDE = "hide"
    DONE = "done"
    FAILED = "failed"


_PHASE_COLOR: dict[IngestPhase, str] = {
    IngestPhase.READ: _READ_COLOR,
    IngestPhase.OUTLINE: "\033[33m",
    IngestPhase.TITLES: "\033[33m",
    IngestPhase.FIELDS: "\033[35m",
    IngestPhase.PERSIST: "\033[34m",
    IngestPhase.EMBED: "\033[96m",
    IngestPhase.HIDE: "\033[90m",
    IngestPhase.DONE: "\033[32m",
    IngestPhase.FAILED: _ALERT_COLOR,
}


def import_tag(deck_id: UUID | str) -> str:
    return str(deck_id).replace("-", "")[:8]


def slide_range(slide_numbers: list[int]) -> str:
    if not slide_numbers:
        return ""
    if len(slide_numbers) == 1:
        return str(slide_numbers[0])
    return f"{slide_numbers[0]}–{slide_numbers[-1]}"


def block_phrase(block_numbers: list[int]) -> str:
    if len(block_numbers) == 1:
        return f"block {block_numbers[0]}"
    joined = " and ".join(str(number) for number in block_numbers)
    return f"blocks {joined}"


def _color_enabled() -> bool:
    return not os.environ.get("NO_COLOR")


def ingest_info(deck_id: UUID | str, phase: IngestPhase, message: str) -> None:
    tag = import_tag(deck_id)
    line = f"Import {tag}: {message}"
    if _color_enabled():
        color = _PHASE_COLOR.get(phase, "")
        if color:
            line = f"{color}{line}{_RESET}"
    logger.info(line)


def ingest_alert(deck_id: UUID | str, message: str) -> None:
    """Log a problem line (reject, retry, title-miss) in red so it stands out."""
    tag = import_tag(deck_id)
    line = f"Import {tag}: {message}"
    if _color_enabled():
        line = f"{_ALERT_COLOR}{line}{_RESET}"
    logger.info(line)
