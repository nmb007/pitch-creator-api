"""Colored, readable ingest progress lines for worker logs."""

from __future__ import annotations

import logging
import os
from enum import StrEnum
from uuid import UUID

logger = logging.getLogger(__name__)

_RESET = "\033[0m"


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
    IngestPhase.READ: "\033[36m",
    IngestPhase.OUTLINE: "\033[33m",
    IngestPhase.TITLES: "\033[33m",
    IngestPhase.FIELDS: "\033[35m",
    IngestPhase.PERSIST: "\033[34m",
    IngestPhase.EMBED: "\033[96m",
    IngestPhase.HIDE: "\033[90m",
    IngestPhase.DONE: "\033[32m",
    IngestPhase.FAILED: "\033[31m",
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


def ingest_info(deck_id: UUID | str, phase: IngestPhase, message: str) -> None:
    tag = import_tag(deck_id)
    line = f"Import {tag}: {message}"
    if not os.environ.get("NO_COLOR"):
        color = _PHASE_COLOR.get(phase, "")
        if color:
            line = f"{color}{line}{_RESET}"
    logger.info(line)
