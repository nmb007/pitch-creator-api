"""Deck structure calls: mark titles, then assign field block numbers."""

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

TITLE_SYSTEM_PROMPT = """\
You mark case-study titles and section divider blocks in Arbisoft PowerPoint \
blocks. Return only block numbers. Do not write title or section text.

Title: the short name of one case study. Usually one headline block, or two \
adjacent blocks when the name wraps onto a second line. Two numbers in one \
block_numbers list are only those two next-to-each-other lines. Prefer lines \
that name the work or the client in a headline style. If only the first line \
of a wrapped title is in this batch, return that one block with incomplete \
true. Otherwise omit incomplete or set it false. When several case studies \
are in this batch, emit one titles entry per case study. Do not put two \
separate headlines in one block_numbers list.

Section: only a short category or divider name that labels the group of \
studies that follow (for example "Cross platform App Case Studies" or \
"Mobile App Development"). Before any such divider, the section is General. \
A section is almost never a full sentence and almost never a Tech Stack line.

Never put these in titles or sections:
- body paragraphs (long sentences about the client or the work)
- headings such as The Challenge, The Approach, The Outcome, Background, \
Challenges, Technology Stack, Tech Stack
- format labels such as General Format, What the Company Is?, What We Did?
- banners and footers such as Arbisoft Proposal, Confidential, valid till, \
www.arbisoft.com, and dates

When unsure whether a block is a title or a section, leave it out of both \
lists. Empty titles and empty sections are better than a wrong block.

Example. Blocks:
10. slide=3 text=Advancing edX with Arbisoft's Innovation in Online Learning
11. slide=3 text=edX is a brainchild of MIT and Harvard, is a global leader \
in online education.
12. slide=3 text=Tech Stack: Python/Django, React
13. slide=12 text=Cross platform App Case Studies
Good reply:
{"sections":[{"block_number":13}],"titles":[{"block_numbers":[10]}]}
Bad reply (do not do this): putting block 11 or 12 in sections, or leaving \
block 10 out of titles.

Example. Two studies in one batch:
20. slide=63 text=QA Collaboration with edX: A Decade of Innovation
21. slide=63 text=The Challenge
30. slide=64 text=Global Crew Accommodation Solutions Platform
Good reply:
{"sections":[],"titles":[{"block_numbers":[20]},{"block_numbers":[30]}]}
Bad reply (do not do this): {"sections":[],"titles":[{"block_numbers":[20, 30]}]}

Do not write study body fields. Do not say whether a study continues.
Return JSON only, matching the required shape.
"""

TITLE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "block_number": {"type": "integer"},
                },
                "required": ["block_number"],
            },
        },
        "titles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "block_numbers": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                    "incomplete": {"type": "boolean"},
                },
                "required": ["block_numbers"],
            },
        },
    },
    "required": ["sections", "titles"],
}

FIELD_SYSTEM_PROMPT = """\
You assign Docling block numbers to case-study fields.
The title and section are already known. Do not change them.
For background, challenge, approach, solution, outcome, and tech_stack, \
return only lists of block numbers from the Blocks list you were given.
Copy no sentences. Use an empty list when a field has no matching block.
The client is the organization the work was done for. Arbisoft is the vendor, \
not the client. If the blocks do not name that organization, client_name is \
an empty string.
Return JSON only, matching the required shape.
"""

FIELD_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "client_name": {"type": "string"},
        "background": {"type": "array", "items": {"type": "integer"}},
        "challenge": {"type": "array", "items": {"type": "integer"}},
        "approach": {"type": "array", "items": {"type": "integer"}},
        "solution": {"type": "array", "items": {"type": "integer"}},
        "outcome": {"type": "array", "items": {"type": "integer"}},
        "tech_stack": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["client_name", *FIELD_NAMES],
}


def structure_titles(user_message: str) -> dict[str, Any]:
    """Ask the structure LLM for section and title anchors. Cache by prompt hash."""
    return _cached_complete(
        TITLE_SYSTEM_PROMPT,
        TITLE_JSON_SCHEMA,
        user_message,
        kind="titles",
    )


def structure_fields(user_message: str) -> dict[str, Any]:
    """Ask the structure LLM for field block numbers. Cache by prompt hash."""
    return _cached_complete(
        FIELD_SYSTEM_PROMPT,
        FIELD_JSON_SCHEMA,
        user_message,
        kind="fields",
    )


def build_title_message(
    *,
    section: str,
    blocks: list[tuple[int, int, str]],
    held_title: dict[str, Any] | None = None,
) -> str:
    lines = [f"Current section: {section}", "Blocks:"]
    for number, slide, text in blocks:
        lines.append(f"{number}. slide={slide} text={text}")
    if held_title is not None:
        lines.append("Held open wrapped title (complete it or mark another title):")
        lines.append(json.dumps(held_title, ensure_ascii=False))
    return "\n".join(lines)


def build_field_message(
    *,
    section: str,
    title: str,
    title_block_numbers: list[int],
    body_blocks: list[tuple[int, int, str]],
) -> str:
    title_bits = ", ".join(str(n) for n in title_block_numbers)
    lines = [
        f"Section: {section}",
        f"Title block: {title_bits}. {title}",
        "Blocks:",
    ]
    for number, slide, text in body_blocks:
        lines.append(f"{number}. slide={slide} text={text}")
    return "\n".join(lines)


# Keep the old names as aliases so any leftover import fails loudly if misused.
structure_studies = structure_titles
build_user_message = build_title_message
SYSTEM_PROMPT = TITLE_SYSTEM_PROMPT
STUDY_JSON_SCHEMA = TITLE_JSON_SCHEMA


def _cached_complete(
    system_prompt: str,
    schema: dict[str, Any],
    user_message: str,
    *,
    kind: str,
) -> dict[str, Any]:
    provider = provider_for("structure")
    model = model_name_for("structure")
    reasoning = settings.STRUCTURE_LLM_REASONING_EFFORT.strip()
    max_tokens = settings.STRUCTURE_LLM_MAX_TOKENS
    cache_key = hashlib.sha256(
        f"{provider}\n{model}\n{reasoning}\n{max_tokens}\n{system_prompt}\n{user_message}".encode()
    ).hexdigest()
    cache_path = _cache_dir() / f"{cache_key}.json"
    if cache_path.exists():
        logger.info("Structure model (%s): cache hit (%s…)", kind, cache_key[:12])
        return json.loads(cache_path.read_text())
    logger.info(
        "Structure model (%s): calling %s/%s (cache miss %s…)",
        kind,
        provider,
        model,
        cache_key[:12],
    )
    payload = complete_json(
        system=system_prompt,
        user=user_message,
        schema=schema,
        role="structure",
    )
    if not isinstance(payload, dict):
        raise ValueError("Structure model JSON must be an object")
    cache_path.write_text(json.dumps(payload))
    if kind == "titles":
        title_count = len(payload.get("titles") or [])
        logger.info("Structure model (titles): got %s title(s) in JSON", title_count)
    else:
        logger.info("Structure model (fields): got field block lists")
    return payload


def _cache_dir() -> Path:
    root = Path.home() / ".cache" / "pitch-structure"
    root.mkdir(parents=True, exist_ok=True)
    return root
