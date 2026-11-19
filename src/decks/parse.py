"""Turn the master PowerPoint into study drafts via Docling and an outline model."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from src.config import settings
from src.decks.blocks import SlideBlocks, TextBlock, read_pptx_blocks
from src.decks.drafts import FIELD_NAMES, ImageDraft, StudyDraft
from src.decks.ingest_log import (
    IngestPhase,
    ingest_info,
    slide_range,
)
from src.decks.structure_model import (
    build_outline_message,
    commit_outline_cache,
    discard_outline_cache,
    structure_outline,
)
from src.decks.validate import (
    StructureValidationError,
    draft_from_labeled_span,
    is_banner,
    outline_from_payload,
)

StructureFn = Callable[[str], dict[str, Any]]

__all__ = [
    "FIELD_NAMES",
    "ImageDraft",
    "StructureValidationError",
    "StudyDraft",
    "parse_pptx",
    "section_for",
]


def parse_pptx(
    path: Path,
    *,
    deck_id: UUID | None = None,
    complete: StructureFn | None = None,
    complete_fields: StructureFn | None = None,
) -> list[StudyDraft]:
    """Read blocks with Docling, label them once, then build study drafts in code.

    ``complete`` is the outline model call. ``complete_fields`` is accepted for
    older call sites and ignored; field assignment no longer uses a model.
    """
    del complete_fields
    slides = read_pptx_blocks(path, deck_id=deck_id)
    if not slides:
        if deck_id is not None:
            ingest_info(deck_id, IngestPhase.READ, "finished reading. No slides found.")
        return []
    blocks_by_number = {block.number: block for slide in slides for block in slide.blocks}
    slides_by_number = {slide.number: slide for slide in slides}
    outline_runner = complete or structure_outline
    batch_size = max(1, settings.STRUCTURE_BATCH_SLIDES)
    if deck_id is not None:
        ingest_info(
            deck_id,
            IngestPhase.OUTLINE,
            f"labeling blocks. {len(slides)} slides, {batch_size} slides per call.",
        )
    labeled, debug_batches = _collect_outline(
        slides,
        blocks_by_number,
        outline_runner,
        log_id=deck_id,
        persist_cache=outline_runner is structure_outline,
    )
    if deck_id is not None:
        _write_outline_debug(deck_id, debug_batches)
    drafts = _drafts_from_labeled(
        labeled,
        blocks_by_number,
        slides_by_number,
        log_id=deck_id,
    )
    return _replace_same_key(drafts, log_id=deck_id)


def _collect_outline(
    slides: list[SlideBlocks],
    blocks_by_number: dict[int, TextBlock],
    complete: StructureFn,
    *,
    log_id: UUID | str | None,
    persist_cache: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    labeled: list[dict[str, Any]] = []
    debug_batches: list[dict[str, Any]] = []
    section = "General"
    held_heading: dict[str, Any] | None = None
    index = 0
    batch_size = max(1, settings.STRUCTURE_BATCH_SLIDES)
    model_calls = 0
    retry_limit = max(0, settings.STRUCTURE_LLM_RETRY_LIMIT)

    while index < len(slides):
        window = slides[index : index + batch_size]
        index += len(window)
        block_rows = [
            (block.number, block.slide, block.text) for slide in window for block in slide.blocks
        ]
        if not block_rows:
            continue
        batch_numbers = {number for number, _, _ in block_rows}
        slide_nos = [slide.number for slide in window]
        model_calls += 1
        if log_id is not None:
            held = " (open heading)" if held_heading is not None else ""
            ingest_info(
                log_id,
                IngestPhase.OUTLINE,
                f"outline call {model_calls}, slides {slide_range(slide_nos)}{held}.",
            )
        message = build_outline_message(
            section=section,
            blocks=block_rows,
            held_heading=held_heading,
        )
        accepted: list[dict[str, Any]] | None = None
        last_error = ""
        rejected: list[dict[str, Any]] = []
        for attempt in range(retry_limit + 1):
            payload = complete(message)
            try:
                accepted = outline_from_payload(
                    payload,
                    blocks_by_number,
                    batch_numbers=batch_numbers,
                )
                if persist_cache:
                    commit_outline_cache(message, payload)
                break
            except StructureValidationError as exc:
                last_error = str(exc)
                if persist_cache:
                    discard_outline_cache(message)
                rejected.append(
                    {
                        "attempt": attempt + 1,
                        "error": last_error,
                        "payload": _debug_payload(payload),
                    }
                )
                if log_id is not None:
                    ingest_info(
                        log_id,
                        IngestPhase.OUTLINE,
                        f"outline rejected for slides {slide_range(slide_nos)}: {last_error}",
                    )
        if accepted is None:
            raise StructureValidationError(
                f"Outline failed for slides {slide_range(slide_nos)}: {last_error}"
            )

        debug_batches.append(
            {
                "slides": slide_nos,
                "accepted": accepted,
                "rejected": rejected,
            }
        )
        labeled.extend(accepted)

        titles = [item for item in accepted if item["block_type"] == "title"]
        sections = [item for item in accepted if item["block_type"] == "section"]
        if log_id is not None:
            ingest_info(
                log_id,
                IngestPhase.OUTLINE,
                f"labeled {len(accepted)} blocks ({len(titles)} title, {len(sections)} section).",
            )
        for item in sections:
            section = blocks_by_number[int(item["block_id"])].text.strip()
            if log_id is not None:
                ingest_info(
                    log_id,
                    IngestPhase.OUTLINE,
                    f'found section "{section}" at block {item["block_id"]}.',
                )

        held_heading = _next_held_heading(accepted, blocks_by_number)

    if log_id is not None:
        ingest_info(
            log_id,
            IngestPhase.OUTLINE,
            f"finished labeling. {len(labeled)} blocks from {model_calls} calls.",
        )
    return labeled, debug_batches


def _next_held_heading(
    accepted: list[dict[str, Any]],
    blocks_by_number: dict[int, TextBlock],
) -> dict[str, Any] | None:
    """Keep the last heading open when the batch ends under it."""
    open_heading: dict[str, Any] | None = None
    for item in accepted:
        block_type = item["block_type"]
        if block_type in {"title", "section"}:
            open_heading = None
            continue
        if block_type == "header_note":
            continue
        if block_type == "heading":
            block_id = int(item["block_id"])
            open_heading = {
                "block_id": block_id,
                "field": item.get("field") or "",
                "text": blocks_by_number[block_id].text.strip(),
            }
    return open_heading


def _drafts_from_labeled(
    labeled: list[dict[str, Any]],
    blocks_by_number: dict[int, TextBlock],
    slides_by_number: dict[int, SlideBlocks],
    *,
    log_id: UUID | str | None,
) -> list[StudyDraft]:
    ordered = sorted(labeled, key=lambda item: int(item["block_id"]))
    by_id = {int(item["block_id"]): item for item in ordered}
    # Attach loose paragraphs to the open heading; skip header notes.
    _attach_orphan_paragraphs(ordered)

    drafts: list[StudyDraft] = []
    section = "General"
    index = 0
    while index < len(ordered):
        item = ordered[index]
        block_id = int(item["block_id"])
        block_type = item["block_type"]
        if block_type == "section":
            section = blocks_by_number[block_id].text.strip() or section
            index += 1
            continue
        if block_type != "title":
            index += 1
            continue
        if is_banner(blocks_by_number[block_id].text):
            index += 1
            continue

        title_numbers = [block_id]
        look = index + 1
        while look < len(ordered) and ordered[look]["block_type"] == "title":
            next_id = int(ordered[look]["block_id"])
            if is_banner(blocks_by_number[next_id].text):
                break
            # Only join adjacent title blocks into one wrapped title.
            if next_id != title_numbers[-1] + 1 and not _adjacent_blocks(
                title_numbers[-1], next_id, blocks_by_number
            ):
                break
            title_numbers.append(next_id)
            look += 1
        title = " ".join(blocks_by_number[n].text.strip() for n in title_numbers).strip()
        if not title:
            index = look
            continue

        span_start = look
        span_end = span_start
        while span_end < len(ordered):
            nxt = ordered[span_end]
            nxt_type = nxt["block_type"]
            if nxt_type in {"title", "section"}:
                break
            span_end += 1
        span_items = ordered[span_start:span_end]
        draft = draft_from_labeled_span(
            title=title,
            section=section,
            title_block_numbers=title_numbers,
            span_items=span_items,
            blocks_by_number=blocks_by_number,
            slides_by_number=slides_by_number,
        )
        drafts.append(draft)
        if log_id is not None:
            ingest_info(
                log_id,
                IngestPhase.OUTLINE,
                f'built study "{title}" in section "{section}" '
                f"({slide_range(draft.slide_numbers)}).",
            )
        index = span_end

    del by_id
    if log_id is not None:
        ingest_info(
            log_id,
            IngestPhase.OUTLINE,
            f"finished building {len(drafts)} case studies from labels.",
        )
    return drafts


def _attach_orphan_paragraphs(ordered: list[dict[str, Any]]) -> None:
    """Join top-level paragraphs to the open heading until the next title/section."""
    open_heading: dict[str, Any] | None = None
    nested: set[int] = set()
    for item in ordered:
        if item["block_type"] == "heading":
            nested.update(int(child) for child in item.get("children") or [])
    for item in ordered:
        block_type = item["block_type"]
        block_id = int(item["block_id"])
        if block_type in {"title", "section"}:
            open_heading = None
            continue
        if block_type == "header_note":
            continue
        if block_type == "heading":
            open_heading = item
            continue
        if block_type != "paragraph" or open_heading is None:
            continue
        if block_id in nested:
            continue
        children = list(open_heading.get("children") or [])
        if block_id not in children:
            children.append(block_id)
            open_heading["children"] = children
            nested.add(block_id)


def _adjacent_blocks(
    first: int,
    second: int,
    blocks_by_number: dict[int, TextBlock],
) -> bool:
    ordered = sorted(blocks_by_number)
    try:
        left = ordered.index(first)
        right = ordered.index(second)
    except ValueError:
        return False
    return right == left + 1


def section_for(block_number: int, section_marks: list[tuple[int, str]]) -> str:
    current = "General"
    for mark_block, name in section_marks:
        if mark_block > block_number:
            break
        current = name
    return current


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
                IngestPhase.OUTLINE,
                f'replaced an earlier copy of "{draft.title}" with a later one '
                f'in section "{draft.section}".',
            )
        merged[key] = draft
    return [merged[key] for key in order]


def _write_outline_debug(deck_id: UUID, batches: list[dict[str, Any]]) -> None:
    from src.decks.service import deck_path

    path = deck_path(deck_id).with_suffix(".outline.json")
    path.write_text(
        json.dumps(
            {"deck_id": str(deck_id), "batches": batches},
            ensure_ascii=False,
            indent=2,
        )
    )
    ingest_info(
        deck_id,
        IngestPhase.OUTLINE,
        f"wrote outline debug file {path.name}.",
    )


def _debug_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip accidental text fields before writing the debug file."""
    blocks = payload.get("blocks")
    if not isinstance(blocks, list):
        return {"blocks": []}
    cleaned: list[dict[str, Any]] = []
    for item in blocks:
        if not isinstance(item, dict):
            continue
        entry: dict[str, Any] = {
            "block_id": item.get("block_id"),
            "block_type": item.get("block_type"),
        }
        if item.get("field"):
            entry["field"] = item.get("field")
        if item.get("children"):
            entry["children"] = item.get("children")
        cleaned.append(entry)
    return {"blocks": cleaned}
