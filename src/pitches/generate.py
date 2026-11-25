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
            "description": "Short pitch title for the prospect",
        },
        "body": {
            "type": "string",
            "description": "One tailored pitch description grounded in the selected studies",
        },
    },
    "required": ["title", "body"],
    "additionalProperties": False,
}

_SYSTEM = """You write one tailored pitch for a prospect from Arbisoft case studies.
Use only facts that appear in the selected studies.
Do not invent clients, metrics, technologies, or outcomes.
Write a concise title and a single body paragraph (or a few short paragraphs).
Do not list the studies by name in the body; the app lists them separately.
Keep the tone professional."""


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
                    "raw_text": (version.raw_text or "")[:6000],
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
        parts.append(
            f"\n{index}. {study['title']} ({study['section']})\n"
            f"Background: {study['background']}\n"
            f"Challenge: {study['challenge']}\n"
            f"Approach: {study['approach']}\n"
            f"Solution: {study['solution']}\n"
            f"Outcome: {study['outcome']}\n"
            f"Tech stack: {study['tech_stack']}\n"
            f"Source text:\n{study['raw_text']}"
        )
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
        "title": str(raw.get("title") or "Pitch"),
        "body": str(raw.get("body") or ""),
    }


def _fallback_body(studies: list[dict[str, str]]) -> str:
    if not studies:
        return "No case studies were selected for this pitch."
    lines = ["Arbisoft can support this prospect with proven delivery across similar engagements:"]
    for study in studies:
        snippet = (
            study["outcome"]
            or study["solution"]
            or study["challenge"]
            or study["raw_text"][:240]
            or study["title"]
        ).strip()
        lines.append(f"- {study['title']}: {snippet}")
    return "\n".join(lines)
