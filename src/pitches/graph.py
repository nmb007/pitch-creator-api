"""Search graph. Week 1 runs through explain and then waits for a selection."""

from __future__ import annotations

import logging
from typing import Any, TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy import text
from sqlmodel import select

from src.config import settings
from src.database import get_sync_session
from src.decks.models import StudyChunk
from src.embeddings import embed_texts
from src.llm import chat_model
from src.models import utcnow
from src.pitches.grounding import ground_reason
from src.pitches.models import Pitch, PitchInput, PitchMatch, PitchMessage

logger = logging.getLogger(__name__)

SUMMARY_LIMIT = 15
CHUNK_LIMIT = 3


class PitchState(TypedDict, total=False):
    pitch_id: str
    shortlist: list[dict[str, Any]]


def compile_graph(checkpointer):  # noqa: ANN001, ANN202
    builder = StateGraph(PitchState)
    builder.add_node("read_input", read_input)
    builder.add_node("retrieve", retrieve)
    builder.add_node("explain", explain)
    builder.add_node("wait_for_selection", wait_for_selection)
    builder.add_edge(START, "read_input")
    builder.add_edge("read_input", "retrieve")
    builder.add_edge("retrieve", "explain")
    builder.add_edge("explain", "wait_for_selection")
    builder.add_edge("wait_for_selection", END)
    return builder.compile(checkpointer=checkpointer)


def read_input(state: PitchState) -> dict[str, Any]:
    pitch_id = UUID(state["pitch_id"])
    with get_sync_session() as session:
        pitch = session.get(Pitch, pitch_id)
        incoming = session.exec(select(PitchInput).where(PitchInput.pitch_id == pitch_id)).first()
        if pitch is None or incoming is None or not incoming.body:
            raise ValueError("Pitch is missing its starting input")
        pitch.prospect_text = incoming.body
        pitch.updated_at = utcnow()
        label = incoming.body
        if incoming.kind == "file" and incoming.file_name:
            label = f"Uploaded {incoming.file_name}\n\n{incoming.body}"
        session.add(PitchMessage(pitch_id=pitch_id, role="user", body=label))
        session.add(pitch)
        session.commit()
    return {}


def retrieve(state: PitchState) -> dict[str, Any]:
    pitch_id = UUID(state["pitch_id"])
    with get_sync_session() as session:
        pitch = session.get(Pitch, pitch_id)
        if pitch is None or not pitch.prospect_text:
            return {"shortlist": []}
        vector = embed_texts([pitch.prospect_text])[0]
        literal = _vector_literal(vector)
        rows = session.execute(
            text(
                """
                SELECT sv.id AS version_id, s.id AS study_id, s.title, s.section
                FROM study_versions AS sv
                JOIN studies AS s ON s.id = sv.study_id
                WHERE sv.is_current AND s.status = 'active'
                ORDER BY sv.summary_embedding <=> CAST(:embedding AS vector)
                LIMIT :limit
                """
            ),
            {"embedding": literal, "limit": SUMMARY_LIMIT},
        ).all()
        shortlist: list[dict[str, Any]] = []
        for row in rows:
            chunks = session.execute(
                text(
                    """
                    SELECT position, text
                    FROM study_chunks
                    WHERE study_version_id = :version_id
                    ORDER BY embedding <=> CAST(:embedding AS vector)
                    LIMIT :limit
                    """
                ),
                {
                    "version_id": row.version_id,
                    "embedding": literal,
                    "limit": CHUNK_LIMIT,
                },
            ).all()
            ordered = sorted(
                [{"position": chunk.position, "text": chunk.text} for chunk in chunks],
                key=lambda item: item["position"],
            )
            if not ordered:
                stored = session.exec(
                    select(StudyChunk)
                    .where(StudyChunk.study_version_id == row.version_id)
                    .order_by(StudyChunk.position)
                ).all()
                ordered = [
                    {"position": chunk.position, "text": chunk.text}
                    for chunk in stored[:CHUNK_LIMIT]
                ]
            shortlist.append(
                {
                    "study_id": str(row.study_id),
                    "version_id": str(row.version_id),
                    "title": row.title,
                    "section": row.section,
                    "chunks": ordered,
                }
            )
    return {"shortlist": shortlist}


def explain(state: PitchState) -> dict[str, Any]:
    pitch_id = UUID(state["pitch_id"])
    with get_sync_session() as session:
        pitch = session.get(Pitch, pitch_id)
        if pitch is None:
            return {}
        prospect = pitch.prospect_text
        lines: list[str] = []
        for rank, item in enumerate(state.get("shortlist") or [], start=1):
            quote = "\n\n".join(chunk["text"] for chunk in item["chunks"]).strip()
            drafted = _draft_reason(prospect, item["title"], quote, str(pitch_id))
            reason = ground_reason(drafted, quote) or _fallback(quote)
            session.add(
                PitchMatch(
                    pitch_id=pitch_id,
                    study_id=UUID(item["study_id"]),
                    study_version_id=UUID(item["version_id"]),
                    rank=rank,
                    reason=reason,
                    source_quote=quote[:4000],
                    selected=False,
                )
            )
            lines.append(f"{rank}. {item['section']} — {item['title']}: {reason}")
        if lines:
            body = "Closest studies:\n" + "\n".join(lines)
        else:
            body = "No active studies were close to this prospect."
        session.add(PitchMessage(pitch_id=pitch_id, role="assistant", body=body))
        pitch.status = "selecting"
        pitch.updated_at = utcnow()
        session.add(pitch)
        session.commit()
    return {}


def wait_for_selection(state: PitchState) -> dict[str, Any]:
    interrupt({"pitch_id": state["pitch_id"]})
    return {}


def _draft_reason(prospect: str, title: str, quote: str, pitch_id: str) -> str:
    if not quote:
        return ""
    if not settings.GROQ_API_KEY:
        return _fallback(quote)
    prompt = (
        "Explain why this case study might fit the prospect. "
        "Use only facts that appear in the passages. One or two sentences.\n\n"
        f"Prospect:\n{prospect[:4000]}\n\n"
        f"Study: {title}\n\n"
        f"Passages:\n{quote[:6000]}"
    )
    try:
        reply = chat_model().invoke(
            prompt,
            config={
                "metadata": {"pitch_id": pitch_id, "model": settings.GROQ_MODEL},
                "tags": [pitch_id, settings.GROQ_MODEL],
            },
        )
    except Exception:
        logger.exception("Groq reason failed for pitch %s", pitch_id)
        return _fallback(quote)
    content = reply.content
    if isinstance(content, str):
        return content.strip()
    return str(content).strip()


def _fallback(quote: str) -> str:
    sentence = quote.strip().split("\n", maxsplit=1)[0]
    return sentence[:400]


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"
