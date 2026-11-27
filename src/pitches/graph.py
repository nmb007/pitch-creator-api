"""Conversational pitch graph: policy gate, search or revise tools, optional generate."""

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
from src.pitches.revise_tools import revise_tool_list
from src.pitches.service import has_page
from src.pitches.tools import awaiting_selection, current_pitch_id, pitch_tools

logger = logging.getLogger(__name__)

AGENT_SYSTEM = """You are Arbisoft's case-study and pitch assistant.
You help users find relevant case studies and recommend studies for a prospect pitch.
Use browse_studies for topic/capability searches.
Use recommend_pitch_studies when the user describes a prospect, RFP, or asks for a pitch shortlist.
Follow each tool's argument guidance when calling tools.

Both browse_studies and recommend_pitch_studies require search_query: 2 to 8 words with only
the domain, technology, industry, product, or region the user said. Copy those words. Do not
add synonyms or pitch language. Drop filler such as preparing, pitch, company, seeking,
solutions, case study, case studies, references, for a pitch. Never put the full sentence
or a full RFP into search_query.
Examples:
- "We are preparing a pitch for a company with fintech/digital payments"
  → search_query: "fintech digital payments"
- "RFP for a React Native payments app in MENA"
  → search_query: "React Native payments MENA"
- "Find case studies on fintech or digital payments"
  → search_query: "fintech digital payments"
For recommend_pitch_studies, also pass brief as the user's message unchanged. Retrieval uses
search_query only; brief is for explaining why each study fits.

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

REVISE_SYSTEM = """You refine an existing Arbisoft pitch page for this chat thread.
You only use revise tools. Do not search the full study library.
Prefer surgical tools (update_title, update_body, add_study, remove_study) over regenerate_page
unless the user asks to start over or regenerate from the current selection.
Resolve studies with find_pitch_studies before add_study or remove_study. Never invent UUIDs.
Only add a study if find_pitch_studies shows it in this pitch's matches and selected is false.
If find returns no match, say it was not in this pitch's suggested list.
If find returns two or more studies (study_count >= 2), list the matches and ask which one
to use; do not call add_study or remove_study until the user picks a single study.
If selected is already true on an add request, say it is already on the pitch.
If both remove_study or add_study and update_body are needed, change selection first, then update_body.
Always pass rewrite explicitly as true or false on add_study and remove_study; never omit it.
If the user asks to add or remove a study and does not mention updating the description,
always ask first whether to update the description too; do not call add_study or remove_study
until they answer.
When your previous message asked whether to update the description and the user affirms
(yes, please, yeah, yep, sure, update it, do it, go ahead, etc.), call add_study or
remove_study with rewrite=true.
When they decline (no, skip, just remove/add, leave the description, etc.), use rewrite=false.
Do not set rewrite=true just because the same message also asks for a body or style change —
that is a separate copy ask: use rewrite=false for the selection change, then update_body.
Stay grounded in selected studies and the prospect brief. Do not invent facts.
After a successful change, confirm briefly and include the view_url from the tool result.
Write replies in Markdown only. Do not use HTML tags such as <br>."""

_FALLBACK_GATE_REPLY = (
    "I can help with Arbisoft case studies and pitch shortlists. What should we look for?"
)
_FALLBACK_REVISE_GATE_REPLY = (
    "I can help refine this pitch — title, description, or case studies. What should we change?"
)


class PitchState(TypedDict, total=False):
    pitch_id: str
    gate: Literal["allow_tools", "refuse", "clarify"]
    has_page: bool
    messages: Annotated[list[BaseMessage], add_messages]
    awaiting_selection: bool
    selected_study_ids: list[str]
    generated_message: str


def compile_graph(checkpointer):  # noqa: ANN001, ANN202
    search_tools = pitch_tools()
    revise_tools = revise_tool_list()
    search_tool_node = ToolNode(search_tools)
    revise_tool_node = ToolNode(revise_tools)
    builder = StateGraph(PitchState)
    builder.add_node("policy_gate", policy_gate)
    builder.add_node("agent", make_agent_node(search_tools, AGENT_SYSTEM))
    builder.add_node("tools", make_search_tools_node(search_tool_node))
    builder.add_node("revise_agent", make_agent_node(revise_tools, REVISE_SYSTEM))
    builder.add_node("revise_tools", make_revise_tools_node(revise_tool_node))
    builder.add_node("maybe_wait", maybe_wait)
    builder.add_node("generate", generate)
    builder.add_edge(START, "policy_gate")
    builder.add_conditional_edges(
        "policy_gate",
        route_after_gate,
        {"agent": "agent", "revise_agent": "revise_agent", END: END},
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
        "revise_agent",
        tools_condition,
        {"tools": "revise_tools", END: END},
    )
    builder.add_edge("revise_tools", "revise_agent")
    builder.add_conditional_edges(
        "maybe_wait",
        route_after_wait,
        {"generate": "generate", END: END},
    )
    builder.add_edge("generate", END)
    return builder.compile(checkpointer=checkpointer)


def make_agent_node(tools: list[Any], system_prompt: str):  # noqa: ANN201
    def agent(state: PitchState) -> dict[str, Any]:
        model = chat_model().bind_tools(tools)
        # System prompt is invoke-only so it is not duplicated in checkpointed messages.
        history = [SystemMessage(content=system_prompt), *(state.get("messages") or [])]
        reply = model.invoke(history)
        _persist_state_message(UUID(state["pitch_id"]), reply)
        return {"messages": [reply]}

    return agent


def make_search_tools_node(tool_node: ToolNode):  # noqa: ANN201
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


def make_revise_tools_node(tool_node: ToolNode):  # noqa: ANN201
    def run_tools(state: PitchState) -> dict[str, Any]:
        pitch_token = current_pitch_id.set(state["pitch_id"])
        try:
            result = tool_node.invoke(state)
        finally:
            current_pitch_id.reset(pitch_token)
        pitch_id = UUID(state["pitch_id"])
        for message in result.get("messages") or []:
            _persist_state_message(pitch_id, message)
        return {"messages": result.get("messages", [])}

    return run_tools


def policy_gate(state: PitchState) -> dict[str, Any]:
    pitch_id = state["pitch_id"]
    page_exists = has_page(UUID(pitch_id))
    decision, reply = decide_gate(
        pitch_id=pitch_id,
        messages=state.get("messages") or [],
        has_page=page_exists,
    )
    if decision == "allow_tools":
        return {"gate": decision, "has_page": page_exists}
    body = reply.strip() or (
        _FALLBACK_REVISE_GATE_REPLY if page_exists else _FALLBACK_GATE_REPLY
    )
    message = AIMessage(content=body)
    _persist_state_message(UUID(pitch_id), message)
    return {"gate": decision, "has_page": page_exists, "messages": [message]}


def route_after_gate(state: PitchState) -> str:
    if state.get("gate") != "allow_tools":
        return END
    if state.get("has_page"):
        return "revise_agent"
    return "agent"


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
