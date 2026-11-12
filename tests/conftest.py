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
os.environ["GROQ_API_KEY"] = ""
os.environ["LANGSMITH_API_KEY"] = ""
os.environ["SEED_USER_EMAIL"] = ""
os.environ["SEED_USER_PASSWORD"] = ""

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
    async def fake_start(pitch_id) -> None:  # noqa: ANN001
        del pitch_id

    def fake_enqueue(deck_id) -> None:  # noqa: ANN001
        del deck_id

    monkeypatch.setattr(pitch_service, "start_search", fake_start)
    monkeypatch.setattr(deck_service, "enqueue_ingest", fake_enqueue)


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as api:
        yield api
