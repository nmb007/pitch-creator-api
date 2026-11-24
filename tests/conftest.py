"""Shared test fixtures. Route tests use SQLite and never call Postgres, Redis, or Groq."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

os.environ["ENVIRONMENT"] = "test"
os.environ["AUTH_JWT_SECRET"] = "test-secret-at-least-32-characters-long"
os.environ["DATABASE_URL"] = "postgresql+psycopg://postgres:postgres@localhost:5433/pitch"
os.environ["CELERY_BROKER_URL"] = "memory://"
os.environ["STORAGE_DIR"] = "storage"
os.environ["CORS_ALLOW_ORIGINS"] = "http://localhost:3000"
os.environ["GROQ_API_KEY"] = ""
os.environ["GROQ_MODEL"] = "openai/gpt-oss-120b"
os.environ["GROQ_BASE_URL"] = "https://api.groq.com/openai/v1"
os.environ["OPENAI_API_KEY"] = "ollama"
os.environ["OPENAI_BASE_URL"] = "http://127.0.0.1:11434/v1"
os.environ["LANGSMITH_API_KEY"] = ""
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGSMITH_ENDPOINT"] = ""
os.environ["LANGSMITH_AGENT_ID"] = ""
os.environ["LANGSMITH_AGENT_ENVIRONMENT"] = ""
os.environ.pop("LANGSMITH_PROJECT", None)
os.environ.pop("LANGCHAIN_PROJECT", None)
os.environ.pop("LANGCHAIN_SESSION", None)
os.environ["SEED_USER_EMAIL"] = ""
os.environ["SEED_USER_PASSWORD"] = ""
os.environ["SEED_USER_NAME"] = "Demo User"
os.environ["EMBEDDING_MODEL"] = "BAAI/bge-small-en-v1.5"
os.environ["EMBEDDING_DIMENSIONS"] = "384"
os.environ["RETRIEVAL_VECTOR_K"] = "40"
os.environ["RETRIEVAL_KEYWORD_K"] = "40"
os.environ["RETRIEVAL_FUSED_POOL"] = "40"
os.environ["RETRIEVAL_MAX_CHUNKS_PER_STUDY"] = "3"
os.environ["RETRIEVAL_STUDY_LIMIT"] = "15"
os.environ["RETRIEVAL_RRF_K"] = "60"
os.environ["RERANK_MODEL"] = "BAAI/bge-reranker-v2-m3"
os.environ["RERANK_MAX_LENGTH"] = "1024"
os.environ["RERANK_QUERY_CHARS"] = "1500"
os.environ["RERANK_MIN_SCORE"] = "-5.0"
os.environ["STRUCTURE_LLM_PROVIDER"] = "openai"
os.environ["STRUCTURE_LLM_MODEL"] = "qwen3.5:9b"
os.environ["STRUCTURE_LLM_REASONING_EFFORT"] = ""
os.environ["STRUCTURE_LLM_MAX_TOKENS"] = "4096"
os.environ["STRUCTURE_LLM_RETRY_LIMIT"] = "2"
os.environ["STRUCTURE_LLM_RETRY_BASE_SECONDS"] = "1"
os.environ["STRUCTURE_MODEL_REPO"] = "Qwen/Qwen2.5-7B-Instruct-GGUF"
os.environ["STRUCTURE_MODEL_FILE"] = "qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf"
os.environ["STRUCTURE_MODEL_THREADS"] = "4"
os.environ["STRUCTURE_BATCH_SLIDES"] = "2"

from src.auth import service as auth_service  # noqa: E402
from src.config import settings  # noqa: E402
from src.database import get_sync_session as real_get_sync_session  # noqa: E402
from src.decks import service as deck_service  # noqa: E402
from src.main import app  # noqa: E402
from src.pitches import graph as pitch_graph  # noqa: E402
from src.pitches import service as pitch_service  # noqa: E402

del real_get_sync_session


@pytest.fixture(autouse=True)
def _storage(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path))


@pytest.fixture(autouse=True)
def _sync_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    @contextmanager
    def get_session():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(auth_service, "get_sync_session", get_session)
    monkeypatch.setattr(deck_service, "get_sync_session", get_session)
    monkeypatch.setattr(pitch_service, "get_sync_session", get_session)
    monkeypatch.setattr(pitch_graph, "get_sync_session", get_session)
    try:
        yield
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _stub_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_enqueue(deck_id) -> None:  # noqa: ANN001
        del deck_id

    monkeypatch.setattr(deck_service, "enqueue_ingest", fake_enqueue)


@pytest.fixture(autouse=True)
def _stub_client_name_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse/audit tests must not call Groq. Unit tests can override ask_client_name."""
    from src.decks import client_name as client_name_mod

    # Longer names first so "Braces on Demand" wins over a shorter fragment.
    known = (
        "Braces on Demand",
        "CLEAResult",
        "Toastmasters",
        "Wikimedia",
        "Wanderu",
        "Harvard",
        "Landit",
        "KAYAK",
        "Audacy",
        "Caliper",
        "edX",
        "MIT",
        "ALW",
        "Rizq",
    )

    def fake_ask(title: str, raw_text: str, **_kwargs: object) -> str:
        source = f"{title}\n{raw_text}"
        lowered = source.lower()
        if "the client" in lowered and not any(name.lower() in lowered for name in known):
            return ""
        for name in known:
            if name.lower() in lowered:
                return name
        return ""

    monkeypatch.setattr(client_name_mod, "ask_client_name", fake_ask)


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as api:
        yield api
