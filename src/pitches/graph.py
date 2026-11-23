"""Conversational pitch graph: policy gate, then tools agent."""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal, TypedDict
from uuid import UUID

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import interrupt

from src.database import get_sync_session
from src.llm import chat_model
from src.models import utcnow
from src.pitches.models import Pitch, PitchMessage
from src.pitches.policy import decide_gate
from src.pitches.tools import awaiting_selection, current_pitch_id, pitch_tools

logger = logging.getLogger(__name__)

AGENT_SYSTEM = """You are Arbisoft's case-study and pitch assistant.
You help users find relevant case studies and recommend studies for a prospect pitch.
Use browse_studies for topic/capability searches.
Use recommend_pitch_studies when the user describes a prospect, RFP, or asks for a pitch shortlist.
Do not invent studies. Rely on tool results. Keep replies concise and professional.
If tools return no studies, say so and ask for a clearer topic or brief."""


class PitchState(TypedDict, total=False):
    pitch_id: str
    turn_text: str
    gate: Literal["allow_tools", "refuse", "clarify"]
    gate_reply: str
    messages: Annotated[list[BaseMessage], add_messages]
    awaiting_selection: bool


def compile_graph(checkpointer):  # noqa: ANN001, ANN202
    tools = pitch_tools()
    tool_node = ToolNode(tools)
    builder = StateGraph(PitchState)
    builder.add_node("load_turn", load_turn)
    builder.add_node("policy_gate", policy_gate)
    builder.add_node("gate_reply", gate_reply)
    builder.add_node("agent", make_agent_node(tools))
    builder.add_node("tools", make_tools_node(tool_node))
    builder.add_node("persist_assistant", persist_assistant)
    builder.add_node("maybe_wait", maybe_wait)
    builder.add_edge(START, "load_turn")
    builder.add_edge("load_turn", "policy_gate")
    builder.add_conditional_edges(
        "policy_gate",
        route_after_gate,
        {"gate_reply": "gate_reply", "agent": "agent"},
    )
    builder.add_edge("gate_reply", END)
    builder.add_conditional_edges(
        "agent",
        tools_condition,
        {"tools": "tools", END: "persist_assistant"},
    )
    builder.add_edge("tools", "agent")
    builder.add_edge("persist_assistant", "maybe_wait")
    builder.add_edge("maybe_wait", END)
    return builder.compile(checkpointer=checkpointer)


def make_agent_node(tools: list[Any]):  # noqa: ANN201
    def agent(state: PitchState) -> dict[str, Any]:
        model = chat_model().bind_tools(tools)
        history = list(state.get("messages") or [])
        if not history or not isinstance(history[0], SystemMessage):
            history = [SystemMessage(content=AGENT_SYSTEM), *history]
        reply = model.invoke(history)
        return {"messages": [reply]}

    return agent


def make_tools_node(tool_node: ToolNode):  # noqa: ANN201
    def run_tools(state: PitchState) -> dict[str, Any]:
        token = current_pitch_id.set(state["pitch_id"])
        awaiting_selection.set(False)
        try:
            result = tool_node.invoke(state)
        finally:
            current_pitch_id.reset(token)
        flagged = awaiting_selection.get()
        return {
            "messages": result.get("messages", []),
            "awaiting_selection": bool(flagged or state.get("awaiting_selection")),
        }

    return run_tools


def load_turn(state: PitchState) -> dict[str, Any]:
    turn = (state.get("turn_text") or "").strip()
    if not turn:
        raise ValueError("Pitch turn text is required")
    return {
        "turn_text": turn,
        "messages": [HumanMessage(content=turn)],
        "awaiting_selection": False,
    }


def policy_gate(state: PitchState) -> dict[str, Any]:
    decision, reply = decide_gate(state.get("turn_text") or "", pitch_id=state["pitch_id"])
    return {"gate": decision, "gate_reply": reply}


def route_after_gate(state: PitchState) -> str:
    if state.get("gate") == "allow_tools":
        return "agent"
    return "gate_reply"


def gate_reply(state: PitchState) -> dict[str, Any]:
    body = (state.get("gate_reply") or "").strip() or (
        "I can help with Arbisoft case studies and pitch shortlists. What should we look for?"
    )
    pitch_id = UUID(state["pitch_id"])
    with get_sync_session() as session:
        session.add(PitchMessage(pitch_id=pitch_id, role="assistant", body=body))
        pitch = session.get(Pitch, pitch_id)
        if pitch is not None:
            pitch.updated_at = utcnow()
            session.add(pitch)
        session.commit()
    return {"messages": [AIMessage(content=body)]}


def persist_assistant(state: PitchState) -> dict[str, Any]:
    messages = list(state.get("messages") or [])
    last_ai: AIMessage | None = None
    for message in reversed(messages):
        if isinstance(message, AIMessage) and message.content and not message.tool_calls:
            last_ai = message
            break
    if last_ai is None:
        return {}
    body = last_ai.content if isinstance(last_ai.content, str) else str(last_ai.content)
    body = body.strip()
    if not body:
        return {}
    pitch_id = UUID(state["pitch_id"])
    with get_sync_session() as session:
        session.add(PitchMessage(pitch_id=pitch_id, role="assistant", body=body))
        pitch = session.get(Pitch, pitch_id)
        if pitch is not None:
            pitch.updated_at = utcnow()
            session.add(pitch)
        session.commit()
    return {}


def maybe_wait(state: PitchState) -> dict[str, Any]:
    if state.get("awaiting_selection"):
        interrupt({"pitch_id": state["pitch_id"]})
    return {}
