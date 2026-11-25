"""Conversational pitch graph: policy gate, tools agent, then optional generate."""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any, Literal, TypedDict
from uuid import UUID

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import interrupt

from src.database import get_sync_session
from src.llm import chat_model
from src.models import utcnow
from src.pitches.generate import write_generated_pitch
from src.pitches.models import Pitch, PitchMessage
from src.pitches.policy import decide_gate
from src.pitches.tools import awaiting_selection, current_pitch_id, pitch_tools

logger = logging.getLogger(__name__)

AGENT_SYSTEM = """You are Arbisoft's case-study and pitch assistant.
You help users find relevant case studies and recommend studies for a prospect pitch.
Use browse_studies for topic/capability searches.
Use recommend_pitch_studies when the user describes a prospect, RFP, or asks for a pitch shortlist.
Follow each tool's argument guidance when calling tools.
Use the conversation history: short follow-ups like "give me the last 5" refer to prior results.
Do not invent studies. Rely on tool results. Keep replies professional.
If tools return no studies, say so and ask for a clearer topic or brief.
Base every claim on the chunks in the tool result.
Name a technology or outcome only when it appears in one of those chunks.
When browse_studies returns studies, cover every study in that result. Do not omit any for brevity.
When recommend_pitch_studies returns studies, stop after the tool call.
The app shows those studies. Do not write a shortlist.
Put clear matches first. If a study's chunks do not clearly support the user's ask,
still include it under a short "Weaker or uncertain matches" section and say why the match is weak.
Your reply must account for all studies (the full study_count when present).
Write replies in Markdown only. Do not use HTML tags such as <br>.
Inside tables, separate points with semicolons or a middle dot ( · ), not line-break tags."""

_FALLBACK_GATE_REPLY = (
    "I can help with Arbisoft case studies and pitch shortlists. What should we look for?"
)


class PitchState(TypedDict, total=False):
    pitch_id: str
    gate: Literal["allow_tools", "refuse", "clarify"]
    messages: Annotated[list[BaseMessage], add_messages]
    awaiting_selection: bool
    selected_study_ids: list[str]
    generated_message: str


def compile_graph(checkpointer):  # noqa: ANN001, ANN202
    tools = pitch_tools()
    tool_node = ToolNode(tools)
    builder = StateGraph(PitchState)
    builder.add_node("policy_gate", policy_gate)
    builder.add_node("agent", make_agent_node(tools))
    builder.add_node("tools", make_tools_node(tool_node))
    builder.add_node("maybe_wait", maybe_wait)
    builder.add_node("generate", generate)
    builder.add_edge(START, "policy_gate")
    builder.add_conditional_edges(
        "policy_gate",
        route_after_gate,
        {"agent": "agent", END: END},
    )
    builder.add_conditional_edges(
        "agent",
        tools_condition,
        {"tools": "tools", END: "maybe_wait"},
    )
    builder.add_conditional_edges(
        "tools",
        route_after_tools,
        {"agent": "agent", "maybe_wait": "maybe_wait"},
    )
    builder.add_conditional_edges(
        "maybe_wait",
        route_after_wait,
        {"generate": "generate", END: END},
    )
    builder.add_edge("generate", END)
    return builder.compile(checkpointer=checkpointer)


def make_agent_node(tools: list[Any]):  # noqa: ANN201
    def agent(state: PitchState) -> dict[str, Any]:
        model = chat_model().bind_tools(tools)
        # System prompt is invoke-only so it is not duplicated in checkpointed messages.
        history = [SystemMessage(content=AGENT_SYSTEM), *(state.get("messages") or [])]
        reply = model.invoke(history)
        _persist_state_message(UUID(state["pitch_id"]), reply)
        return {"messages": [reply]}

    return agent


def make_tools_node(tool_node: ToolNode):  # noqa: ANN201
    def run_tools(state: PitchState) -> dict[str, Any]:
        pitch_token = current_pitch_id.set(state["pitch_id"])
        box = {"ready": False}
        flag_token = awaiting_selection.set(box)
        try:
            result = tool_node.invoke(state)
        finally:
            current_pitch_id.reset(pitch_token)
            awaiting_selection.reset(flag_token)
        pitch_id = UUID(state["pitch_id"])
        for message in result.get("messages") or []:
            _persist_state_message(pitch_id, message)
        return {
            "messages": result.get("messages", []),
            "awaiting_selection": bool(box["ready"] or state.get("awaiting_selection")),
        }

    return run_tools


def policy_gate(state: PitchState) -> dict[str, Any]:
    decision, reply = decide_gate(
        pitch_id=state["pitch_id"],
        messages=state.get("messages") or [],
    )
    if decision == "allow_tools":
        return {"gate": decision}
    body = reply.strip() or _FALLBACK_GATE_REPLY
    message = AIMessage(content=body)
    _persist_state_message(UUID(state["pitch_id"]), message)
    return {"gate": decision, "messages": [message]}


def route_after_gate(state: PitchState) -> str:
    if state.get("gate") == "allow_tools":
        return "agent"
    return END


def route_after_tools(state: PitchState) -> str:
    """Skip the post-tool reply when a pitch shortlist was saved."""
    if state.get("awaiting_selection"):
        return "maybe_wait"
    return "agent"


def maybe_wait(state: PitchState) -> dict[str, Any]:
    if not state.get("awaiting_selection"):
        return {}
    # Resume value is the list of selected study id strings from PUT /selection.
    selected = interrupt({"pitch_id": state["pitch_id"]})
    ids = [str(item) for item in selected] if isinstance(selected, list) else []
    return {
        "selected_study_ids": ids,
        "awaiting_selection": False,
    }


def route_after_wait(state: PitchState) -> str:
    if state.get("selected_study_ids"):
        return "generate"
    return END


def generate(state: PitchState) -> dict[str, Any]:
    pitch_id = UUID(state["pitch_id"])
    with get_sync_session() as session:
        pitch = session.get(Pitch, pitch_id)
        if pitch is None:
            raise RuntimeError(f"Pitch {pitch_id} was missing during generate")
        user_id = pitch.created_by
    result = write_generated_pitch(pitch_id=pitch_id, user_id=user_id)
    return {"generated_message": result["message"]}


def _persist_state_message(pitch_id: UUID, message: BaseMessage) -> None:
    role, body = _role_and_body(message)
    if not body:
        return
    with get_sync_session() as session:
        session.add(PitchMessage(pitch_id=pitch_id, role=role, body=body))
        pitch = session.get(Pitch, pitch_id)
        if pitch is not None:
            pitch.updated_at = utcnow()
            session.add(pitch)
        session.commit()


def _role_and_body(message: BaseMessage) -> tuple[str, str]:
    if isinstance(message, HumanMessage):
        return "user", _as_text(message.content)
    if isinstance(message, ToolMessage):
        name = getattr(message, "name", None) or "tool"
        text = _as_text(message.content)
        return "tool", f"{name}: {text}" if text else name
    if isinstance(message, AIMessage):
        parts: list[str] = []
        text = _as_text(message.content)
        if text:
            parts.append(text)
        if message.tool_calls:
            parts.append(json.dumps({"tool_calls": message.tool_calls}, default=str))
        return "assistant", "\n".join(parts).strip()
    return "assistant", _as_text(getattr(message, "content", ""))


def _as_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                chunks.append(str(item.get("text") or ""))
        return "".join(chunks).strip()
    if content is None:
        return ""
    return str(content).strip()
