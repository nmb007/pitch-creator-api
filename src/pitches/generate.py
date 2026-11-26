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


def draft_page_content(pitch_id: UUID) -> dict[str, str]:
    """Draft title/body from the prospect brief and currently selected studies."""
    brief, studies = _load_brief_and_studies(pitch_id)
    drafted = _draft_pitch(pitch_id=pitch_id, brief=brief, studies=studies)
    title = (drafted.get("title") or "Pitch").strip()[:200] or "Pitch"
    body = (drafted.get("body") or "").strip()
    if not body:
        body = _fallback_body(studies)
    return {"title": title, "body": body}


_REVISE_BODY_SCHEMA: dict[str, Any] = {
    "title": "RevisedPitchBody",
    "type": "object",
    "properties": {
        "body": {
            "type": "string",
            "description": "Full revised pitch body in Markdown with the same ## section headings",
        },
    },
    "required": ["body"],
    "additionalProperties": False,
}

_REVISE_BODY_SYSTEM = """You revise an existing Arbisoft pitch page body.
Keep the same Markdown section headings when they still apply:
## Why this matters, ## How we can help, ## Relevant experience, ## Why Arbisoft, ## Next step.
Use only facts from the prospect brief and selected case studies.
Do not invent clients, metrics, technologies, or outcomes.
Do not list study titles in the body; the app lists studies separately.
Follow the user's instruction. If a section name is given, focus changes there but return the full body.
Return the complete revised body Markdown."""


def revise_body_content(
    *,
    pitch_id: UUID,
    current_title: str,
    current_body: str,
    instruction: str,
    section: str | None = None,
) -> str:
    """Rewrite pitch body from instruction, grounded in brief + selected studies."""
    brief, studies = _load_brief_and_studies(pitch_id)
    if not settings.GROQ_API_KEY:
        return current_body.strip()
    parts = [
        f"Prospect brief:\n{brief[:6000]}\n",
        f"Current title:\n{current_title}\n",
        f"Current body:\n{current_body}\n",
        f"Instruction:\n{instruction.strip()}",
    ]
    if section and section.strip():
        parts.append(f"Focus section:\n{section.strip()}")
    if studies:
        parts.append("Selected case studies:")
        for index, study in enumerate(studies, start=1):
            parts.append(
                f"\n{index}. {study['title']} ({study['section']})\n"
                f"Background: {study['background']}\n"
                f"Challenge: {study['challenge']}\n"
                f"Approach: {study['approach']}\n"
                f"Solution: {study['solution']}\n"
                f"Outcome: {study['outcome']}\n"
                f"Tech stack: {study['tech_stack']}"
            )
    try:
        raw = complete_json(
            system=_REVISE_BODY_SYSTEM,
            user="\n".join(parts),
            schema=_REVISE_BODY_SCHEMA,
            role="pitch",
            tags=[str(pitch_id), model_name_for("pitch")],
            metadata={"pitch_id": str(pitch_id), "model": model_name_for("pitch")},
            run_name="pitch_revise_body",
        )
    except Exception:
        logger.exception("Pitch body revise failed for %s", pitch_id)
        return current_body.strip()
    body = str(raw.get("body") or "").strip()
    return body or current_body.strip()


def append_page_revision(
    *,
    pitch_id: UUID,
    user_id: UUID,
    title: str,
    body: str,
    source: str,
    bump_updated_at: bool = True,
    assistant_message: str | None = None,
) -> None:
    """Append a pitch_revisions row and a new pitch_pages row."""
    clean_title = title.strip()[:200] or "Pitch"
    clean_body = body.strip() or "No case studies were selected for this pitch."
    with get_sync_session() as session:
        pitch = session.get(Pitch, pitch_id)
        if pitch is None:
            raise RuntimeError(f"Pitch {pitch_id} was missing during page persist")
        current = session.exec(
            select(PitchPage)
            .join(PitchRevision, PitchRevision.id == PitchPage.revision_id)
            .where(PitchRevision.pitch_id == pitch_id)
            .order_by(PitchRevision.created_at.desc())
        ).first()
        logo_key = current.prospect_logo_storage_key if current is not None else None
        revision = PitchRevision(pitch_id=pitch_id, source=source, created_by=user_id)
        session.add(revision)
        session.flush()
        session.add(
            PitchPage(
                revision_id=revision.id,
                title=clean_title,
                body=clean_body,
                prospect_logo_storage_key=logo_key,
            )
        )
        if assistant_message:
            session.add(PitchMessage(pitch_id=pitch_id, role="assistant", body=assistant_message))
        if bump_updated_at:
            pitch.updated_at = utcnow()
            session.add(pitch)
        session.commit()


def write_generated_pitch(*, pitch_id: UUID, user_id: UUID) -> dict[str, str]:
    """Create a revision and page from selected matches. Returns title and view URL."""
    drafted = draft_page_content(pitch_id)
    title = drafted["title"]
    body = drafted["body"]
    url = view_url(pitch_id)
    message = (
        f"Your pitch is ready: [{title}]({url})\n\n"
        "Open the link to review the tailored description and the selected case studies."
    )
    append_page_revision(
        pitch_id=pitch_id,
        user_id=user_id,
        title=title,
        body=body,
        source="generate",
        bump_updated_at=True,
        assistant_message=message,
    )
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
