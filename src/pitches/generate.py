"""Write one pitch_pages row from the selected case studies."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlmodel import select

from src.config import settings
from src.database import get_sync_session
from src.decks.models import Study, StudyVersion
from src.llm import complete_json, model_name_for
from src.models import utcnow
from src.pitches.models import (
    Pitch,
    PitchInput,
    PitchMatch,
    PitchMessage,
    PitchPage,
    PitchRevision,
)

logger = logging.getLogger(__name__)

_PITCH_SCHEMA: dict[str, Any] = {
    "title": "GeneratedPitch",
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "Short pitch title for the prospect (not a heading inside the body)",
        },
        "hook": {
            "type": "string",
            "description": "1–2 sentences tying the prospect brief to a concrete need",
        },
        "what_we_will_do": {
            "type": "string",
            "description": "Approach or solution in plain language",
        },
        "proof": {
            "type": "array",
            "description": "2–4 bullets grounded in the selected studies",
            "minItems": 2,
            "maxItems": 4,
            "items": {
                "type": "string",
                "description": "One proof point: outcome, tech, or domain fit from a study",
            },
        },
        "why_arbisoft": {
            "type": "string",
            "description": "Short capability close explaining why Arbisoft fits",
        },
        "next_step": {
            "type": "string",
            "description": "Optional one-line ask or next step; empty string if none",
        },
    },
    "required": ["title", "hook", "what_we_will_do", "proof", "why_arbisoft", "next_step"],
    "additionalProperties": False,
}

_SYSTEM = """You write a tailored pitch for a prospect from Arbisoft case studies.
Use only facts that appear in the selected studies.
Do not invent clients, metrics, technologies, or outcomes.
Do not list study titles in the pitch fields; the app lists studies separately.
Keep the tone professional and concrete.

Fill every field:
- title: short page title
- hook: 1–2 sentences linking the prospect brief to a concrete need
- what_we_will_do: how Arbisoft would approach or solve that need
- proof: 2–4 short bullets grounded in study outcomes, tech, or domain
- why_arbisoft: brief capability close
- next_step: one optional ask, or empty string"""


def view_url(pitch_id: UUID) -> str:
    base = settings.WEB_BASE_URL.rstrip("/")
    return f"{base}/pitch/view?id={pitch_id}"


def write_generated_pitch(*, pitch_id: UUID, user_id: UUID) -> dict[str, str]:
    """Create a revision and page from selected matches. Returns title and view URL."""
    brief, studies = _load_brief_and_studies(pitch_id)
    drafted = _draft_pitch(pitch_id=pitch_id, brief=brief, studies=studies)
    title = (drafted.get("title") or "Pitch").strip()[:200] or "Pitch"
    body = (drafted.get("body") or "").strip()
    if not body:
        body = _fallback_body(studies)
    url = view_url(pitch_id)
    message = (
        f"Your pitch is ready: [{title}]({url})\n\n"
        "Open the link to review the tailored description and the selected case studies."
    )
    with get_sync_session() as session:
        revision = PitchRevision(pitch_id=pitch_id, source="generate", created_by=user_id)
        session.add(revision)
        session.flush()
        session.add(PitchPage(revision_id=revision.id, title=title, body=body))
        session.add(PitchMessage(pitch_id=pitch_id, role="assistant", body=message))
        pitch = session.get(Pitch, pitch_id)
        if pitch is not None:
            pitch.updated_at = utcnow()
            session.add(pitch)
        session.commit()
    return {"title": title, "url": url, "message": message}


def _load_brief_and_studies(pitch_id: UUID) -> tuple[str, list[dict[str, str]]]:
    with get_sync_session() as session:
        incoming = session.exec(select(PitchInput).where(PitchInput.pitch_id == pitch_id)).first()
        brief = (incoming.body if incoming is not None else "") or ""
        rows = session.exec(
            select(PitchMatch, Study, StudyVersion)
            .join(Study, Study.id == PitchMatch.study_id)
            .join(StudyVersion, StudyVersion.id == PitchMatch.study_version_id)
            .where(PitchMatch.pitch_id == pitch_id)
            .where(PitchMatch.selected.is_(True))
            .order_by(PitchMatch.rank)
        ).all()
        studies: list[dict[str, str]] = []
        for _match, study, version in rows:
            studies.append(
                {
                    "section": study.section,
                    "title": study.title,
                    "background": version.background or "",
                    "challenge": version.challenge or "",
                    "approach": version.approach or "",
                    "solution": version.solution or "",
                    "outcome": version.outcome or "",
                    "tech_stack": version.tech_stack or "",
                    # Short excerpt only; structured fields are the primary source.
                    "raw_text": (version.raw_text or "")[:1500],
                }
            )
        return brief.strip(), studies


def _draft_pitch(*, pitch_id: UUID, brief: str, studies: list[dict[str, str]]) -> dict[str, str]:
    if not studies:
        return {"title": "Pitch", "body": ""}
    if not settings.GROQ_API_KEY:
        return {"title": "Pitch", "body": _fallback_body(studies)}
    parts = [f"Prospect brief:\n{brief[:6000]}\n", "Selected case studies:"]
    for index, study in enumerate(studies, start=1):
        block = (
            f"\n{index}. {study['title']} ({study['section']})\n"
            f"Background: {study['background']}\n"
            f"Challenge: {study['challenge']}\n"
            f"Approach: {study['approach']}\n"
            f"Solution: {study['solution']}\n"
            f"Outcome: {study['outcome']}\n"
            f"Tech stack: {study['tech_stack']}"
        )
        if study["raw_text"].strip():
            block += f"\nSource excerpt:\n{study['raw_text']}"
        parts.append(block)
    try:
        raw = complete_json(
            system=_SYSTEM,
            user="\n".join(parts),
            schema=_PITCH_SCHEMA,
            role="pitch",
            tags=[str(pitch_id), model_name_for("pitch")],
            metadata={"pitch_id": str(pitch_id), "model": model_name_for("pitch")},
            run_name="pitch_generate",
        )
    except Exception:
        logger.exception("Pitch generate failed for %s", pitch_id)
        return {"title": "Pitch", "body": _fallback_body(studies)}
    return {
        "title": str(raw.get("title") or "Pitch").strip()[:200] or "Pitch",
        "body": pitch_markdown_from_sections(raw),
    }


def pitch_markdown_from_sections(sections: dict[str, Any]) -> str:
    """Assemble stored pitch body Markdown from structured LLM fields."""
    hook = str(sections.get("hook") or "").strip()
    what = str(sections.get("what_we_will_do") or "").strip()
    why = str(sections.get("why_arbisoft") or "").strip()
    next_step = str(sections.get("next_step") or "").strip()
    proof_raw = sections.get("proof")
    proof_items: list[str] = []
    if isinstance(proof_raw, list):
        for item in proof_raw:
            text = str(item or "").strip()
            if text:
                proof_items.append(text)

    parts: list[str] = []
    if hook:
        parts.extend(["## Why this matters", "", hook])
    if what:
        if parts:
            parts.append("")
        parts.extend(["## How we can help", "", what])
    if proof_items:
        if parts:
            parts.append("")
        parts.extend(["## Relevant experience", ""])
        parts.extend(f"- {item}" for item in proof_items)
    if why:
        if parts:
            parts.append("")
        parts.extend(["## Why Arbisoft", "", why])
    if next_step:
        if parts:
            parts.append("")
        parts.extend(["## Next step", "", next_step])
    return "\n".join(parts).strip()


def _fallback_body(studies: list[dict[str, str]]) -> str:
    if not studies:
        return "No case studies were selected for this pitch."
    proof: list[str] = []
    for study in studies[:4]:
        snippet = (
            study["outcome"]
            or study["solution"]
            or study["challenge"]
            or study["tech_stack"]
            or study["title"]
        ).strip()
        if snippet:
            proof.append(snippet)
    return pitch_markdown_from_sections(
        {
            "hook": (
                "This prospect needs delivery support that matches the selected case-study "
                "experience."
            ),
            "what_we_will_do": (
                "We can apply the same patterns from comparable engagements to design and "
                "deliver a fit-for-purpose solution."
            ),
            "proof": proof
            or ["Selected case studies demonstrate relevant domain and delivery experience."],
            "why_arbisoft": (
                "Arbisoft brings seasoned product engineering teams with proven outcomes in "
                "similar contexts."
            ),
            "next_step": "Share priorities and constraints so we can outline a concrete plan.",
        }
    )
