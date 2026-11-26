"""Policy gate: allow_tools | refuse | clarify. Always uses the LLM."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from src.llm import complete_json, model_name_for

GateDecision = Literal["allow_tools", "refuse", "clarify"]

HISTORY_LIMIT = 10

_GATE_SCHEMA: dict[str, Any] = {
    "title": "PitchPolicyGate",
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["allow_tools", "refuse", "clarify"],
        },
        "reply": {
            "type": "string",
            "description": "Short assistant reply when decision is refuse or clarify",
        },
    },
    "required": ["decision", "reply"],
    "additionalProperties": False,
}

_SYSTEM_SEARCH = """You gate messages for Arbisoft's case-study and pitch assistant.
You receive recent chat history (including tool results when present) plus the
latest user message. Use that history.
Decide exactly one of:
- allow_tools: look up case studies or a pitch shortlist for a prospect/brief.
  Also allow_tools for short follow-ups that clearly continue prior case-study or
  pitch work (for example "ok, give me the last 5", "those ones", "more like #2").
- refuse: off-topic (general knowledge, sports, politics, homework, etc.).
  Reply briefly but politely and steer them back to case studies or pitch help.
  reply must be non-empty.
- clarify: greeting with no prior in-domain work, too vague, or not enough detail
  even with history. Ask what they need. reply must be non-empty.

Never answer out-of-domain questions. For allow_tools, reply may be empty."""

_SYSTEM_REVISE = """You gate messages for Arbisoft's pitch refine assistant.
A pitch page already exists for this thread. You receive recent chat history plus
the latest user message. Use that history.
Decide exactly one of:
- allow_tools: refine the existing pitch page — change the title, edit the body or
  a section, add or remove a shortlisted case study, regenerate from the current
  selection, or short follow-ups that continue refine work (for example
  "yes, update the description", "make it shorter", "remove that one").
- refuse: off-topic (general knowledge, sports, politics, homework, etc.).
  Reply briefly but politely and steer them back to refining this pitch.
  reply must be non-empty.
- clarify: greeting with no clear refine intent, or too vague to act on even with
  history. Ask what they want to change. reply must be non-empty.

Never answer out-of-domain questions. For allow_tools, reply may be empty."""


def decide_gate(
    *,
    pitch_id: str,
    messages: Sequence[BaseMessage],
    has_page: bool = False,
) -> tuple[GateDecision, str]:
    """Classify the latest turn using LangGraph ``messages`` (last N kept)."""
    window = list(messages)[-HISTORY_LIMIT:]
    system = _SYSTEM_REVISE if has_page else _SYSTEM_SEARCH
    raw = complete_json(
        system=system,
        user=_gate_user_payload(window),
        schema=_GATE_SCHEMA,
        role="pitch",
        tags=[pitch_id, model_name_for("pitch")],
        metadata={
            "pitch_id": pitch_id,
            "model": model_name_for("pitch"),
            "has_page": has_page,
        },
        run_name="pitch_policy_gate",
    )
    decision = str(raw.get("decision") or "clarify")
    if decision not in {"allow_tools", "refuse", "clarify"}:
        decision = "clarify"
    reply = str(raw.get("reply") or "").strip()
    if decision != "allow_tools" and not reply:
        if has_page:
            reply = (
                "I can help refine this pitch — title, description, or case studies. "
                "What should we change?"
            )
        else:
            reply = (
                "I can help with Arbisoft case studies and pitch shortlists. "
                "What should we look for?"
            )
    return decision, reply  # type: ignore[return-value]


def _gate_user_payload(messages: Sequence[BaseMessage]) -> str:
    if not messages:
        return "Latest user message:\n(empty)"
    lines = ["Recent conversation (oldest to newest):"]
    for message in messages[:-1]:
        label, body = _label_and_body(message)
        if body:
            lines.append(f"{label}: {body}")
    latest_label, latest_body = _label_and_body(messages[-1])
    lines.append("")
    lines.append(f"Latest {latest_label.lower()} message:\n{latest_body or '(empty)'}")
    if len(messages) == 1:
        return f"Latest {latest_label.lower()} message:\n{latest_body or '(empty)'}"
    return "\n".join(lines)


def _label_and_body(message: BaseMessage) -> tuple[str, str]:
    if isinstance(message, HumanMessage):
        return "User", _text_content(message.content)
    if isinstance(message, ToolMessage):
        name = getattr(message, "name", None) or "tool"
        return f"Tool({name})", _text_content(message.content)
    if isinstance(message, AIMessage):
        parts: list[str] = []
        text = _text_content(message.content)
        if text:
            parts.append(text)
        if message.tool_calls:
            names = ", ".join(str(call.get("name") or "tool") for call in message.tool_calls)
            parts.append(f"[tool_calls: {names}]")
        return "Assistant", " ".join(parts).strip()
    return "Message", _text_content(getattr(message, "content", ""))


def _text_content(content: object) -> str:
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
