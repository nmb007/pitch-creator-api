"""Local embedding model. Tests replace ``embed_texts`` so the model is not downloaded."""

from __future__ import annotations

from functools import lru_cache

from src.config import settings


@lru_cache(maxsize=1)
def _model():  # noqa: ANN202
    from fastembed import TextEmbedding

    return TextEmbedding(settings.EMBEDDING_MODEL)


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    return [vector.tolist() for vector in _model().embed(texts)]
