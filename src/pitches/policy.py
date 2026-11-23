"""Policy gate: allow_tools | refuse | clarify. Always uses the LLM."""

from __future__ import annotations

from typing import Any, Literal

from src.llm import complete_json, model_name_for

GateDecision = Literal["allow_tools", "refuse", "clarify"]

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
- clarify: greeting, too vague, single letters/words, or not enough detail to search.
  Ask what they need. reply must be non-empty.

Never answer out-of-domain questions. For allow_tools, reply may be empty."""


def decide_gate(turn_text: str, *, pitch_id: str) -> tuple[GateDecision, str]:
    text = turn_text.strip() or "(empty)"
    raw = complete_json(
        system=_SYSTEM,
        user=text[:4000],
        schema=_GATE_SCHEMA,
        role="pitch",
        tags=[pitch_id, model_name_for("pitch")],
        metadata={"pitch_id": pitch_id, "model": model_name_for("pitch")},
        run_name="pitch_policy_gate",
    )
    decision = str(raw.get("decision") or "clarify")
    if decision not in {"allow_tools", "refuse", "clarify"}:
        decision = "clarify"
    reply = str(raw.get("reply") or "").strip()
    if decision != "allow_tools" and not reply:
        reply = (
            "I can help with Arbisoft case studies and pitch shortlists. What should we look for?"
        )
    return decision, reply  # type: ignore[return-value]
