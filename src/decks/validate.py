"""Accept outline labels and build study drafts from Docling blocks."""

from __future__ import annotations

import logging
import re
from typing import Any

from src.decks.blocks import SlideBlocks, TextBlock
from src.decks.client_name import accept_client_name, infer_client_name
from src.decks.drafts import FIELD_NAMES, StudyDraft
from src.decks.structure_model import BLOCK_TYPES

logger = logging.getLogger(__name__)

_FIELD_SET = frozenset(FIELD_NAMES)

DATE_LINE = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2},\s+\d{4}$",
    re.IGNORECASE,
)

PAGE_LINE = re.compile(r"^page\s*[‹<]?#?[›>]?$", re.IGNORECASE)

# Re-export for callers that imported client helpers from validate.
__all__ = (
    "StructureValidationError",
    "accept_client_name",
    "build_raw_text",
    "draft_from_labeled_span",
    "infer_client_name",
    "is_banner",
    "is_format_legend",
    "is_section_header",
    "outline_from_payload",
    "study_slide_numbers",
)


class StructureValidationError(ValueError):
    """Model output does not match the blocks. Fail the whole import."""


def outline_from_payload(
    payload: dict[str, Any],
    blocks_by_number: dict[int, TextBlock],
    *,
    batch_numbers: set[int],
) -> list[dict[str, Any]]:
    """Validate outline JSON. Every batch block id must appear exactly once."""
    if not isinstance(payload, dict):
        raise StructureValidationError("Outline JSON must be an object")
    raw_blocks = payload.get("blocks")
    if not isinstance(raw_blocks, list):
        raise StructureValidationError("Outline blocks must be a list")

    seen: set[int] = set()
    labeled: list[dict[str, Any]] = []
    for item in raw_blocks:
        if not isinstance(item, dict):
            raise StructureValidationError("Each outline block must be an object")
        try:
            block_id = int(item.get("block_id"))
        except (TypeError, ValueError) as exc:
            raise StructureValidationError("block_id must be an integer") from exc
        if block_id not in batch_numbers:
            raise StructureValidationError(
                f"Outline references block {block_id} outside the current batch"
            )
        if block_id in seen:
            raise StructureValidationError(f"Outline repeats block {block_id}")
        if block_id not in blocks_by_number:
            raise StructureValidationError(f"Outline references unknown block {block_id}")
        seen.add(block_id)

        block_type = str(item.get("block_type") or "").strip()
        if block_type not in BLOCK_TYPES:
            raise StructureValidationError(
                f"Unknown block_type {block_type!r} for block {block_id}"
            )
        block_type = _normalize_outline_type(
            block_type,
            blocks_by_number[block_id].text,
        )

        field = str(item.get("field") or "").strip()
        if field and field not in _FIELD_SET:
            raise StructureValidationError(f"Unknown field {field!r} for block {block_id}")
        if block_type != "heading":
            field = ""

        children = _unique_ints(_int_list(item.get("children"), "children"))
        if block_type != "heading":
            children = []
        for child in children:
            if child not in batch_numbers:
                raise StructureValidationError(
                    f"Heading {block_id} child {child} is outside the current batch"
                )
            if child not in blocks_by_number:
                raise StructureValidationError(f"Heading {block_id} child {child} is unknown")
            if _is_banner(blocks_by_number[child].text):
                raise StructureValidationError(f"Heading {block_id} child {child} is a banner")

        labeled.append(
            {
                "block_id": block_id,
                "block_type": block_type,
                "field": field,
                "children": children,
            }
        )

    missing = sorted(batch_numbers - seen)
    if missing:
        raise StructureValidationError(f"Outline omitted block id(s): {missing}")

    # Children must also appear as top-level paragraph entries with matching ids.
    child_ids = {
        child for item in labeled if item["block_type"] == "heading" for child in item["children"]
    }
    by_id = {item["block_id"]: item for item in labeled}
    for child in child_ids:
        entry = by_id.get(child)
        if entry is None:
            raise StructureValidationError(
                f"Heading child {child} is missing from the outline list"
            )
        if entry["block_type"] not in {"paragraph", "header_note"}:
            # Force nested body lines to paragraph when the model nested them.
            if entry["block_type"] == "heading":
                raise StructureValidationError(f"Heading child {child} cannot be another heading")
            entry["block_type"] = "paragraph"
            entry["field"] = ""
            entry["children"] = []
        elif entry["block_type"] == "header_note":
            raise StructureValidationError(f"Heading child {child} is a banner")

    labeled.sort(key=lambda item: item["block_id"])
    return labeled


def draft_from_labeled_span(
    *,
    title: str,
    section: str,
    title_block_numbers: list[int],
    span_items: list[dict[str, Any]],
    blocks_by_number: dict[int, TextBlock],
    slides_by_number: dict[int, SlideBlocks],
    deck_id: str | None = None,
) -> StudyDraft:
    """Copy Docling text into fields from labeled blocks in one study span."""
    field_ids: dict[str, list[int]] = {name: [] for name in FIELD_NAMES}
    raw_numbers: list[int] = []
    open_field: str | None = None

    for item in span_items:
        block_id = int(item["block_id"])
        block_type = str(item["block_type"])
        if block_type in {"title", "section", "header_note"}:
            continue
        if block_type == "heading":
            raw_numbers.append(block_id)
            field = str(item.get("field") or "")
            open_field = field if field in _FIELD_SET else None
            for child in item.get("children") or []:
                child_id = int(child)
                if child_id in blocks_by_number and not _is_banner(blocks_by_number[child_id].text):
                    raw_numbers.append(child_id)
                    if open_field is not None:
                        field_ids[open_field].append(child_id)
            continue
        if block_type == "paragraph":
            # Nested children are also listed as top-level paragraphs; skip
            # duplicates already taken from a heading's children list.
            if raw_numbers and raw_numbers[-1] == block_id:
                continue
            if any(block_id in ids for ids in field_ids.values()):
                if block_id not in raw_numbers:
                    raw_numbers.append(block_id)
                continue
            raw_numbers.append(block_id)
            if open_field is not None:
                field_ids[open_field].append(block_id)

    # Preserve reading order and uniqueness for raw_text.
    seen_raw: set[int] = set()
    ordered_raw: list[int] = []
    for number in raw_numbers:
        if number in seen_raw:
            continue
        seen_raw.add(number)
        ordered_raw.append(number)

    fields: dict[str, str] = {}
    for name in FIELD_NAMES:
        kept = _unique_ints(field_ids[name])
        fields[name] = "\n".join(
            blocks_by_number[n].text.strip()
            for n in kept
            if n in blocks_by_number
            and blocks_by_number[n].text.strip()
            and not _is_banner(blocks_by_number[n].text)
        )

    raw_text = build_raw_text(
        ordered_raw,
        title,
        blocks_by_number,
        title_block_numbers=title_block_numbers,
    )
    slide_numbers = study_slide_numbers(
        title_block_numbers=title_block_numbers,
        body_block_numbers=[
            n
            for n in ordered_raw
            if n in blocks_by_number and not _is_banner(blocks_by_number[n].text)
        ],
        blocks_by_number=blocks_by_number,
        span_items=span_items,
    )
    images = []
    for number in slide_numbers:
        slide = slides_by_number.get(number)
        if slide is not None:
            images.extend(slide.images)
    return StudyDraft(
        section=section or "General",
        title=title,
        client_name=infer_client_name(
            title,
            raw_text,
            deck_id=deck_id,
            section=section or "General",
        ),
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


def study_slide_numbers(
    *,
    title_block_numbers: list[int],
    body_block_numbers: list[int],
    blocks_by_number: dict[int, TextBlock],
    span_items: list[dict[str, Any]],
) -> list[int]:
    """Slides that hold the title or a real body paragraph. Not footer-only."""
    keep: set[int] = set()
    for number in title_block_numbers:
        block = blocks_by_number.get(number)
        if block is not None:
            keep.add(block.slide)
    for number in body_block_numbers:
        block = blocks_by_number.get(number)
        if block is None or _is_banner(block.text):
            continue
        # Headings count as study content for slide membership.
        keep.add(block.slide)
    # A slide that is only a section divider inside the span is excluded.
    section_only_slides: set[int] = set()
    for item in span_items:
        block = blocks_by_number.get(int(item["block_id"]))
        if block is None:
            continue
        if item["block_type"] == "section":
            section_only_slides.add(block.slide)
    for slide in list(section_only_slides):
        has_other = False
        for item in span_items:
            block = blocks_by_number.get(int(item["block_id"]))
            if block is None or block.slide != slide:
                continue
            if item["block_type"] not in {"section", "header_note"}:
                has_other = True
                break
        if not has_other:
            keep.discard(slide)
    return sorted(keep)


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


def is_banner(text: str) -> bool:
    return _is_banner(text)


def is_section_header(text: str) -> bool:
    """True when text is a known cover/category divider (exact label match)."""
    return _normalize_label(text) in _SECTION_HEADER_BANNERS


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


# Template, legend, and promo lines. Never study titles or section dividers.
NON_CONTENT_LABELS = frozenset(
    {
        "arbisoft case study for zain cash",
        "design case study: view our design case studies here",
        "format",
        "general format",
        "what the company is?",
        "what we did?",
    }
)
# Format / General Format legend slides end the prior study without changing section.
FORMAT_LEGEND_LABELS = frozenset({"format", "general format"})
# Back-compat alias for older imports.
FAKE_SECTION_LABELS = NON_CONTENT_LABELS


def is_format_legend(text: str) -> bool:
    """True for Format / General Format legend chrome (not a real category)."""
    return _normalize_label(text) in FORMAT_LEGEND_LABELS


# Cover and category dividers. Always section, even when the model says otherwise.
# Also banners so they stay out of study raw_text.
_SECTION_HEADER_BANNERS = frozenset(
    {
        "arbisoft case studies",
        "arbisoft e-commerce case studies",
        "arbisoft case studies for humanitarian and nonprofit organizations",
        "arbisoft case studies for non-profit organizations",
        "broader it solutions for non-profit organisations",
        "content management system (cms)",
        "cross platform app case studies",
        "data scraping",
        "erp, dashboards, and reporting solutions for non-profit organisations",
        "ai-powered training content support platforms",
        "axim proposals",
        "design (nurture)",
        "ecommerce",
        "fintech design",
        "healthcare",
        "instructional design (content management, development & course authoring)",
        "managed hosting",
        "ml/ai",
        "mobile app development",
        "multi-language support",
        "multi-tier training academies (vendor-partner-user training)",
        "qa",
        "saas",
        "travel (general+ global distribution systems)",
        "web / mobile app development (search interface)",
        "edly instructional design "
        "(content management, development & course authoring) case studies",
        "edly case studies",
    }
)


def _normalize_label(text: str) -> str:
    return " ".join(text.split()).lower()


def _normalize_outline_type(block_type: str, text: str) -> str:
    """Force known cover dividers and reject non-content / template lines."""
    lowered = _normalize_label(text)
    if lowered in NON_CONTENT_LABELS:
        return "header_note"
    if lowered in _SECTION_HEADER_BANNERS:
        return "section"
    # Other banners are header notes unless already typed as a real section
    # (for example "Mobile App Development" is not in the cover list).
    if _is_banner(text) and block_type != "section":
        return "header_note"
    return block_type


def _is_banner(text: str) -> bool:
    """Running header or footer. These lines are not study body and not raw_text."""
    collapsed = " ".join(text.split())
    lowered = collapsed.lower()
    if not lowered:
        return False
    if (
        lowered
        in {
            "www.arbisoft.com",
            "www.edly.io",
            "confidential",
            "edly.io",
        }
        or lowered in _SECTION_HEADER_BANNERS
        or lowered in NON_CONTENT_LABELS
    ):
        return True
    if "edly.io" in lowered:
        return True
    if lowered.startswith("arbisoft proposal") or lowered.startswith("edly proposal"):
        return True
    if "valid till" in lowered or "valid until" in lowered:
        return True
    if lowered.startswith("proposal valid"):
        return True
    if PAGE_LINE.match(collapsed):
        return True
    return bool(DATE_LINE.match(collapsed))
