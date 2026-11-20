"""Deck structure call: label each Docling block once, then code builds studies."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from src.config import settings
from src.decks.drafts import FIELD_NAMES
from src.llm import complete_json, model_name_for, provider_for

logger = logging.getLogger(__name__)

BLOCK_TYPES = ("title", "section", "header_note", "heading", "paragraph")

OUTLINE_SYSTEM_PROMPT = """\
Context: You are labeling text extracted from an Arbisoft / Edly master \
PowerPoint of client case studies. Slides usually show running proposal \
banners, optional category dividers, one or more case-study headlines, \
field labels (challenge, approach, outcome, tech stack), and body copy. \
Some slides are only a category divider, a format legend, or a promo CTA. \
Your job is to classify each Docling block in the current batch by role.

Return only block ids and types. Do not copy or rewrite block text.

Reply shape (required):
- One JSON object. Top level must be {"blocks":[...]}, never a bare array.
- Each item uses keys block_id and block_type (not id, not type).
- Every input block id under Blocks must appear exactly once in blocks.
- Never invent block ids. Never return block text. Empty children is [].
- Label only ids listed under Blocks. Never re-list a Held open heading \
block_id; that id is from an earlier batch and is not in this batch.

How to choose block_type:
- title: the short headline name of one case study (what the study is \
called). Usually appears after banners such as Arbisoft Proposal / \
Confidential, and is followed by field headings such as The Challenge or \
Background. Often a short phrase or project name, not a full sentence. \
Two adjacent lines that together make one wrapped name are both title. \
Never label a case-study headline as paragraph.
- section: a short category divider that groups the studies that follow \
(for example "Arbisoft Case Studies", "Edly Case Studies", \
"Cross platform App Case Studies", "Mobile App Development", \
"Ecommerce", "Healthcare", "Managed Hosting", "Fintech Design", "QA"). \
It names a theme or practice area, not one client's story.
- header_note: chrome that is not study content — running banners and \
footers such as Arbisoft Proposal, Edly Proposal, Confidential, valid \
till, valid until, dates, www.arbisoft.com, edly.io, axim proposals, page \
number placeholders; template legend lines such as Format, General \
Format, What the Company Is?, and What We Did?; and promo / CTA lines \
such as Design Case Study: View Our Design Case Studies here.
- heading: a field label only, such as The Challenge, The Approach, The \
Outcome, Background, Challenges, Solutions, Solution, Technology Stack, \
Tech Stack. Set field to exactly one of background, challenge, approach, \
solution, outcome, tech_stack when you know which. Leave field empty when \
unsure. Put the paragraph block ids that belong under this heading in \
children. Those child paragraphs must also appear as their own paragraph \
entries. Children must be ids from this batch only.
- paragraph: body sentences and bullet lines that tell the story under a \
heading. If a paragraph continues a Held open heading from an earlier \
slide, label it paragraph with children []. Do not re-emit the held \
heading. Code will attach those paragraphs. Do not use paragraph for a \
case-study headline that sits above The Challenge / Background.

Example. Blocks:
10. slide=13 text=Arbisoft Proposal for XYZ Ltd
11. slide=13 text=Confidential
12. slide=13 text=Cross-Platform Development for Landit
13. slide=13 text=The Challenge
14. slide=13 text=Landit needed a secure mobile app.
15. slide=13 text=The Approach
16. slide=13 text=We recommended React Native.
Good reply (every item includes field and children; use "" and [] when unused):
{"blocks":[{"block_id":10,"block_type":"header_note","field":"","children":[]},{"block_id":11,"block_type":"header_note","field":"","children":[]},{"block_id":12,"block_type":"title","field":"","children":[]},{"block_id":13,"block_type":"heading","field":"challenge","children":[14]},{"block_id":14,"block_type":"paragraph","field":"","children":[]},{"block_id":15,"block_type":"heading","field":"approach","children":[16]},{"block_id":16,"block_type":"paragraph","field":"","children":[]}]}
Bad reply (do not do this): a bare array such as \
[{"id":10,"type":"header_note"},{"id":12,"type":"title"}]
Bad reply (do not do this): keys named id or type instead of block_id and \
block_type.
Bad reply (do not do this): omitting a block id from the input, or inventing \
a new id.
Bad reply (do not do this): re-listing a Held open heading block_id that is \
not under Blocks.
Bad reply (do not do this): labeling a case-study headline as paragraph \
when The Challenge or Background follows it.

Return JSON only, matching the required shape.
"""

# Strict JSON Schema (Groq/OpenAI): every property required, no extras.
# LangChain needs top-level title as the response-format / function name.
OUTLINE_JSON_SCHEMA: dict[str, Any] = {
    "title": "DeckOutline",
    "type": "object",
    "properties": {
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "block_id": {"type": "integer"},
                    "block_type": {"type": "string", "enum": list(BLOCK_TYPES)},
                    "field": {
                        "type": "string",
                        "enum": ["", *FIELD_NAMES],
                    },
                    "children": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
                "required": ["block_id", "block_type", "field", "children"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["blocks"],
    "additionalProperties": False,
}


def structure_outline(
    user_message: str,
    *,
    deck_id: str | None = None,
    slide_start: int | None = None,
    slide_end: int | None = None,
    outline_call: int | None = None,
    title_miss: bool = False,
) -> dict[str, Any]:
    """Ask the structure LLM to label every block in the batch.

    Reads a prior validated cache entry when present. Does not write the cache;
    call commit_outline_cache after outline_from_payload succeeds.
    """
    tags = ["deck_ingest", "outline"]
    if title_miss:
        tags.append("retry")
        tags.append("title_miss_retry")
    metadata: dict[str, Any] = {
        "pipeline": "deck_ingest",
        "kind": "outline",
        "deck_id": str(deck_id) if deck_id is not None else None,
        "slide_start": slide_start,
        "slide_end": slide_end,
        "outline_call": outline_call,
        "title_miss": title_miss,
        "model": model_name_for("structure"),
        "provider": provider_for("structure"),
    }
    run_name = "outline"
    if slide_start is not None and slide_end is not None:
        run_name = f"outline slides {slide_start}–{slide_end}"
    return _cached_complete(
        OUTLINE_SYSTEM_PROMPT,
        OUTLINE_JSON_SCHEMA,
        user_message,
        kind="outline",
        tags=tags,
        metadata=metadata,
        run_name=run_name,
    )


def commit_outline_cache(user_message: str, payload: dict[str, Any]) -> None:
    """Persist a validated outline reply so later imports can reuse it."""
    cache_path = _outline_cache_path(user_message)
    cache_path.write_text(json.dumps(payload))
    logger.info(
        "Structure model (outline): cached %s block(s) (%s…)",
        len(payload.get("blocks") or []),
        cache_path.stem[:12],
    )


def discard_outline_cache(user_message: str) -> None:
    """Drop a cached outline reply that failed validation so retries re-call."""
    cache_path = _outline_cache_path(user_message)
    if cache_path.exists():
        cache_path.unlink()
        logger.info(
            "Structure model (outline): discarded invalid cache (%s…)",
            cache_path.stem[:12],
        )


def build_outline_message(
    *,
    section: str,
    blocks: list[tuple[int, int, str]],
    held_heading: dict[str, Any] | None = None,
) -> str:
    lines = [f"Current section: {section}", "Blocks:"]
    for number, slide, text in blocks:
        lines.append(f"{number}. slide={slide} text={text}")
    if held_heading is not None:
        lines.append(
            "Held open heading (context only — do not re-list this block_id; "
            "label continuing body as paragraph):"
        )
        lines.append(json.dumps(held_heading, ensure_ascii=False))
    return "\n".join(lines)


# Aliases so older imports fail clearly if still used for the wrong shape.
structure_titles = structure_outline
structure_fields = structure_outline
structure_studies = structure_outline
build_title_message = build_outline_message
build_field_message = build_outline_message
build_user_message = build_outline_message
SYSTEM_PROMPT = OUTLINE_SYSTEM_PROMPT
TITLE_SYSTEM_PROMPT = OUTLINE_SYSTEM_PROMPT
FIELD_SYSTEM_PROMPT = OUTLINE_SYSTEM_PROMPT
TITLE_JSON_SCHEMA = OUTLINE_JSON_SCHEMA
FIELD_JSON_SCHEMA = OUTLINE_JSON_SCHEMA
STUDY_JSON_SCHEMA = OUTLINE_JSON_SCHEMA


def _cached_complete(
    system_prompt: str,
    schema: dict[str, Any],
    user_message: str,
    *,
    kind: str,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    run_name: str | None = None,
) -> dict[str, Any]:
    cache_path = _cache_path_for(system_prompt, schema, user_message)
    if cache_path.exists():
        logger.info("Structure model (%s): cache hit (%s…)", kind, cache_path.stem[:12])
        return json.loads(cache_path.read_text())
    logger.info(
        "Structure model (%s): calling %s/%s (cache miss %s…)",
        kind,
        provider_for("structure"),
        model_name_for("structure"),
        cache_path.stem[:12],
    )
    payload = complete_json(
        system=system_prompt,
        user=user_message,
        schema=schema,
        role="structure",
        tags=tags,
        metadata=metadata,
        run_name=run_name,
    )
    if not isinstance(payload, dict):
        raise ValueError("Structure model JSON must be an object")
    # Do not write here. Invalid replies must not poison retries; parse commits
    # only after outline_from_payload accepts the payload.
    block_count = len(payload.get("blocks") or [])
    logger.info("Structure model (%s): got %s labeled block(s)", kind, block_count)
    return payload


def _outline_cache_path(user_message: str) -> Path:
    return _cache_path_for(OUTLINE_SYSTEM_PROMPT, OUTLINE_JSON_SCHEMA, user_message)


def _cache_path_for(
    system_prompt: str,
    schema: dict[str, Any],
    user_message: str,
) -> Path:
    provider = provider_for("structure")
    model = model_name_for("structure")
    reasoning = settings.STRUCTURE_LLM_REASONING_EFFORT.strip()
    max_tokens = settings.STRUCTURE_LLM_MAX_TOKENS
    schema_key = hashlib.sha256(
        json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    cache_key = hashlib.sha256(
        f"{provider}\n{model}\n{reasoning}\n{max_tokens}\n"
        f"json_schema_strict\n{schema_key}\n{system_prompt}\n{user_message}".encode()
    ).hexdigest()
    return _cache_dir() / f"{cache_key}.json"


def _cache_dir() -> Path:
    root = Path.home() / ".cache" / "pitch-structure"
    root.mkdir(parents=True, exist_ok=True)
    return root
