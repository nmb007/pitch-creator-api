"""Select studies and generate a pitch page."""

from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from sqlmodel import select
from src.auth import service as auth_service
from src.decks.models import DeckImport, Study, StudyVersion
from src.pitches import generate as generate_mod
from src.pitches import graph as pitch_graph
from src.pitches import service as pitch_service
from src.pitches import tools as pitch_tools
from src.pitches.models import Pitch, PitchInput, PitchMatch


async def _token(client) -> str:  # noqa: ANN001
    auth_service.ensure_user(
        username="select@example.com",
        password="correct-password",
        name="Select Person",
    )
    response = await client.post(
        "/v1/auth/login",
        json={"username": "select@example.com", "password": "correct-password"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def _parse_sse(body: str) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        event = "message"
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if data_lines:
            events.append((event, data_lines[0]))
    return events


def _seed_pitch_with_matches(user_id, *, study_count: int = 2):  # noqa: ANN001
    with pitch_service.get_sync_session() as session:
        pitch = Pitch(created_by=user_id, title="Travel prospect")
        session.add(pitch)
        session.flush()
        session.add(
            PitchInput(
                pitch_id=pitch.id,
                kind="message",
                body="A travel company needs a faster mobile app.",
                created_by=user_id,
            )
        )
        deck = DeckImport(
            uploaded_by=user_id,
            file_name="deck.pptx",
            sha256="a" * 64,
            status="completed",
        )
        session.add(deck)
        session.flush()
        study_ids = []
        for index in range(study_count):
            study = Study(
                section="Mobile",
                title=f"Study {index + 1}-{uuid4().hex[:8]}",
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
                tech_stack="React Native",
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
                    selected=False,
                )
            )
            study_ids.append(study.id)
        session.commit()
        return pitch.id, study_ids


async def test_empty_selection_is_rejected(client) -> None:  # noqa: ANN001
    token = await _token(client)
    user = auth_service.ensure_user(
        username="select@example.com",
        password="correct-password",
        name="Select Person",
    )
    pitch_id, _study_ids = _seed_pitch_with_matches(user.id)
    response = await client.put(
        f"/v1/pitches/{pitch_id}/selection",
        headers={"Authorization": f"Bearer {token}"},
        json={"study_ids": []},
    )
    assert response.status_code == 422


async def test_unknown_study_id_is_rejected(client) -> None:  # noqa: ANN001
    token = await _token(client)
    user = auth_service.ensure_user(
        username="select@example.com",
        password="correct-password",
        name="Select Person",
    )
    pitch_id, _study_ids = _seed_pitch_with_matches(user.id)
    response = await client.put(
        f"/v1/pitches/{pitch_id}/selection",
        headers={"Authorization": f"Bearer {token}"},
        json={"study_ids": [str(uuid4())]},
    )
    assert response.status_code == 404


async def test_selection_streams_view_link_and_writes_page(
    client,  # noqa: ANN001
    monkeypatch,  # noqa: ANN001
) -> None:
    token = await _token(client)
    user = auth_service.ensure_user(
        username="select@example.com",
        password="correct-password",
        name="Select Person",
    )
    pitch_id, study_ids = _seed_pitch_with_matches(user.id)
    monkeypatch.setattr(
        generate_mod,
        "_draft_pitch",
        lambda **kwargs: {
            "title": "Faster Travel Search",
            "body": "We can help based on the selected studies.",
        },
    )
    response = await client.put(
        f"/v1/pitches/{pitch_id}/selection",
        headers={"Authorization": f"Bearer {token}"},
        json={"study_ids": [str(study_ids[0])]},
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)
    names = [name for name, _payload in events]
    assert "status" in names
    assert "assistant.delta" in names
    assert "assistant.message" in names
    assert "done" in names
    joined = " ".join(payload for _name, payload in events)
    assert "/pitch/view?id=" in joined
    assert str(pitch_id) in joined

    page = await client.get(
        f"/v1/pitches/{pitch_id}/page",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert page.status_code == 200
    body = page.json()
    assert body["title"] == "Faster Travel Search"
    assert "help" in body["body"].lower()
    assert len(body["studies"]) == 1
    assert body["studies"][0]["study_id"] == str(study_ids[0])
    assert body["studies"][0]["background"] == "Background text."
    assert body["studies"][0]["challenge"] == "Challenge text."
    assert body["studies"][0]["approach"] == "Approach text."
    assert body["studies"][0]["solution"] == "Solution text."
    assert body["studies"][0]["outcome"] == "Outcome text."
    assert body["studies"][0]["tech_stack"] == "React Native"
    # Structured fields present → raw_text fallback stays empty.
    assert body["studies"][0]["raw_text"] == ""

    pitch = await client.get(
        f"/v1/pitches/{pitch_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert pitch.json()["has_page"] is True


async def test_selection_marks_only_chosen_studies(client, monkeypatch) -> None:  # noqa: ANN001
    token = await _token(client)
    user = auth_service.ensure_user(
        username="select@example.com",
        password="correct-password",
        name="Select Person",
    )
    pitch_id, study_ids = _seed_pitch_with_matches(user.id, study_count=3)
    monkeypatch.setattr(
        generate_mod,
        "_draft_pitch",
        lambda **kwargs: {"title": "Pitch", "body": "Body"},
    )
    response = await client.put(
        f"/v1/pitches/{pitch_id}/selection",
        headers={"Authorization": f"Bearer {token}"},
        json={"study_ids": [str(study_ids[0]), str(study_ids[2])]},
    )
    assert response.status_code == 200
    with pitch_service.get_sync_session() as session:
        rows = list(session.exec(select(PitchMatch).where(PitchMatch.pitch_id == pitch_id)).all())
        by_id = {row.study_id: row.selected for row in rows}
        assert by_id[study_ids[0]] is True
        assert by_id[study_ids[1]] is False
        assert by_id[study_ids[2]] is True


async def test_second_selection_is_conflict(client, monkeypatch) -> None:  # noqa: ANN001
    token = await _token(client)
    user = auth_service.ensure_user(
        username="select@example.com",
        password="correct-password",
        name="Select Person",
    )
    pitch_id, study_ids = _seed_pitch_with_matches(user.id)
    monkeypatch.setattr(
        generate_mod,
        "_draft_pitch",
        lambda **kwargs: {"title": "Pitch", "body": "Body"},
    )
    first = await client.put(
        f"/v1/pitches/{pitch_id}/selection",
        headers={"Authorization": f"Bearer {token}"},
        json={"study_ids": [str(study_ids[0])]},
    )
    assert first.status_code == 200
    second = await client.put(
        f"/v1/pitches/{pitch_id}/selection",
        headers={"Authorization": f"Bearer {token}"},
        json={"study_ids": [str(study_ids[0])]},
    )
    assert second.status_code == 409


async def test_page_requires_auth(client, monkeypatch) -> None:  # noqa: ANN001
    user = auth_service.ensure_user(
        username="select@example.com",
        password="correct-password",
        name="Select Person",
    )
    pitch_id, study_ids = _seed_pitch_with_matches(user.id)
    monkeypatch.setattr(
        generate_mod,
        "_draft_pitch",
        lambda **kwargs: {"title": "Pitch", "body": "Body"},
    )
    pitch_service.apply_selection(
        pitch_id=pitch_id,
        user_id=user.id,
        study_ids=[study_ids[0]],
    )
    generate_mod.write_generated_pitch(pitch_id=pitch_id, user_id=user.id)

    unauth = await client.get(f"/v1/pitches/{pitch_id}/page")
    assert unauth.status_code == 401

    auth_service.ensure_user(
        username="other@example.com",
        password="correct-password",
        name="Other Person",
    )
    login = await client.post(
        "/v1/auth/login",
        json={"username": "other@example.com", "password": "correct-password"},
    )
    token = login.json()["access_token"]
    denied = await client.get(
        f"/v1/pitches/{pitch_id}/page",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert denied.status_code == 404


async def test_page_falls_back_to_raw_text_when_sections_empty(
    client,  # noqa: ANN001
    monkeypatch,  # noqa: ANN001
) -> None:
    token = await _token(client)
    user = auth_service.ensure_user(
        username="select@example.com",
        password="correct-password",
        name="Select Person",
    )
    pitch_id, study_ids = _seed_pitch_with_matches(user.id)
    from sqlmodel import select

    from src.decks.models import StudyVersion

    with pitch_service.get_sync_session() as session:
        version = session.exec(
            select(StudyVersion).where(StudyVersion.study_id == study_ids[0])
        ).one()
        version.background = ""
        version.challenge = ""
        version.approach = ""
        version.solution = ""
        version.outcome = ""
        version.tech_stack = ""
        version.raw_text = "Only the source narrative remains."
        session.add(version)
        session.commit()

    monkeypatch.setattr(
        generate_mod,
        "_draft_pitch",
        lambda **kwargs: {"title": "Pitch", "body": "Body"},
    )
    response = await client.put(
        f"/v1/pitches/{pitch_id}/selection",
        headers={"Authorization": f"Bearer {token}"},
        json={"study_ids": [str(study_ids[0])]},
    )
    assert response.status_code == 200
    page = await client.get(
        f"/v1/pitches/{pitch_id}/page",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert page.status_code == 200
    study = page.json()["studies"][0]
    assert study["background"] == ""
    assert study["raw_text"] == "Only the source narrative remains."


def test_generate_path_does_not_call_agent_again(monkeypatch) -> None:  # noqa: ANN001
    calls = {"agent": 0}

    class ToolCallingModel:
        def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
            del tools, kwargs
            return self

        def invoke(self, messages, config=None):  # noqa: ANN001
            del messages, config
            calls["agent"] += 1
            if calls["agent"] == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "recommend_pitch_studies",
                            "args": {"brief": "travel mobile app"},
                            "id": "c1",
                            "type": "tool_call",
                        }
                    ],
                )
            return AIMessage(content="should not run")

    monkeypatch.setattr(pitch_graph, "chat_model", lambda: ToolCallingModel())
    monkeypatch.setattr(pitch_graph, "decide_gate", lambda **kwargs: ("allow_tools", ""))
    monkeypatch.setattr(
        pitch_graph,
        "write_generated_pitch",
        lambda **kwargs: {
            "title": "T",
            "url": "http://localhost:3000/pitch/view?id=x",
            "message": "Ready: http://localhost:3000/pitch/view?id=x",
        },
    )
    monkeypatch.setattr(
        pitch_tools,
        "_persist_matches",
        lambda pitch_id, query, shortlist: [{"rank": 1}],
    )
    monkeypatch.setattr(pitch_tools, "_retrieve", lambda query: [{"study_id": "study-1"}])

    user = auth_service.ensure_user(
        username="gen@example.com",
        password="correct-password",
        name="Gen Person",
    )
    pitch_id, study_ids = _seed_pitch_with_matches(user.id)
    graph = pitch_graph.compile_graph(MemorySaver())
    config = {"configurable": {"thread_id": str(pitch_id)}}
    try:
        graph.invoke(
            {
                "pitch_id": str(pitch_id),
                "messages": [HumanMessage(content="travel mobile app")],
                "awaiting_selection": False,
            },
            config,
        )
    except Exception as exc:
        if exc.__class__.__name__ != "GraphInterrupt":
            raise
    snapshot = graph.get_state(config)
    interrupted = bool(getattr(snapshot, "interrupts", None)) or any(
        getattr(task, "interrupts", None) for task in (getattr(snapshot, "tasks", ()) or ())
    )
    assert interrupted
    before = calls["agent"]
    pitch_service.apply_selection(
        pitch_id=pitch_id,
        user_id=user.id,
        study_ids=[study_ids[0]],
    )
    graph.invoke(Command(resume=[str(study_ids[0])]), config)
    assert calls["agent"] == before
