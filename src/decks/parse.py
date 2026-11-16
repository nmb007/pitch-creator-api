"""Turn the master PowerPoint into study drafts via Docling and a local model."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.config import settings
from src.decks.blocks import SlideBlocks, TextBlock, read_pptx_blocks
from src.decks.drafts import FIELD_NAMES, ImageDraft, StudyDraft
from src.decks.structure_model import (
    build_field_message,
    build_title_message,
    structure_fields,
    structure_titles,
)
from src.decks.validate import (
    StructureValidationError,
    apply_fields_payload,
    is_banner,
    titles_from_payload,
)

logger = logging.getLogger(__name__)

StructureFn = Callable[[str], dict[str, Any]]

__all__ = [
    "FIELD_NAMES",
    "ImageDraft",
    "StructureValidationError",
    "StudyDraft",
    "parse_pptx",
]


def parse_pptx(
    path: Path,
    *,
    complete: StructureFn | None = None,
    complete_fields: StructureFn | None = None,
) -> list[StudyDraft]:
    """Read blocks with Docling, mark titles, span studies, then fill fields.

    ``complete`` is the title-pass model call. ``complete_fields`` is the
    per-study field call. When only ``complete`` is passed, it handles both
    message shapes (tests). Production uses the two GGUF helpers.
    """
    logger.info("Parse: reading blocks from %s", path)
    slides = read_pptx_blocks(path)
    if not slides:
        logger.info("Parse: no slides found")
        return []
    blocks_by_number = {
        block.number: block for slide in slides for block in slide.blocks
    }
    slides_by_number = {slide.number: slide for slide in slides}
    title_runner = complete or structure_titles
    if complete_fields is not None:
        field_runner = complete_fields
    elif complete is not None:
        field_runner = complete
    else:
        field_runner = structure_fields
    logger.info(
        "Parse: marking titles across %s slide(s) with batch size %s",
        len(slides),
        max(1, settings.STRUCTURE_BATCH_SLIDES),
    )
    anchors = _collect_title_anchors(slides, blocks_by_number, title_runner)
    logger.info("Parse: building %s study span(s)", len(anchors))
    drafts = _drafts_from_anchors(
        anchors,
        blocks_by_number,
        slides_by_number,
        field_runner,
    )
    merged = _replace_same_key(drafts)
    logger.info("Parse: done with %s draft(s)", len(merged))
    return merged


def _collect_title_anchors(
    slides: list[SlideBlocks],
    blocks_by_number: dict[int, TextBlock],
    complete: StructureFn,
) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    section_marks: list[tuple[int, str]] = []
    section = "General"
    held: dict[str, Any] | None = None
    index = 0
    batch_size = max(1, settings.STRUCTURE_BATCH_SLIDES)
    total_slides = len(slides)
    model_calls = 0

    while index < len(slides):
        if held is None:
            window = slides[index : index + batch_size]
            index += len(window)
        else:
            window = [slides[index]]
            index += 1

        block_rows = [
            (block.number, block.slide, block.text)
            for slide in window
            for block in slide.blocks
        ]
        if not block_rows and held is None:
            continue
        batch_numbers = {number for number, _, _ in block_rows}
        slide_nos = [slide.number for slide in window]
        logger.info(
            "Parse: title call on slide(s) %s (through %s/%s)%s",
            ",".join(str(n) for n in slide_nos),
            min(index, total_slides),
            total_slides,
            " [wrap hold]" if held is not None else "",
        )
        message = build_title_message(
            section=section,
            blocks=block_rows,
            held_title=held,
        )
        payload = complete(message)
        model_calls += 1
        held_extra = (
            {int(n) for n in held.get("block_numbers") or []}
            if held is not None
            else set()
        )
        sections, titles, next_held = titles_from_payload(
            payload,
            blocks_by_number,
            batch_numbers=batch_numbers,
            allowed_extra=held_extra,
        )
        for item in sections:
            section_marks.append((int(item["block_number"]), str(item["name"])))
            section = str(item["name"])
            logger.info(
                "Parse: section %r at block %s",
                item["name"],
                item["block_number"],
            )
        for item in titles:
            title_start = min(int(n) for n in item["block_numbers"])
            anchors.append(
                {
                    "title": str(item["title"]),
                    "block_numbers": [int(n) for n in item["block_numbers"]],
                    "start": title_start,
                }
            )
            logger.info(
                "Parse: title %r at block(s) %s",
                item["title"],
                item["block_numbers"],
            )
        if next_held is None and held is not None and not titles:
            # Hold ended with neither a completed wrap nor another title.
            logger.info(
                "Parse: dropping unfinished wrapped title at block(s) %s",
                held.get("block_numbers"),
            )
        held = next_held
        if held is not None:
            logger.info(
                "Parse: holding wrapped title at block(s) %s for the next slide",
                held.get("block_numbers"),
            )

    if held is not None:
        logger.info(
            "Parse: dropping unfinished wrapped title at block(s) %s at end of deck",
            held.get("block_numbers"),
        )

    anchors.sort(key=lambda item: item["start"])
    section_marks.sort(key=lambda item: item[0])
    for anchor in anchors:
        anchor["section"] = _section_for(anchor["start"], section_marks)
    logger.info(
        "Parse: title pass done with %s title(s) after %s call(s)",
        len(anchors),
        model_calls,
    )
    return anchors


def _section_for(block_number: int, section_marks: list[tuple[int, str]]) -> str:
    current = "General"
    for mark_block, name in section_marks:
        if mark_block > block_number:
            break
        current = name
    return current


def _drafts_from_anchors(
    anchors: list[dict[str, Any]],
    blocks_by_number: dict[int, TextBlock],
    slides_by_number: dict[int, SlideBlocks],
    complete_fields: StructureFn,
) -> list[StudyDraft]:
    ordered = sorted(blocks_by_number)
    drafts: list[StudyDraft] = []
    for index, anchor in enumerate(anchors):
        start = int(anchor["start"])
        title_numbers = [int(n) for n in anchor["block_numbers"]]
        next_start = int(anchors[index + 1]["start"]) if index + 1 < len(anchors) else None
        span_numbers = _span_block_numbers(ordered, start, next_start)
        if not span_numbers:
            continue
        body_blocks = [
            (number, blocks_by_number[number].slide, blocks_by_number[number].text)
            for number in span_numbers
            if number not in title_numbers
            and not is_banner(blocks_by_number[number].text)
        ]
        message = build_field_message(
            section=str(anchor["section"]),
            title=str(anchor["title"]),
            title_block_numbers=title_numbers,
            body_blocks=body_blocks,
        )
        logger.info(
            "Parse: field call for %r (%s body block(s))",
            anchor["title"],
            len(body_blocks),
        )
        payload = complete_fields(message)
        draft = apply_fields_payload(
            payload,
            title=str(anchor["title"]),
            section=str(anchor["section"]),
            title_block_numbers=title_numbers,
            span_numbers=span_numbers,
            blocks_by_number=blocks_by_number,
            slides_by_number=slides_by_number,
        )
        drafts.append(draft)
        logger.info(
            "Parse: finished study %r (section=%s, slides=%s)",
            draft.title,
            draft.section,
            draft.slide_numbers,
        )
    return drafts


def _span_block_numbers(
    ordered: list[int],
    start: int,
    next_start: int | None,
) -> list[int]:
    if start not in ordered:
        return []
    begin = ordered.index(start)
    if next_start is None:
        return ordered[begin:]
    if next_start not in ordered:
        return ordered[begin:]
    end = ordered.index(next_start)
    return ordered[begin:end]


def _replace_same_key(drafts: list[StudyDraft]) -> list[StudyDraft]:
    """Keep one draft per section+title. A later copy replaces the earlier one."""
    merged: dict[str, StudyDraft] = {}
    order: list[str] = []
    for draft in drafts:
        key = f"{draft.section}\n{draft.title}"
        if key not in merged:
            order.append(key)
        else:
            logger.info(
                "Parse: replacing earlier study %r (section=%s) with a later copy",
                draft.title,
                draft.section,
            )
        merged[key] = draft
    return [merged[key] for key in order]
