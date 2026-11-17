"""Accept title anchors and field block numbers against Docling blocks."""

from __future__ import annotations

import logging
import re
from typing import Any

from src.decks.blocks import SlideBlocks, TextBlock
from src.decks.drafts import FIELD_NAMES, StudyDraft

logger = logging.getLogger(__name__)

_EMPTY_NAMES = {"none", "null", "n/a", "na", "unknown", "unnamed", "not found", ""}

DATE_LINE = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2},\s+\d{4}$",
    re.IGNORECASE,
)


class StructureValidationError(ValueError):
    """Model output does not match the blocks. Fail the whole import."""


def titles_from_payload(
    payload: dict[str, Any],
    blocks_by_number: dict[int, TextBlock],
    *,
    batch_numbers: set[int],
    allowed_extra: set[int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    """Return accepted sections, accepted titles, and an optional held wrap title."""
    sections_raw = payload.get("sections")
    titles_raw = payload.get("titles")
    if not isinstance(sections_raw, list):
        raise StructureValidationError("JSON sections must be a list")
    if not isinstance(titles_raw, list):
        raise StructureValidationError("JSON titles must be a list")

    allowed = set(batch_numbers) | (allowed_extra or set())
    sections: list[dict[str, Any]] = []
    for item in sections_raw:
        section = _accept_section(item, blocks_by_number)
        if section is not None:
            sections.append(section)

    titles: list[dict[str, Any]] = []
    held: dict[str, Any] | None = None
    for item in titles_raw:
        if not isinstance(item, dict):
            raise StructureValidationError("Each title must be an object")
        accepted, partial = _accept_title(
            item,
            blocks_by_number,
            allowed_numbers=allowed,
            batch_numbers=batch_numbers,
        )
        if accepted:
            titles.extend(accepted)
            # A completed title ends a previous wrap hold; a new trailing
            # partial later in this loop may set held again.
            held = None
        elif partial is not None:
            held = partial
    return sections, titles, held


def apply_fields_payload(
    payload: dict[str, Any],
    *,
    title: str,
    section: str,
    title_block_numbers: list[int],
    span_numbers: list[int],
    blocks_by_number: dict[int, TextBlock],
    slides_by_number: dict[int, SlideBlocks],
) -> StudyDraft:
    """Copy field text from Docling using the block numbers the model returned."""
    if not isinstance(payload, dict):
        raise StructureValidationError("Field JSON must be an object")
    span_set = set(span_numbers)
    fields: dict[str, str] = {}
    for name in FIELD_NAMES:
        numbers = _int_list(payload.get(name), name)
        kept = [n for n in _unique_ints(numbers) if n in span_set and n in blocks_by_number]
        ignored = [n for n in numbers if n not in span_set]
        if ignored:
            logger.warning(
                "Ignoring out-of-span %s block(s) %s for %r",
                name,
                ignored,
                title,
            )
        fields[name] = "\n".join(
            blocks_by_number[n].text.strip()
            for n in kept
            if blocks_by_number[n].text.strip()
        )
    raw_text = build_raw_text(
        span_numbers,
        title,
        blocks_by_number,
        title_block_numbers=title_block_numbers,
    )
    slide_numbers = sorted(
        {
            blocks_by_number[n].slide
            for n in span_numbers
            if n in blocks_by_number
        }
    )
    images = []
    for number in slide_numbers:
        slide = slides_by_number.get(number)
        if slide is not None:
            images.extend(slide.images)
    source = "\n".join(
        blocks_by_number[n].text for n in span_numbers if n in blocks_by_number
    )
    return StudyDraft(
        section=section or "General",
        title=title,
        client_name=accept_client_name(str(payload.get("client_name") or ""), source),
        raw_text=raw_text,
        background=fields["background"],
        challenge=fields["challenge"],
        approach=fields["approach"],
        solution=fields["solution"],
        outcome=fields["outcome"],
        tech_stack=fields["tech_stack"],
        slide_numbers=slide_numbers,
        images=images,
    )


def build_raw_text(
    block_numbers: list[int],
    title: str,
    blocks_by_number: dict[int, TextBlock],
    *,
    title_block_numbers: list[int] | None = None,
) -> str:
    """Join the study body blocks. Title and running banners are left out."""
    lines: list[str] = []
    title_compact = " ".join(title.split())
    title_set = set(title_block_numbers or [])
    for number in block_numbers:
        block = blocks_by_number.get(number)
        if block is None:
            continue
        if number in title_set:
            continue
        text = block.text.strip()
        if not text or _is_banner(text):
            continue
        if text == title or " ".join(text.split()) == title_compact:
            continue
        lines.append(text)
    return "\n".join(lines)


def accept_client_name(reply: str, source: str) -> str | None:
    line = reply.splitlines()[0].strip().strip("\"'`").strip() if reply else ""
    if not line or line.lower().rstrip(".") in _EMPTY_NAMES:
        return None
    if len(line) > 300 or line.lower() == "arbisoft":
        return None
    if line.lower() not in source.lower():
        return None
    return line


def is_banner(text: str) -> bool:
    return _is_banner(text)


def _accept_section(
    item: Any,
    blocks_by_number: dict[int, TextBlock],
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        raise StructureValidationError("Each section must be an object")
    try:
        number = int(item.get("block_number"))
    except (TypeError, ValueError) as exc:
        raise StructureValidationError("section block_number must be an integer") from exc
    block = blocks_by_number.get(number)
    if block is None:
        logger.warning("Skipping section; unknown block %s", number)
        return None
    if _is_banner(block.text):
        logger.warning("Skipping section; block %s is a banner", number)
        return None
    name = block.text.strip()
    if not name:
        logger.warning("Skipping section; block %s is empty", number)
        return None
    return {"name": name, "block_number": number}


def _accept_title(
    item: dict[str, Any],
    blocks_by_number: dict[int, TextBlock],
    *,
    allowed_numbers: set[int],
    batch_numbers: set[int],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    numbers = _unique_ints(_int_list(item.get("block_numbers"), "block_numbers"))
    if not numbers:
        logger.warning("Skipping a title with no block_numbers")
        return [], None
    if len(numbers) > 2:
        logger.warning("Skipping title; expected 1 or 2 block_numbers, got %s", numbers)
        return [], None
    for number in numbers:
        if number not in blocks_by_number:
            raise StructureValidationError(
                f"Title references unknown block {number}"
            )
        if number not in allowed_numbers:
            logger.warning(
                "Skipping title; block %s is outside the current window",
                number,
            )
            return [], None
    if len(numbers) == 2 and not _blocks_are_adjacent(numbers, blocks_by_number):
        logger.warning("Splitting title; block_numbers %s are not adjacent", numbers)
        return _split_title(numbers, blocks_by_number), None
    if any(_is_banner(blocks_by_number[n].text) for n in numbers):
        logger.warning("Skipping title; marked banner block(s) %s", numbers)
        return [], None

    incomplete = bool(item.get("incomplete"))
    # First line of a wrapped title; second line is on a later slide.
    if incomplete and len(numbers) == 1 and numbers[0] in batch_numbers:
        return [], {"block_numbers": numbers, "incomplete": True}

    title = _title_from_numbers(numbers, blocks_by_number)
    if title is None:
        return [], None
    return [title], None


def _split_title(
    numbers: list[int],
    blocks_by_number: dict[int, TextBlock],
) -> list[dict[str, Any]]:
    """Two non-adjacent blocks are two headlines, not one wrapped title."""
    split: list[dict[str, Any]] = []
    for number in numbers:
        if _is_banner(blocks_by_number[number].text):
            logger.warning("Skipping title; marked banner block(s) [%s]", number)
            continue
        title = _title_from_numbers([number], blocks_by_number)
        if title is not None:
            split.append(title)
    return split


def _title_from_numbers(
    numbers: list[int],
    blocks_by_number: dict[int, TextBlock],
) -> dict[str, Any] | None:
    title = " ".join(blocks_by_number[n].text.strip() for n in numbers).strip()
    if not title:
        logger.warning("Skipping title; block(s) %s are empty", numbers)
        return None
    return {"title": title, "block_numbers": numbers}


def _blocks_are_adjacent(
    numbers: list[int],
    blocks_by_number: dict[int, TextBlock],
) -> bool:
    ordered = sorted(blocks_by_number)
    try:
        first = ordered.index(numbers[0])
        second = ordered.index(numbers[1])
    except ValueError:
        return False
    return second == first + 1


def _int_list(value: Any, label: str) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise StructureValidationError(f"{label} must be a list")
    result: list[int] = []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError) as exc:
            raise StructureValidationError(f"{label} must contain integers") from exc
    return result


def _unique_ints(values: list[int]) -> list[int]:
    seen: set[int] = set()
    ordered: list[int] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _is_banner(text: str) -> bool:
    """Running header or footer. These lines are not study body and not raw_text."""
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
    if "edly.io" in lowered:
        return True
    if lowered.startswith("arbisoft proposal") or lowered.startswith("edly proposal"):
        return True
    if "valid till" in lowered or lowered.startswith("proposal valid"):
        return True
    return bool(DATE_LINE.match(text.strip()))
