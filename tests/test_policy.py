"""Policy gate always calls the LLM; title helper stays local."""

from langchain_core.messages import AIMessage, HumanMessage
from src.pitches import policy as policy_mod
from src.pitches.service import thread_title


def test_decide_gate_uses_llm(monkeypatch) -> None:  # noqa: ANN001
    calls: list[str] = []

    def fake_complete_json(**kwargs: object) -> dict[str, str]:
        calls.append(str(kwargs.get("user") or ""))
        return {"decision": "clarify", "reply": "Share a case-study topic or prospect brief."}

    monkeypatch.setattr(policy_mod, "complete_json", fake_complete_json)
    decision, reply = policy_mod.decide_gate(
        pitch_id="test",
        messages=[HumanMessage(content="s")],
    )
    assert len(calls) == 1
    assert "Latest user message:" in calls[0]
    assert calls[0].endswith("s")
    assert decision == "clarify"
    assert "case-study" in reply.lower() or "prospect" in reply.lower()


def test_decide_gate_includes_history(monkeypatch) -> None:  # noqa: ANN001
    seen: list[str] = []

    def fake_complete_json(**kwargs: object) -> dict[str, str]:
        seen.append(str(kwargs.get("user") or ""))
        return {"decision": "allow_tools", "reply": ""}

    monkeypatch.setattr(policy_mod, "complete_json", fake_complete_json)
    decision, reply = policy_mod.decide_gate(
        pitch_id="test",
        messages=[
            HumanMessage(content="case studies on data scraping"),
            AIMessage(content="Here are several scraping studies…"),
            HumanMessage(content="ok, give me the last 5"),
        ],
    )
    assert decision == "allow_tools"
    assert reply == ""
    assert "Recent conversation" in seen[0]
    assert "data scraping" in seen[0]
    assert seen[0].count("ok, give me the last 5") == 1


def test_decide_gate_refuse_from_llm(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        policy_mod,
        "complete_json",
        lambda **_kwargs: {
            "decision": "refuse",
            "reply": "I only help with case studies and pitches.",
        },
    )
    decision, reply = policy_mod.decide_gate(
        pitch_id="test",
        messages=[HumanMessage(content="Why is the moon bright?")],
    )
    assert decision == "refuse"
    assert "case stud" in reply.lower() or "pitch" in reply.lower()


def test_decide_gate_allow_tools_from_llm(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        policy_mod,
        "complete_json",
        lambda **_kwargs: {"decision": "allow_tools", "reply": ""},
    )
    decision, reply = policy_mod.decide_gate(
        pitch_id="test",
        messages=[
            HumanMessage(content="Can you tell me case studies where we worked on data scraping?")
        ],
    )
    assert decision == "allow_tools"
    assert reply == ""


def test_decide_gate_keeps_last_ten_messages(monkeypatch) -> None:  # noqa: ANN001
    seen: list[str] = []

    def fake_complete_json(**kwargs: object) -> dict[str, str]:
        seen.append(str(kwargs.get("user") or ""))
        return {"decision": "allow_tools", "reply": ""}

    monkeypatch.setattr(policy_mod, "complete_json", fake_complete_json)
    messages = [HumanMessage(content=f"turn-{i:02d}") for i in range(12)]
    policy_mod.decide_gate(pitch_id="test", messages=messages)
    assert "turn-00" not in seen[0]
    assert "turn-01" not in seen[0]
    assert "turn-02" in seen[0]
    assert "turn-11" in seen[0]


def test_thread_title_from_message_and_file() -> None:
    assert thread_title(kind="message", body="Short brief", file_name=None) == "Short brief"
    long = "x" * 120
    titled = thread_title(kind="message", body=long, file_name=None)
    assert len(titled) <= 80
    assert titled.endswith("…")
    assert thread_title(kind="file", body="ignored", file_name="brief.pdf") == "brief.pdf"
