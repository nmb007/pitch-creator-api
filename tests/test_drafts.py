"""Study draft helpers for chunks and empty bodies."""

from src.decks.drafts import StudyDraft, chunk_texts


def _draft(**overrides: object) -> StudyDraft:
    base = StudyDraft(
        section="General",
        title="Example",
        client_name=None,
        raw_text="",
        background="",
        challenge="",
        approach="",
        solution="",
        outcome="",
        tech_stack="",
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_chunk_texts_uses_non_empty_fields() -> None:
    draft = _draft(background="Client background.", challenge="Hard problem.")
    assert chunk_texts(draft) == ["Client background.", "Hard problem."]


def test_chunk_texts_falls_back_to_raw_text() -> None:
    draft = _draft(raw_text="Only raw body.")
    assert chunk_texts(draft) == ["Only raw body."]


def test_chunk_texts_empty_when_no_body() -> None:
    assert chunk_texts(_draft()) == []


def test_persist_skips_summary_and_chunks_when_body_empty(monkeypatch) -> None:  # noqa: ANN001
    from sqlmodel import select
    from src.auth.models import User
    from src.decks import service as deck_service
    from src.decks.models import DeckImport, Study, StudyChunk, StudyVersion

    calls: list[list[str]] = []

    def fake_embed(texts: list[str]) -> list[list[float]]:
        calls.append(list(texts))
        return [[0.1] * 384 for _ in texts]

    monkeypatch.setattr(deck_service, "embed_texts", fake_embed)
    empty = _draft(
        section="Axim Proposals",
        title="Learner Group (Clear Result Inc)",
        raw_text="",
    )
    with deck_service.get_sync_session() as session:
        user = User(username="person@example.com", password_hash="x", name="Person")
        session.add(user)
        session.flush()
        deck = DeckImport(
            uploaded_by=user.id, file_name="v1.pptx", sha256="a" * 64, status="running"
        )
        session.add(deck)
        session.flush()
        deck_service.persist_drafts(session, deck, [empty])
        session.commit()

        study = session.exec(select(Study)).one()
        version = session.exec(
            select(StudyVersion).where(StudyVersion.study_id == study.id)
        ).one()
        chunks = list(session.exec(select(StudyChunk)).all())

    assert study.title == "Learner Group (Clear Result Inc)"
    assert version.raw_text == ""
    assert version.summary_embedding == [0.0] * 384
    assert chunks == []
    assert calls == []
