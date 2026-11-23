"""Policy gate short-circuits thin and off-domain turns."""

from src.pitches.policy import decide_gate
from src.pitches.service import thread_title


def test_thin_input_clarifies_without_tools() -> None:
    decision, reply = decide_gate("s", pitch_id="test")
    assert decision == "clarify"
    assert "case stud" in reply.lower() or "prospect" in reply.lower()


def test_hello_clarifies() -> None:
    decision, reply = decide_gate("Hello", pitch_id="test")
    assert decision == "clarify"
    assert reply


def test_ood_refuses() -> None:
    decision, reply = decide_gate("Why is the moon bright tonight?", pitch_id="test")
    assert decision == "refuse"
    assert "case stud" in reply.lower() or "pitch" in reply.lower()


def test_browse_allows_tools() -> None:
    decision, _reply = decide_gate(
        "Can you tell me case studies where we worked on data scraping?",
        pitch_id="test",
    )
    assert decision == "allow_tools"


def test_thread_title_from_message_and_file() -> None:
    assert thread_title(kind="message", body="Short brief", file_name=None) == "Short brief"
    long = "x" * 120
    titled = thread_title(kind="message", body=long, file_name=None)
    assert len(titled) <= 80
    assert titled.endswith("…")
    assert thread_title(kind="file", body="ignored", file_name="brief.pdf") == "brief.pdf"
