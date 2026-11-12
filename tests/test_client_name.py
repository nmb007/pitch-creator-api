"""Client names come from one model reply per study, and only when the study says that name."""

from types import SimpleNamespace

from src.config import settings
from src.decks.client_name import extract_client_name


def test_stores_name_the_study_already_says(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(
        "src.decks.client_name.chat_model",
        lambda: SimpleNamespace(invoke=lambda *_args, **_kwargs: SimpleNamespace(content="edX")),
    )
    name = extract_client_name(
        "Advancing edX with Arbisoft's Innovation in Online Learning",
        "edX is a global leader in online education.",
    )
    assert name == "edX"


def test_drops_name_the_study_does_not_say(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(
        "src.decks.client_name.chat_model",
        lambda: SimpleNamespace(
            invoke=lambda *_args, **_kwargs: SimpleNamespace(content="Philanthropy University")
        ),
    )
    name = extract_client_name(
        "Expanding Access to Education for Global Social Impact",
        "The work expanded access to education.",
    )
    assert name is None


def test_unchanged_study_skips_the_client_name_call(monkeypatch) -> None:  # noqa: ANN001
    from src.auth.models import User
    from src.decks import service as deck_service
    from src.decks.models import DeckImport
    from src.decks.parse import StudyDraft

    calls: list[str] = []

    def fake_name(title: str, raw_text: str) -> str:
        del raw_text
        calls.append(title)
        return "KAYAK"

    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(deck_service, "extract_client_name", fake_name)
    monkeypatch.setattr(
        deck_service,
        "embed_texts",
        lambda texts: [[0.1] * 384 for _ in texts],
    )
    draft = StudyDraft(
        section="Mobile App Development",
        title="KAYAK mobile",
        client_name=None,
        raw_text="KAYAK, a travel company, shipped search.",
        background="KAYAK, a travel company, shipped search.",
        challenge="",
        approach="",
        solution="",
        outcome="",
        tech_stack="",
    )
    with deck_service.get_sync_session() as session:
        user = User(username="person@example.com", password_hash="x", name="Person")
        session.add(user)
        session.flush()
        first = DeckImport(
            uploaded_by=user.id, file_name="v1.pptx", sha256="a" * 64, status="running"
        )
        second = DeckImport(
            uploaded_by=user.id, file_name="v2.pptx", sha256="b" * 64, status="running"
        )
        session.add(first)
        session.add(second)
        session.flush()
        deck_service.persist_drafts(session, first, [draft])
        assert calls == ["KAYAK mobile"]
        calls.clear()
        deck_service.persist_drafts(session, second, [draft])
        assert calls == []
