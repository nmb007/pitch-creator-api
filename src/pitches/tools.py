"""Retrieval tools for the pitch agent. Embed tool args, never pitches.title."""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import Any
from uuid import UUID

from langchain_core.tools import tool
from sqlalchemy import text
from sqlmodel import delete, select

from src.config import settings
from src.database import get_sync_session
from src.decks.models import StudyChunk
from src.embeddings import embed_texts
from src.llm import chat_model
from src.pitches.grounding import ground_reason
from src.pitches.models import PitchMatch

logger = logging.getLogger(__name__)

SUMMARY_CANDIDATES = 15
BROWSE_LIMIT = 5
PITCH_LIMIT = 5
CHUNK_LIMIT = 3
# Cosine distance via <=> ; lower is closer. Tunable against the live deck.
MAX_DISTANCE = 0.55
MIN_QUERY_CHARS = 8

current_pitch_id: ContextVar[str] = ContextVar("current_pitch_id")
awaiting_selection: ContextVar[bool] = ContextVar("awaiting_selection", default=False)


@tool
def browse_studies(query: str) -> str:
    """Find Arbisoft case studies matching a topic or capability (not a full prospect pitch)."""
    cleaned = query.strip()
    if len(cleaned) < MIN_QUERY_CHARS:
        return json.dumps({"ok": False, "error": "Query too short", "studies": []})
    shortlist = _retrieve(cleaned, candidate_limit=BROWSE_LIMIT, final_limit=BROWSE_LIMIT)
    pitch_id = UUID(current_pitch_id.get())
    rows = _persist_matches(pitch_id, cleaned, shortlist, explain=False)
    awaiting_selection.set(False)
    return json.dumps({"ok": True, "mode": "browse", "studies": rows})


@tool
def recommend_pitch_studies(brief: str) -> str:
    """Recommend the best case studies for a prospect brief or RFP-style pitch request."""
    cleaned = brief.strip()
    if len(cleaned) < MIN_QUERY_CHARS:
        return json.dumps({"ok": False, "error": "Brief too short", "studies": []})
    shortlist = _retrieve(
        cleaned,
        candidate_limit=SUMMARY_CANDIDATES,
        final_limit=PITCH_LIMIT,
        rerank_chunks=True,
    )
    pitch_id = UUID(current_pitch_id.get())
    rows = _persist_matches(pitch_id, cleaned, shortlist, explain=True)
    awaiting_selection.set(True)
    return json.dumps({"ok": True, "mode": "pitch", "studies": rows})


def pitch_tools() -> list[Any]:
    return [browse_studies, recommend_pitch_studies]


def _retrieve(
    query: str,
    *,
    candidate_limit: int,
    final_limit: int,
    rerank_chunks: bool = False,
) -> list[dict[str, Any]]:
    vector = embed_texts([query])[0]
    literal = _vector_literal(vector)
    with get_sync_session() as session:
        rows = session.execute(
            text(
                """
                SELECT sv.id AS version_id, s.id AS study_id, s.title, s.section,
                       (sv.summary_embedding <=> CAST(:embedding AS vector)) AS distance
                FROM study_versions AS sv
                JOIN studies AS s ON s.id = sv.study_id
                WHERE sv.is_current AND s.status = 'active'
                ORDER BY sv.summary_embedding <=> CAST(:embedding AS vector)
                LIMIT :limit
                """
            ),
            {"embedding": literal, "limit": candidate_limit},
        ).all()
        scored: list[dict[str, Any]] = []
        for row in rows:
            if row.distance is not None and float(row.distance) > MAX_DISTANCE:
                continue
            chunks, best_chunk_distance = _closest_chunks(session, row.version_id, literal)
            scored.append(
                {
                    "study_id": str(row.study_id),
                    "version_id": str(row.version_id),
                    "title": row.title,
                    "section": row.section,
                    "chunks": chunks,
                    "distance": float(row.distance) if row.distance is not None else 1.0,
                    "chunk_distance": best_chunk_distance,
                }
            )
        if rerank_chunks:
            scored.sort(key=lambda item: (item["chunk_distance"], item["distance"]))
        return scored[:final_limit]


def _closest_chunks(session, version_id, literal: str) -> tuple[list[dict[str, Any]], float]:  # noqa: ANN001
    chunks = session.execute(
        text(
            """
            SELECT position, text,
                   (embedding <=> CAST(:embedding AS vector)) AS distance
            FROM study_chunks
            WHERE study_version_id = :version_id
            ORDER BY embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
            """
        ),
        {"version_id": version_id, "embedding": literal, "limit": CHUNK_LIMIT},
    ).all()
    ordered = sorted(
        [{"position": chunk.position, "text": chunk.text} for chunk in chunks],
        key=lambda item: item["position"],
    )
    best = min((float(chunk.distance) for chunk in chunks), default=1.0)
    if ordered:
        return ordered, best
    stored = session.exec(
        select(StudyChunk)
        .where(StudyChunk.study_version_id == version_id)
        .order_by(StudyChunk.position)
    ).all()
    ordered = [{"position": chunk.position, "text": chunk.text} for chunk in stored[:CHUNK_LIMIT]]
    return ordered, 1.0


def _persist_matches(
    pitch_id: UUID,
    query: str,
    shortlist: list[dict[str, Any]],
    *,
    explain: bool,
) -> list[dict[str, Any]]:
    with get_sync_session() as session:
        session.execute(delete(PitchMatch).where(PitchMatch.pitch_id == pitch_id))
        rows: list[dict[str, Any]] = []
        for rank, item in enumerate(shortlist, start=1):
            quote = "\n\n".join(chunk["text"] for chunk in item["chunks"]).strip()
            if explain:
                drafted = _draft_reason(query, item["title"], quote, str(pitch_id))
                reason = ground_reason(drafted, quote) or _fallback(quote)
            else:
                reason = _fallback(quote) or f"Related to: {item['title']}"
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
            rows.append(
                {
                    "rank": rank,
                    "study_id": item["study_id"],
                    "section": item["section"],
                    "title": item["title"],
                    "reason": reason,
                    "source_quote": quote[:4000],
                }
            )
        session.commit()
        return rows


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
