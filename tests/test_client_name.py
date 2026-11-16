"""Client names stay only when the study blocks already say that name."""

from src.decks.client_name import accept_client_name


def test_stores_name_the_study_already_says() -> None:
    name = accept_client_name(
        "edX",
        "Advancing edX with Arbisoft's Innovation in Online Learning\n"
        "edX is a global leader in online education.",
    )
    assert name == "edX"


def test_drops_name_the_study_does_not_say() -> None:
    name = accept_client_name(
        "Philanthropy University",
        "Expanding Access to Education for Global Social Impact\n"
        "The work expanded access to education.",
    )
    assert name is None


def test_unchanged_study_keeps_client_name_without_rewrite(monkeypatch) -> None:  # noqa: ANN001
    from sqlmodel import select
    from src.auth.models import User
    from src.decks import service as deck_service
    from src.decks.drafts import StudyDraft
    from src.decks.models import DeckImport, Study

    monkeypatch.setattr(
        deck_service,
        "embed_texts",
        lambda texts: [[0.1] * 384 for _ in texts],
    )
    draft = StudyDraft(
        section="Mobile App Development",
        title="KAYAK mobile",
        client_name="KAYAK",
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
        study = session.exec(select(Study)).one()
        assert study.client_name == "KAYAK"
        draft_again = StudyDraft(
            section=draft.section,
            title=draft.title,
            client_name="Other",
            raw_text=draft.raw_text,
            background=draft.background,
            challenge="",
            approach="",
            solution="",
            outcome="",
            tech_stack="",
        )
        deck_service.persist_drafts(session, second, [draft_again])
        session.refresh(study)
        assert study.client_name == "KAYAK"
