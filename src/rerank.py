"""Local cross-encoder reranker. Tests replace ``rerank_texts`` so the model is not downloaded."""

from __future__ import annotations

from functools import lru_cache

from src.config import settings


@lru_cache(maxsize=1)
def _model():  # noqa: ANN202
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    return TextCrossEncoder(settings.RERANK_MODEL)


def rerank_texts(query: str, documents: list[str]) -> list[float]:
    """Score each document against the query. Higher means more relevant."""
    if not documents:
        return []
    # Leave headroom inside RERANK_MAX_LENGTH for the chunk text.
    capped = query.strip()[: settings.RERANK_QUERY_CHARS]
    return list(_model().rerank(capped, documents))
