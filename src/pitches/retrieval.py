"""Hybrid chunk retrieval helpers: RRF, per-study caps, and study grouping."""

from __future__ import annotations

from typing import Any

from src.decks.drafts import FIELD_NAMES


def rrf_merge(*ranked_id_lists: list[str], k: int = 60) -> list[str]:
    """Merge ranked id lists by reciprocal rank fusion. Higher fused score wins."""
    scores: dict[str, float] = {}
    for ranked in ranked_id_lists:
        for rank, item_id in enumerate(ranked, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda item_id: scores[item_id], reverse=True)


def cap_chunks_per_study(
    ordered_chunk_ids: list[str],
    chunk_study: dict[str, str],
    *,
    pool_limit: int,
    max_per_study: int,
) -> list[str]:
    """Keep fused order, at most max_per_study chunks per study, up to pool_limit total."""
    kept: list[str] = []
    per_study: dict[str, int] = {}
    for chunk_id in ordered_chunk_ids:
        study_id = chunk_study[chunk_id]
        count = per_study.get(study_id, 0)
        if count >= max_per_study:
            continue
        kept.append(chunk_id)
        per_study[study_id] = count + 1
        if len(kept) >= pool_limit:
            break
    return kept


def chunk_part_name(text: str, fields: dict[str, str]) -> str:
    """Map chunk text to the study field it came from."""
    for name in FIELD_NAMES:
        if fields.get(name) == text:
            return name
    return "body"


def group_studies_by_best_chunk(
    scored_chunks: list[dict[str, Any]],
    *,
    study_limit: int,
    max_chunks_per_study: int,
    min_score: float,
) -> list[dict[str, Any]]:
    """Group reranked chunks into studies ordered by each study's best chunk score."""
    by_study: dict[str, dict[str, Any]] = {}
    for chunk in scored_chunks:
        study_id = chunk["study_id"]
        score = float(chunk["score"])
        entry = by_study.get(study_id)
        payload = {"part": chunk["part"], "text": chunk["text"], "score": score}
        if entry is None:
            by_study[study_id] = {
                "study_id": study_id,
                "version_id": chunk["version_id"],
                "title": chunk["title"],
                "category": chunk["category"],
                "best_score": score,
                "chunks": [payload],
            }
            continue
        entry["chunks"].append(payload)
        if score > entry["best_score"]:
            entry["best_score"] = score

    ranked = sorted(by_study.values(), key=lambda item: item["best_score"], reverse=True)
    selected: list[dict[str, Any]] = []
    for item in ranked:
        if item["best_score"] < min_score:
            continue
        item["chunks"].sort(key=lambda chunk: chunk["score"], reverse=True)
        item["chunks"] = [
            {"part": chunk["part"], "text": chunk["text"]}
            for chunk in item["chunks"][:max_chunks_per_study]
        ]
        selected.append(item)
        if len(selected) >= study_limit:
            break
    return selected


def escape_like(value: str) -> str:
    """Escape % and _ for ILIKE patterns."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
