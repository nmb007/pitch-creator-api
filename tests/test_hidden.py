"""A study missing from the new file is hidden and left out of search."""

from sqlmodel import select
from src.auth.models import User
from src.decks import service as deck_service
from src.decks.drafts import StudyDraft
from src.decks.models import DeckImport, Study


def test_hidden_studies_are_excluded(monkeypatch) -> None:  # noqa: ANN001
    calls: list[int] = []

    def fake_embed(texts: list[str]) -> list[list[float]]:
        calls.append(len(texts))
        return [[0.1] * 384 for _ in texts]

    monkeypatch.setattr(deck_service, "embed_texts", fake_embed)
    kayak = _draft(
        "Mobile App Development", "KAYAK mobile", "KAYAK, a travel company, shipped search."
    )
    wanderu = _draft("Ecommerce", "Wanderu", "Wanderu, a ground travel company, added carriers.")
    with deck_service.get_sync_session() as session:
        user = User(username="person@example.com", password_hash="x", name="Person")
        session.add(user)
        session.flush()
        first = DeckImport(
            uploaded_by=user.id, file_name="v1.pptx", sha256="a" * 64, status="running"
        )
        session.add(first)
        session.flush()
        deck_service.persist_drafts(session, first, [kayak, wanderu])
        session.commit()
        assert calls == [4]

        second = DeckImport(
            uploaded_by=user.id, file_name="v2.pptx", sha256="b" * 64, status="running"
        )
        session.add(second)
        session.flush()
        calls.clear()
        deck_service.persist_drafts(session, second, [kayak])
        session.commit()
        assert calls == []

        hidden = session.exec(select(Study).where(Study.status == "hidden")).all()
        assert [study.title for study in hidden] == ["Wanderu"]

    active = deck_service.list_active_studies()
    assert {study.title for study in active} == {"KAYAK mobile"}


def _draft(section: str, title: str, raw: str) -> StudyDraft:
    return StudyDraft(
        section=section,
        title=title,
        client_name=None,
        raw_text=raw,
        background=raw,
        challenge="",
        approach="",
        solution="",
        outcome="",
        tech_stack="",
    )
