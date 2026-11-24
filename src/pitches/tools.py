"""Retrieval tools for the pitch agent. Embed tool args, never pitches.title."""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import Any
from uuid import UUID

from langchain_core.tools import tool
from sqlalchemy import text
from sqlmodel import delete

from src.config import settings
from src.database import get_sync_session
from src.embeddings import embed_texts
from src.llm import chat_model
from src.pitches.grounding import ground_reason
from src.pitches.models import PitchMatch
from src.pitches.retrieval import (
    cap_chunks_per_study,
    chunk_part_name,
    escape_like,
    group_studies_by_best_chunk,
    rrf_merge,
)
from src.rerank import rerank_texts

logger = logging.getLogger(__name__)

MIN_QUERY_CHARS = 8

current_pitch_id: ContextVar[str] = ContextVar("current_pitch_id")
awaiting_selection: ContextVar[bool] = ContextVar("awaiting_selection", default=False)

_CHUNK_SELECT = """
    sc.id AS chunk_id,
    sc.text AS chunk_text,
    sv.id AS version_id,
    s.id AS study_id,
    s.title AS title,
    s.section AS category,
    sv.background AS background,
    sv.challenge AS challenge,
    sv.approach AS approach,
    sv.solution AS solution,
    sv.outcome AS outcome,
    sv.tech_stack AS tech_stack
"""


@tool
def browse_studies(query: str) -> str:
    """Find Arbisoft case studies matching a topic or capability (not a full prospect pitch).

    Results include winning passage chunks. Base every claim on those chunks.
    """
    cleaned = query.strip()
    if len(cleaned) < MIN_QUERY_CHARS:
        return json.dumps({"ok": False, "error": "Query too short", "studies": []})
    shortlist = _retrieve(cleaned)
    rows = _study_rows(shortlist)
    awaiting_selection.set(False)
    return json.dumps(
        {"ok": True, "mode": "browse", "study_count": len(rows), "studies": rows}
    )


@tool
def recommend_pitch_studies(brief: str) -> str:
    """Recommend the best case studies for a prospect brief or RFP-style pitch request.

    Results include winning passage chunks. Base every claim on those chunks.
    """
    cleaned = brief.strip()
    if len(cleaned) < MIN_QUERY_CHARS:
        return json.dumps({"ok": False, "error": "Brief too short", "studies": []})
    shortlist = _retrieve(cleaned)
    pitch_id = UUID(current_pitch_id.get())
    rows = _persist_matches(pitch_id, cleaned, shortlist)
    awaiting_selection.set(True)
    return json.dumps(
        {"ok": True, "mode": "pitch", "study_count": len(rows), "studies": rows}
    )


def pitch_tools() -> list[Any]:
    return [browse_studies, recommend_pitch_studies]


def _retrieve(query: str) -> list[dict[str, Any]]:
    vector = embed_texts([query])[0]
    literal = _vector_literal(vector)
    with get_sync_session() as session:
        vector_rows = session.execute(
            text(
                f"""
                SELECT {_CHUNK_SELECT},
                       (sc.embedding <=> CAST(:embedding AS vector)) AS distance
                FROM study_chunks AS sc
                JOIN study_versions AS sv
                  ON sv.id = sc.study_version_id AND sv.is_current
                JOIN studies AS s ON s.id = sv.study_id AND s.status = 'active'
                ORDER BY sc.embedding <=> CAST(:embedding AS vector)
                LIMIT :limit
                """
            ),
            {"embedding": literal, "limit": settings.RETRIEVAL_VECTOR_K},
        ).all()
        keyword_rows = session.execute(
            text(
                f"""
                SELECT {_CHUNK_SELECT},
                       ts_rank_cd(sc.text_search, plainto_tsquery('english', :q)) AS rank,
                       CASE
                         WHEN sc.text ILIKE '%' || :phrase || '%' ESCAPE '\\' THEN 1
                         ELSE 0
                       END AS phrase_hit
                FROM study_chunks AS sc
                JOIN study_versions AS sv
                  ON sv.id = sc.study_version_id AND sv.is_current
                JOIN studies AS s ON s.id = sv.study_id AND s.status = 'active'
                WHERE sc.text_search @@ plainto_tsquery('english', :q)
                ORDER BY phrase_hit DESC, rank DESC
                LIMIT :limit
                """
            ),
            {
                "q": query,
                "phrase": escape_like(query),
                "limit": settings.RETRIEVAL_KEYWORD_K,
            },
        ).all()

        by_id: dict[str, Any] = {}
        for row in (*vector_rows, *keyword_rows):
            chunk_id = str(row.chunk_id)
            if chunk_id in by_id:
                continue
            fields = {
                "background": row.background or "",
                "challenge": row.challenge or "",
                "approach": row.approach or "",
                "solution": row.solution or "",
                "outcome": row.outcome or "",
                "tech_stack": row.tech_stack or "",
            }
            by_id[chunk_id] = {
                "chunk_id": chunk_id,
                "study_id": str(row.study_id),
                "version_id": str(row.version_id),
                "title": row.title,
                "category": row.category,
                "text": row.chunk_text,
                "part": chunk_part_name(row.chunk_text, fields),
            }

        vector_ids = [str(row.chunk_id) for row in vector_rows]
        keyword_ids = [str(row.chunk_id) for row in keyword_rows]
        fused_ids = rrf_merge(
            vector_ids,
            keyword_ids,
            k=settings.RETRIEVAL_RRF_K,
        )
        capped_ids = cap_chunks_per_study(
            fused_ids,
            {chunk_id: by_id[chunk_id]["study_id"] for chunk_id in by_id},
            pool_limit=settings.RETRIEVAL_FUSED_POOL,
            max_per_study=settings.RETRIEVAL_MAX_CHUNKS_PER_STUDY,
        )
        candidates = [by_id[chunk_id] for chunk_id in capped_ids if chunk_id in by_id]
        if not candidates:
            return []

        scores = rerank_texts(query, [item["text"] for item in candidates])
        scored = [
            {**item, "score": float(score)}
            for item, score in zip(candidates, scores, strict=True)
        ]
        scored.sort(key=lambda item: item["score"], reverse=True)
        return group_studies_by_best_chunk(
            scored,
            study_limit=settings.RETRIEVAL_STUDY_LIMIT,
            max_chunks_per_study=settings.RETRIEVAL_MAX_CHUNKS_PER_STUDY,
            min_score=settings.RERANK_MIN_SCORE,
        )


def _study_rows(shortlist: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rank, item in enumerate(shortlist, start=1):
        rows.append(
            {
                "rank": rank,
                "study_id": item["study_id"],
                "category": item["category"],
                "title": item["title"],
                "chunks": list(item["chunks"]),
            }
        )
    return rows


def _persist_matches(
    pitch_id: UUID,
    query: str,
    shortlist: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    with get_sync_session() as session:
        session.execute(delete(PitchMatch).where(PitchMatch.pitch_id == pitch_id))
        rows: list[dict[str, Any]] = []
        for rank, item in enumerate(shortlist, start=1):
            chunks = list(item["chunks"])
            quote = "\n\n".join(chunk["text"] for chunk in chunks).strip()
            drafted = _draft_reason(query, item["title"], quote, str(pitch_id))
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
            rows.append(
                {
                    "rank": rank,
                    "study_id": item["study_id"],
                    "category": item["category"],
                    "title": item["title"],
                    "chunks": chunks,
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
