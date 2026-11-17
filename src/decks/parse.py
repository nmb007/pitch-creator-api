"""Turn the master PowerPoint into study drafts via Docling and a local model."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from src.config import settings
from src.decks.blocks import SlideBlocks, TextBlock, read_pptx_blocks
from src.decks.drafts import FIELD_NAMES, ImageDraft, StudyDraft
from src.decks.ingest_log import (
    IngestPhase,
    block_phrase,
    ingest_info,
    slide_range,
)
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
    deck_id: UUID | None = None,
    complete: StructureFn | None = None,
    complete_fields: StructureFn | None = None,
) -> list[StudyDraft]:
    """Read blocks with Docling, mark titles, span studies, then fill fields.

    ``complete`` is the title-pass model call. ``complete_fields`` is the
    per-study field call. When only ``complete`` is passed, it handles both
    message shapes (tests). Production uses the two GGUF helpers.
    """
    slides = read_pptx_blocks(path, deck_id=deck_id)
    if not slides:
        if deck_id is not None:
            ingest_info(deck_id, IngestPhase.READ, "finished reading. No slides found.")
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
    batch_size = max(1, settings.STRUCTURE_BATCH_SLIDES)
    if deck_id is not None:
        ingest_info(
            deck_id,
            IngestPhase.TITLES,
            f"marking titles. {len(slides)} slides, {batch_size} slides per call.",
        )
    anchors = _collect_title_anchors(
        slides,
        blocks_by_number,
        title_runner,
        log_id=deck_id,
    )
    drafts = _drafts_from_anchors(
        anchors,
        blocks_by_number,
        slides_by_number,
        field_runner,
        log_id=deck_id,
    )
    return _replace_same_key(drafts, log_id=deck_id)


def _collect_title_anchors(
    slides: list[SlideBlocks],
    blocks_by_number: dict[int, TextBlock],
    complete: StructureFn,
    *,
    log_id: UUID | str | None,
) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    section_marks: list[tuple[int, str]] = []
    section = "General"
    held: dict[str, Any] | None = None
    index = 0
    batch_size = max(1, settings.STRUCTURE_BATCH_SLIDES)
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
        model_calls += 1
        wrap = " (completing a wrapped title)" if held is not None else ""
        if log_id is not None:
            ingest_info(
                log_id,
                IngestPhase.TITLES,
                f"title call {model_calls}, slides {slide_range(slide_nos)}{wrap}.",
            )
        message = build_title_message(
            section=section,
            blocks=block_rows,
            held_title=held,
        )
        payload = complete(message)
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
        if log_id is not None:
            if titles:
                ingest_info(
                    log_id,
                    IngestPhase.TITLES,
                    f"Found {len(titles)} title{'s' if len(titles) != 1 else ''}.",
                )
            else:
                ingest_info(log_id, IngestPhase.TITLES, "Found no titles.")
        for item in sections:
            section_marks.append((int(item["block_number"]), str(item["name"])))
            section = str(item["name"])
            if log_id is not None:
                ingest_info(
                    log_id,
                    IngestPhase.TITLES,
                    f'found section "{item["name"]}" at block {item["block_number"]}.',
                )
        for item in titles:
            title_start = min(int(n) for n in item["block_numbers"])
            numbers = [int(n) for n in item["block_numbers"]]
            anchors.append(
                {
                    "title": str(item["title"]),
                    "block_numbers": numbers,
                    "start": title_start,
                }
            )
            if log_id is not None:
                ingest_info(
                    log_id,
                    IngestPhase.TITLES,
                    f'found title "{item["title"]}" at {block_phrase(numbers)}.',
                )
        if next_held is None and held is not None and not titles and log_id is not None:
            ingest_info(
                log_id,
                IngestPhase.TITLES,
                f"dropped an unfinished wrapped title at "
                f"{block_phrase(list(held.get('block_numbers') or []))}.",
            )
        held = next_held
        if held is not None and log_id is not None:
            ingest_info(
                log_id,
                IngestPhase.TITLES,
                f"holding a wrapped title at "
                f"{block_phrase(list(held.get('block_numbers') or []))} for the next slide.",
            )

    if held is not None and log_id is not None:
        ingest_info(
            log_id,
            IngestPhase.TITLES,
            f"dropped an unfinished wrapped title at end of deck at "
            f"{block_phrase(list(held.get('block_numbers') or []))}.",
        )

    anchors.sort(key=lambda item: item["start"])
    section_marks.sort(key=lambda item: item[0])
    for anchor in anchors:
        anchor["section"] = section_for(anchor["start"], section_marks)
    if log_id is not None:
        ingest_info(
            log_id,
            IngestPhase.TITLES,
            f"finished marking titles. {len(anchors)} case studies found from "
            f"{model_calls} calls.",
        )
    return anchors


def section_for(block_number: int, section_marks: list[tuple[int, str]]) -> str:
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
    *,
    log_id: UUID | str | None,
) -> list[StudyDraft]:
    ordered = sorted(blocks_by_number)
    drafts: list[StudyDraft] = []
    total = len(anchors)
    if log_id is not None and total:
        ingest_info(
            log_id,
            IngestPhase.FIELDS,
            f"filling fields for {total} case studies.",
        )
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
        if log_id is not None:
            ingest_info(
                log_id,
                IngestPhase.FIELDS,
                f'field call {index + 1} of {total}, "{anchor["title"]}".',
            )
        message = build_field_message(
            section=str(anchor["section"]),
            title=str(anchor["title"]),
            title_block_numbers=title_numbers,
            body_blocks=body_blocks,
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
    if log_id is not None:
        ingest_info(
            log_id,
            IngestPhase.FIELDS,
            f"finished filling fields for {len(drafts)} case studies.",
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


def _replace_same_key(
    drafts: list[StudyDraft],
    *,
    log_id: UUID | str | None,
) -> list[StudyDraft]:
    """Keep one draft per section+title. A later copy replaces the earlier one."""
    merged: dict[str, StudyDraft] = {}
    order: list[str] = []
    for draft in drafts:
        key = f"{draft.section}\n{draft.title}"
        if key not in merged:
            order.append(key)
        elif log_id is not None:
            ingest_info(
                log_id,
                IngestPhase.FIELDS,
                f'replaced an earlier copy of "{draft.title}" with a later one '
                f'in section "{draft.section}".',
            )
        merged[key] = draft
    return [merged[key] for key in order]