"""Chat refine tools and routing after a pitch page exists."""

from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from sqlmodel import select
from src.auth import service as auth_service
from src.decks.models import DeckImport, Study, StudyVersion
from src.exceptions import ConflictError
from src.pitches import generate as generate_mod
from src.pitches import graph as pitch_graph
from src.pitches import policy as policy_mod
from src.pitches import revise_tools as revise_tools_mod
from src.pitches import service as pitch_service
from src.pitches.models import Pitch, PitchInput, PitchMatch, PitchPage, PitchRevision
from src.pitches.tools import current_pitch_id


def _seed_page_with_matches(user_id, *, selected_count: int = 1, total: int = 2):  # noqa: ANN001
    with pitch_service.get_sync_session() as session:
        pitch = Pitch(created_by=user_id, title="Refine prospect")
        session.add(pitch)
        session.flush()
        session.add(
            PitchInput(
                pitch_id=pitch.id,
                kind="message",
                body="A healthcare company needs a patient portal.",
                created_by=user_id,
            )
        )
        deck = DeckImport(
            uploaded_by=user_id,
            file_name="deck.pptx",
            sha256="b" * 64,
            status="completed",
        )
        session.add(deck)
        session.flush()
        study_ids = []
        titles = [
            "Healthcare Portal Rebuild",
            "Landit Mobile Delivery",
            "Fintech Payments Wallet",
        ]
        for index in range(total):
            study = Study(
                section="Health" if index == 0 else "Mobile",
                title=titles[index % len(titles)] + f" {uuid4().hex[:6]}",
                status="active",
            )
            session.add(study)
            session.flush()
            version = StudyVersion(
                study_id=study.id,
                deck_import_id=deck.id,
                slide_numbers=[index + 1],
                content_hash=f"hash-{uuid4().hex}",
                background="Background text.",
                challenge="Challenge text.",
                approach="Approach text.",
                solution="Solution text.",
                outcome="Outcome text.",
                tech_stack="React",
                raw_text="Full study text.",
                summary_embedding=[0.0] * 384,
                is_current=True,
            )
            session.add(version)
            session.flush()
            session.add(
                PitchMatch(
                    pitch_id=pitch.id,
                    study_id=study.id,
                    study_version_id=version.id,
                    rank=index + 1,
                    reason="Fits the brief.",
                    source_quote="Source quote.",
                    selected=index < selected_count,
                )
            )
            study_ids.append(study.id)
        revision = PitchRevision(pitch_id=pitch.id, source="generate", created_by=user_id)
        session.add(revision)
        session.flush()
        session.add(
            PitchPage(
                revision_id=revision.id,
                title="Original Title",
                body="## Why this matters\n\nOriginal body.",
            )
        )
        session.commit()
        return pitch.id, study_ids


def test_find_pitch_match_studies_title_only_includes_unselected() -> None:
    user = auth_service.ensure_user(
        username="revise-find@example.com",
        password="correct-password",
        name="Revise Find",
    )
    pitch_id, study_ids = _seed_page_with_matches(user.id, selected_count=1, total=2)
    with pitch_service.get_sync_session() as session:
        titles = {
            row.id: row.title
            for row in session.exec(select(Study).where(Study.id.in_(study_ids))).all()
        }

    hits = pitch_service.find_pitch_match_studies(pitch_id=pitch_id, query="Landit")
    landit_title = titles[study_ids[1]]
    assert any(hit["study_id"] == str(study_ids[1]) for hit in hits)
    assert all("rank" not in hit for hit in hits)
    match = next(hit for hit in hits if hit["study_id"] == str(study_ids[1]))
    assert match["selected"] is False
    assert match["title"] == landit_title

    health = pitch_service.find_pitch_match_studies(pitch_id=pitch_id, query="Healthcare")
    assert len(health) == 1
    assert health[0]["selected"] is True

    none = pitch_service.find_pitch_match_studies(pitch_id=pitch_id, query="zzz-no-match")
    assert none == []


def test_add_page_study_selects_and_optional_chat_rewrite(monkeypatch) -> None:  # noqa: ANN001
    user = auth_service.ensure_user(
        username="revise-add@example.com",
        password="correct-password",
        name="Revise Add",
    )
    pitch_id, study_ids = _seed_page_with_matches(user.id, selected_count=1, total=2)
    page = pitch_service.add_page_study(
        pitch_id=pitch_id,
        user_id=user.id,
        study_id=study_ids[1],
        rewrite=False,
        source="chat",
    )
    assert len(page["studies"]) == 2

    monkeypatch.setattr(
        pitch_service,
        "draft_page_content",
        lambda pitch_id: {
            "title": "After Add",
            "body": "## Why this matters\n\nRewritten with new study.",
        },
    )
    # Already selected → conflict
    try:
        pitch_service.add_page_study(
            pitch_id=pitch_id,
            user_id=user.id,
            study_id=study_ids[1],
            rewrite=False,
            source="chat",
        )
        raise AssertionError("expected ConflictError")
    except ConflictError:
        pass

    # Fresh unselected for rewrite path: deselect first then add with rewrite
    pitch_service.remove_page_study(
        pitch_id=pitch_id,
        user_id=user.id,
        study_id=study_ids[1],
        rewrite=False,
        source="chat",
    )
    rewritten = pitch_service.add_page_study(
        pitch_id=pitch_id,
        user_id=user.id,
        study_id=study_ids[1],
        rewrite=True,
        source="chat",
    )
    assert rewritten["title"] == "After Add"
    with pitch_service.get_sync_session() as session:
        sources = [
            row.source
            for row in session.exec(
                select(PitchRevision)
                .where(PitchRevision.pitch_id == pitch_id)
                .order_by(PitchRevision.created_at)
            ).all()
        ]
        assert "chat" in sources
        assert sources[-1] == "chat"


def test_remove_update_regenerate_use_chat_source(monkeypatch) -> None:  # noqa: ANN001
    user = auth_service.ensure_user(
        username="revise-mut@example.com",
        password="correct-password",
        name="Revise Mut",
    )
    pitch_id, study_ids = _seed_page_with_matches(user.id, selected_count=2, total=2)
    monkeypatch.setattr(
        pitch_service,
        "draft_page_content",
        lambda pitch_id: {
            "title": "Regenerated",
            "body": "## Why this matters\n\nFresh draft.",
        },
    )
    monkeypatch.setattr(
        generate_mod,
        "revise_body_content",
        lambda **kwargs: "## Why this matters\n\nShorter body.",
    )

    pitch_service.update_page(
        pitch_id=pitch_id,
        user_id=user.id,
        title="Chat Title",
        body="## Why this matters\n\nChat body.",
        source="chat",
    )
    pitch_service.remove_page_study(
        pitch_id=pitch_id,
        user_id=user.id,
        study_id=study_ids[0],
        rewrite=True,
        source="chat",
    )
    page = pitch_service.regenerate_page(pitch_id=pitch_id, user_id=user.id, source="chat")
    assert page["title"] == "Regenerated"

    import json

    token = current_pitch_id.set(str(pitch_id))
    try:
        body_result = revise_tools_mod.update_body.invoke(
            {"instruction": "Make it shorter", "section": ""}
        )
    finally:
        current_pitch_id.reset(token)
    assert json.loads(body_result)["ok"] is True

    with pitch_service.get_sync_session() as session:
        chat_revisions = list(
            session.exec(
                select(PitchRevision).where(
                    PitchRevision.pitch_id == pitch_id,
                    PitchRevision.source == "chat",
                )
            ).all()
        )
        assert len(chat_revisions) >= 3


def test_decide_gate_revise_prompt_when_has_page(monkeypatch) -> None:  # noqa: ANN001
    systems: list[str] = []

    def fake_complete_json(**kwargs: object) -> dict[str, str]:
        systems.append(str(kwargs.get("system") or ""))
        return {"decision": "allow_tools", "reply": ""}

    monkeypatch.setattr(policy_mod, "complete_json", fake_complete_json)
    decision, reply = policy_mod.decide_gate(
        pitch_id="test",
        messages=[HumanMessage(content="Make the title shorter")],
        has_page=True,
    )
    assert decision == "allow_tools"
    assert reply == ""
    assert "refine" in systems[0].lower() or "pitch page" in systems[0].lower()


def test_graph_routes_to_revise_not_recommend_when_page_exists(monkeypatch) -> None:  # noqa: ANN001
    invoke_count = {"n": 0}
    bound_names: list[set[str]] = []

    class TwoStepModel:
        def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
            del kwargs
            names = {
                getattr(tool, "name", None) or getattr(tool, "__name__", "") for tool in tools
            }
            bound_names.append(names)
            assert "recommend_pitch_studies" not in names
            assert "browse_studies" not in names
            assert "find_pitch_studies" in names
            return self

        def invoke(self, messages, config=None):  # noqa: ANN001
            del messages, config
            invoke_count["n"] += 1
            if invoke_count["n"] == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "find_pitch_studies",
                            "args": {"query": "Healthcare"},
                            "id": "c1",
                            "type": "tool_call",
                        }
                    ],
                )
            return AIMessage(content="Found the healthcare study on this pitch.")

    monkeypatch.setattr(pitch_graph, "chat_model", lambda: TwoStepModel())
    monkeypatch.setattr(pitch_graph, "decide_gate", lambda **kwargs: ("allow_tools", ""))
    monkeypatch.setattr(
        pitch_service,
        "find_pitch_match_studies",
        lambda **kwargs: [
            {
                "study_id": str(uuid4()),
                "title": "Healthcare Portal",
                "section": "Health",
                "selected": True,
            }
        ],
    )

    user = auth_service.ensure_user(
        username="revise-route@example.com",
        password="correct-password",
        name="Revise Route",
    )
    pitch_id, _study_ids = _seed_page_with_matches(user.id, selected_count=1, total=2)

    graph = pitch_graph.compile_graph(MemorySaver())
    result = graph.invoke(
        {
            "pitch_id": str(pitch_id),
            "messages": [HumanMessage(content="Find the healthcare study")],
            "awaiting_selection": False,
        },
        {"configurable": {"thread_id": str(pitch_id)}},
    )
    assert bound_names
    assert invoke_count["n"] >= 2
    assert any(
        getattr(msg, "content", None) and "healthcare" in str(msg.content).lower()
        for msg in result.get("messages") or []
        if isinstance(msg, AIMessage)
    )


def test_find_and_add_tools_via_context() -> None:
    user = auth_service.ensure_user(
        username="revise-tools@example.com",
        password="correct-password",
        name="Revise Tools",
    )
    pitch_id, study_ids = _seed_page_with_matches(user.id, selected_count=1, total=2)
    token = current_pitch_id.set(str(pitch_id))
    try:
        import json

        found = json.loads(
            revise_tools_mod.find_pitch_studies.invoke({"query": "Landit"})
        )
        assert found["ok"] is True
        assert found["study_count"] >= 1
        target = found["studies"][0]["study_id"]
        assert found["studies"][0]["selected"] is False

        added = json.loads(
            revise_tools_mod.add_study.invoke({"study_id": target, "rewrite": False})
        )
        assert added["ok"] is True
        assert added["study_count"] == 2

        again = json.loads(
            revise_tools_mod.add_study.invoke({"study_id": target, "rewrite": False})
        )
        assert again["ok"] is False
    finally:
        current_pitch_id.reset(token)

    page = pitch_service.get_page(pitch_id, user.id)
    assert any(str(row["study_id"]) == str(study_ids[1]) for row in page["studies"])
