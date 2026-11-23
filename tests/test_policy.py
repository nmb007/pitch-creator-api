"""Policy gate always calls the LLM; title helper stays local."""

from src.pitches import policy as policy_mod
from src.pitches.service import thread_title


def test_decide_gate_uses_llm(monkeypatch) -> None:  # noqa: ANN001
    calls: list[str] = []

    def fake_complete_json(**kwargs: object) -> dict[str, str]:
        calls.append(str(kwargs.get("user") or ""))
        return {"decision": "clarify", "reply": "Share a case-study topic or prospect brief."}

    monkeypatch.setattr(policy_mod, "complete_json", fake_complete_json)
    decision, reply = policy_mod.decide_gate("s", pitch_id="test")
    assert calls == ["s"]
    assert decision == "clarify"
    assert "case-study" in reply.lower() or "prospect" in reply.lower()


def test_decide_gate_refuse_from_llm(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        policy_mod,
        "complete_json",
        lambda **_kwargs: {
            "decision": "refuse",
            "reply": "I only help with case studies and pitches.",
        },
    )
    decision, reply = policy_mod.decide_gate("Why is the moon bright?", pitch_id="test")
    assert decision == "refuse"
    assert "case stud" in reply.lower() or "pitch" in reply.lower()


def test_decide_gate_allow_tools_from_llm(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        policy_mod,
        "complete_json",
        lambda **_kwargs: {"decision": "allow_tools", "reply": ""},
    )
    decision, reply = policy_mod.decide_gate(
        "Can you tell me case studies where we worked on data scraping?",
        pitch_id="test",
    )
    assert decision == "allow_tools"
    assert reply == ""


def test_thread_title_from_message_and_file() -> None:
    assert thread_title(kind="message", body="Short brief", file_name=None) == "Short brief"
    long = "x" * 120
    titled = thread_title(kind="message", body=long, file_name=None)
    assert len(titled) <= 80
    assert titled.endswith("…")
    assert thread_title(kind="file", body="ignored", file_name="brief.pdf") == "brief.pdf"
