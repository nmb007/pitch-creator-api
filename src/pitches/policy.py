"""Cheap policy gate: allow_tools | refuse | clarify."""

from __future__ import annotations

from typing import Any, Literal

from src.llm import complete_json, model_name_for

GateDecision = Literal["allow_tools", "refuse", "clarify"]

MIN_TURN_CHARS = 8

_GATE_SCHEMA: dict[str, Any] = {
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

_SYSTEM = """You gate messages for Arbisoft's case-study and pitch assistant.
Decide exactly one of:
- allow_tools: look up case studies or a pitch shortlist for a prospect/brief.
- refuse: off-topic (general knowledge, sports, politics, homework, etc.).
  Reply briefly but politely and steer them back to case studies or pitch help.
  reply must be non-empty.
- clarify: greeting, too vague, or not enough detail to search.
  Ask what they need. reply must be non-empty.

Never answer out-of-domain questions. For allow_tools, reply may be empty."""


def decide_gate(turn_text: str, *, pitch_id: str) -> tuple[GateDecision, str]:
    text = turn_text.strip()
    if len(text) < MIN_TURN_CHARS:
        return (
            "clarify",
            "Tell me a bit more — for example a topic to search in our case studies, "
            "or a prospect brief so I can suggest relevant studies.",
        )
    if not _groq_ready():
        return _heuristic_gate(text)
    try:
        raw = complete_json(
            system=_SYSTEM,
            user=text[:4000],
            schema=_GATE_SCHEMA,
            role="pitch",
            tags=[pitch_id, model_name_for("pitch")],
            metadata={"pitch_id": pitch_id, "model": model_name_for("pitch")},
            run_name="pitch_policy_gate",
        )
    except Exception:
        return _heuristic_gate(text)
    decision = str(raw.get("decision") or "clarify")
    if decision not in {"allow_tools", "refuse", "clarify"}:
        decision = "clarify"
    reply = str(raw.get("reply") or "").strip()
    if decision != "allow_tools" and not reply:
        reply = (
            "I can help with Arbisoft case studies and pitch shortlists. What should we look for?"
        )
    return decision, reply  # type: ignore[return-value]


def _heuristic_gate(text: str) -> tuple[GateDecision, str]:
    lowered = text.lower()
    ood_markers = (
        "world cup",
        "moon",
        "weather",
        "stock market",
        "who won",
        "capital of",
    )
    if any(marker in lowered for marker in ood_markers):
        return (
            "refuse",
            "I only help with Arbisoft case studies and pitch shortlists for prospects. "
            "Ask about a technology theme or share a prospect brief.",
        )
    browse_markers = ("case stud", "studies where", "worked on", "show me studies")
    pitch_markers = ("prospect", "pitch", "rfp", "propose", "recommend studies")
    if any(marker in lowered for marker in (*browse_markers, *pitch_markers)):
        return "allow_tools", ""
    if len(text.split()) <= 3:
        return (
            "clarify",
            "Could you share more detail — a case-study topic, or a prospect description?",
        )
    return "allow_tools", ""


def _groq_ready() -> bool:
    from src.config import settings

    return bool(settings.GROQ_API_KEY.strip())
