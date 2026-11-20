"""Client names come from the structure LLM, then must appear in the study text."""

from __future__ import annotations

from src.decks import client_name as cn
from src.decks.client_name import accept_client_name, build_client_message, infer_client_name


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


def test_drops_vendor_names() -> None:
    assert accept_client_name("Arbisoft", "Arbisoft built the platform.") is None
    assert accept_client_name("Edly", "Edly partnered with Harvard.") is None


def test_build_client_message_includes_title_and_raw_text() -> None:
    message = build_client_message(
        title="Driving Global Science Education with Harvard’s LabXchange",
        raw_text="Harvard led the LabXchange science platform.",
    )
    assert "Case study title:" in message
    assert "Harvard’s LabXchange" in message
    assert "Case study raw_text:" in message
    assert "Harvard led the LabXchange" in message


def test_infer_client_uses_llm_reply_when_present_in_text(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        cn,
        "ask_client_name",
        lambda title, raw_text: "Harvard",
    )
    name = infer_client_name(
        "Driving Global Science Education with Harvard’s LabXchange",
        "Harvard led the LabXchange science platform.",
    )
    assert name == "Harvard"


def test_infer_client_empty_when_llm_says_none(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(cn, "ask_client_name", lambda title, raw_text: "")
    name = infer_client_name(
        "Enhancing Retail Analytics with Advanced Data Services",
        "The client needed to extract retail data from many websites.",
    )
    assert name is None


def test_infer_client_rejects_llm_hallucination(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(cn, "ask_client_name", lambda title, raw_text: "Acme Corp")
    name = infer_client_name(
        "Enhancing Retail Analytics with Advanced Data Services",
        "The client needed to extract retail data from many websites.",
    )
    assert name is None


def test_ask_client_name_calls_complete_json(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    calls: list[dict] = []

    def fake_complete(**kwargs):  # noqa: ANN003
        calls.append(kwargs)
        return {"client_name": "KAYAK"}

    def real_ask(title: str, raw_text: str) -> str:
        message = build_client_message(title=title, raw_text=raw_text)
        payload = cn._cached_complete(message)
        value = payload.get("client_name", "")
        return "" if value is None else str(value).strip()

    monkeypatch.setattr(cn, "_cache_path_for", lambda message: tmp_path / "client.json")
    monkeypatch.setattr(cn, "complete_json", fake_complete)
    monkeypatch.setattr(cn, "ask_client_name", real_ask)
    reply = cn.ask_client_name(
        "Elevating KAYAK's Mobile Experience",
        "KAYAK needed a faster mobile app.",
    )
    assert reply == "KAYAK"
    assert len(calls) == 1
    assert calls[0]["system"] == cn.CLIENT_SYSTEM_PROMPT
    assert "Case study title:" in calls[0]["user"]
    assert "KAYAK needed a faster mobile app." in calls[0]["user"]
    assert calls[0]["schema"] == cn.CLIENT_JSON_SCHEMA
    assert calls[0]["role"] == "structure"
    # Second call hits cache and does not call complete_json again.
    assert cn.ask_client_name(
        "Elevating KAYAK's Mobile Experience",
        "KAYAK needed a faster mobile app.",
    ) == "KAYAK"
    assert len(calls) == 1


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
