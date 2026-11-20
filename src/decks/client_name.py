"""Ask the structure LLM for the study client; keep only names already in the text."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from src.config import settings
from src.llm import complete_json, model_name_for, provider_for

logger = logging.getLogger(__name__)

_EMPTY_NAMES = {"none", "null", "n/a", "na", "unknown", "unnamed", "not found", ""}

CLIENT_SYSTEM_PROMPT = """\
Context: You name the client organization for one Arbisoft / Edly case study. \
You receive the case-study title and the full body text that will be stored \
as raw_text (field headings and paragraphs for that study only).

Your job: return the client (customer / partner organization) that this \
study is about — the company or institution Arbisoft or Edly worked for.

Rules:
- Read the title and the body. Prefer an explicit organization name that \
appears in that text (for example KAYAK, Harvard, Landit, MIT, Wikimedia, \
Cheetay).
- Copy the client name exactly as written in the title or body — same \
letters, same spelling, same capitalization pattern as the source text. \
Do not correct, translate, phoneticize, or substitute a look-alike word \
(for example if the text says "Cheetay", return "Cheetay", never "cheetah" \
or "Cheetah").
- Return the shortest clear organization token from the text (for example \
"Harvard" not a full slogan; "CLEAResult" when that is the client), but \
still use the exact spelling found in the text.
- If the text only says "the client", "a leading firm", or never names an \
organization, return an empty client_name.
- Never invent a name that is not written in the title or body.
- Never return Arbisoft or Edly — those are the vendors, not the client.
- Never return a person unless the study is clearly about that person as \
the client organization brand.
- Reply with JSON only. Shape: {"client_name":"<name or empty string>"}.
- client_name must be a string. Use "" when there is no client to store.
"""

CLIENT_JSON_SCHEMA: dict[str, Any] = {
    "title": "StudyClientName",
    "type": "object",
    "properties": {
        "client_name": {"type": "string"},
    },
    "required": ["client_name"],
    "additionalProperties": False,
}


def build_client_message(*, title: str, raw_text: str) -> str:
    """User message: case study title plus the raw_text that will be saved."""
    body = raw_text.strip() if raw_text else ""
    return (
        f"Case study title: {title.strip()}\n"
        f"Case study raw_text:\n{body if body else '(empty)'}"
    )


def ask_client_name(
    title: str,
    raw_text: str,
    *,
    deck_id: str | None = None,
    section: str | None = None,
) -> str:
    """Call the structure LLM once for this study. Returns the raw name string."""
    message = build_client_message(title=title, raw_text=raw_text)
    payload = _cached_complete(
        message,
        deck_id=deck_id,
        section=section,
        study_title=title.strip(),
    )
    value = payload.get("client_name", "")
    if value is None:
        return ""
    return str(value).strip()


def accept_client_name(reply: str, source: str) -> str | None:
    """Keep a model reply only when the study text already contains that name."""
    line = reply.splitlines()[0].strip().strip("\"'`").strip() if reply else ""
    if not line or line.lower().rstrip(".") in _EMPTY_NAMES:
        return None
    if len(line) > 300 or line.lower() == "arbisoft" or line.lower() == "edly":
        return None
    if line.lower() not in source.lower():
        return None
    return line


def infer_client_name(
    title: str,
    raw_text: str,
    *,
    deck_id: str | None = None,
    section: str | None = None,
) -> str | None:
    """Ask the LLM, then accept only a name already present in title + raw_text."""
    source = "\n".join(part for part in (title, raw_text) if part and part.strip())
    reply = ask_client_name(title, raw_text, deck_id=deck_id, section=section)
    return accept_client_name(reply, source)


def _cached_complete(
    user_message: str,
    *,
    deck_id: str | None = None,
    section: str | None = None,
    study_title: str | None = None,
) -> dict[str, Any]:
    cache_path = _cache_path_for(user_message)
    if cache_path.exists():
        logger.info("Structure model (client): cache hit (%s…)", cache_path.stem[:12])
        return json.loads(cache_path.read_text())
    logger.info(
        "Structure model (client): calling %s/%s (cache miss %s…)",
        provider_for("structure"),
        model_name_for("structure"),
        cache_path.stem[:12],
    )
    tags = ["deck_ingest", "client_name"]
    metadata: dict[str, Any] = {
        "pipeline": "deck_ingest",
        "kind": "client_name",
        "deck_id": str(deck_id) if deck_id is not None else None,
        "section": section,
        "study_title": study_title,
        "model": model_name_for("structure"),
        "provider": provider_for("structure"),
    }
    run_name = "client_name"
    if study_title:
        short = study_title if len(study_title) <= 60 else f"{study_title[:57]}..."
        run_name = f"client_name {short}"
    payload = complete_json(
        system=CLIENT_SYSTEM_PROMPT,
        user=user_message,
        schema=CLIENT_JSON_SCHEMA,
        role="structure",
        tags=tags,
        metadata=metadata,
        run_name=run_name,
    )
    if not isinstance(payload, dict):
        raise ValueError("Client model JSON must be an object")
    cache_path.write_text(json.dumps(payload))
    logger.info(
        "Structure model (client): got client_name=%r (%s…)",
        payload.get("client_name", ""),
        cache_path.stem[:12],
    )
    return payload


def _cache_path_for(user_message: str) -> Path:
    provider = provider_for("structure")
    model = model_name_for("structure")
    reasoning = settings.STRUCTURE_LLM_REASONING_EFFORT.strip()
    max_tokens = settings.STRUCTURE_LLM_MAX_TOKENS
    schema_key = hashlib.sha256(
        json.dumps(CLIENT_JSON_SCHEMA, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    cache_key = hashlib.sha256(
        f"{provider}\n{model}\n{reasoning}\n{max_tokens}\n"
        f"json_schema_strict\n{schema_key}\n{CLIENT_SYSTEM_PROMPT}\n{user_message}".encode()
    ).hexdigest()
    root = Path.home() / ".cache" / "pitch-structure"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{cache_key}.json"
