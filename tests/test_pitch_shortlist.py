"""A saved pitch shortlist skips the follow-up assistant reply."""

from uuid import UUID

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphInterrupt
from src.auth import service as auth_service
from src.pitches import graph as pitch_graph
from src.pitches import service as pitch_service
from src.pitches import tools as pitch_tools
from src.pitches.models import Pitch, PitchMessage


class ScriptedModel:
    def __init__(self, tool_name: str, tool_args: dict) -> None:
        self.tool_name = tool_name
        self.tool_args = tool_args
        self.calls = 0

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
        del tools, kwargs
        return self

    def invoke(self, messages, config=None):  # noqa: ANN001
        del messages, config
        self.calls += 1
        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": self.tool_name,
                        "args": self.tool_args,
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(content="Pitch-Ready Case-Study Shortlist")


def _pitch_id() -> str:
    user = auth_service.ensure_user(
        username="shortlist@example.com",
        password="correct-password",
        name="Shortlist Person",
    )
    with pitch_graph.get_sync_session() as session:
        pitch = Pitch(created_by=user.id, title="Travel pitch")
        session.add(pitch)
        session.commit()
        session.refresh(pitch)
        return str(pitch.id)


def _compile(monkeypatch, model: ScriptedModel):  # noqa: ANN001
    monkeypatch.setattr(pitch_graph, "chat_model", lambda: model)
    monkeypatch.setattr(pitch_graph, "decide_gate", lambda **kwargs: ("allow_tools", ""))
    monkeypatch.setattr(pitch_tools, "_retrieve", lambda query: [{"study_id": "study-1"}])
    return pitch_graph.compile_graph(MemorySaver())


def _invoke(graph, pitch_id: str) -> tuple[bool, object]:  # noqa: ANN001
    config = {"configurable": {"thread_id": pitch_id}}
    try:
        graph.invoke(
            {
                "pitch_id": pitch_id,
                "messages": [
                    HumanMessage(content="Pitch a faster mobile app for a travel company.")
                ],
                "awaiting_selection": False,
            },
            config,
        )
    except GraphInterrupt:
        return True, None
    snapshot = graph.get_state(config)
    tasks = getattr(snapshot, "tasks", ()) or ()
    interrupted = bool(getattr(snapshot, "interrupts", None)) or any(
        getattr(task, "interrupts", None) for task in tasks
    )
    return interrupted, snapshot


def test_saved_shortlist_skips_the_assistant_reply(monkeypatch) -> None:  # noqa: ANN001
    model = ScriptedModel(
        "recommend_pitch_studies",
        {
            "search_query": "travel mobile app",
            "brief": "A travel company needs a faster mobile app.",
        },
    )
    monkeypatch.setattr(
        pitch_tools,
        "_persist_matches",
        lambda pitch_id, query, shortlist: [{"rank": 1}],
    )
    graph = _compile(monkeypatch, model)
    interrupted, snapshot = _invoke(graph, _pitch_id())
    assert interrupted, (model.calls, snapshot)
    assert model.calls == 1


def test_empty_shortlist_still_gets_a_reply(monkeypatch) -> None:  # noqa: ANN001
    model = ScriptedModel(
        "recommend_pitch_studies",
        {
            "search_query": "travel mobile app",
            "brief": "A travel company needs a faster mobile app.",
        },
    )
    monkeypatch.setattr(pitch_tools, "_persist_matches", lambda pitch_id, query, shortlist: [])
    graph = _compile(monkeypatch, model)
    interrupted, _snapshot = _invoke(graph, _pitch_id())
    assert not interrupted
    assert model.calls == 2


def test_browse_still_gets_a_reply(monkeypatch) -> None:  # noqa: ANN001
    model = ScriptedModel("browse_studies", {"search_query": "React Native mobile"})
    graph = _compile(monkeypatch, model)
    monkeypatch.setattr(
        pitch_tools,
        "_retrieve",
        lambda query: [
            {
                "study_id": "study-1",
                "category": "Mobile",
                "title": "Landit",
                "chunks": [{"part": "tech_stack", "text": "We used React Native."}],
            }
        ],
    )
    interrupted, _snapshot = _invoke(graph, _pitch_id())
    assert not interrupted
    assert model.calls == 2


async def test_interrupted_turn_omits_assistant_message(monkeypatch) -> None:  # noqa: ANN001
    pitch_id = UUID(_pitch_id())
    with pitch_service.get_sync_session() as session:
        session.add(PitchMessage(pitch_id=pitch_id, role="user", body="Pitch a travel app."))
        session.add(
            PitchMessage(
                pitch_id=pitch_id,
                role="assistant",
                body='{"tool_calls": [{"name": "recommend_pitch_studies"}]}',
            )
        )
        session.commit()

    class RaisingGraph:
        async def astream_events(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
            raise GraphInterrupt(())
            yield {}

    monkeypatch.setattr(pitch_service.runtime, "graph", RaisingGraph())
    names = [
        chunk.split("\n", maxsplit=1)[0].removeprefix("event: ")
        async for chunk in pitch_service.stream_turn(
            pitch_id=pitch_id,
            turn_text="Pitch a travel app.",
        )
    ]
    assert "assistant.message" not in names
    assert "interrupted" in names
    assert "done" in names
