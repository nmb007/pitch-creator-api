"""Structured pitch fields assemble into readable Markdown."""

from src.pitches.generate import pitch_markdown_from_sections


def test_pitch_markdown_from_sections_includes_headings_and_bullets() -> None:
    body = pitch_markdown_from_sections(
        {
            "hook": "They need a faster mobile booking flow.",
            "what_we_will_do": "Rebuild search and checkout for reliability at peak load.",
            "proof": [
                "Cut search latency in a travel marketplace.",
                "Shipped React Native with secure payments.",
            ],
            "why_arbisoft": "Product engineering teams with travel and mobile depth.",
            "next_step": "Book a working session on current bottlenecks.",
        }
    )
    assert "## Why this matters" in body
    assert "They need a faster mobile booking flow." in body
    assert "## How we can help" in body
    assert "## Relevant experience" in body
    assert "- Cut search latency in a travel marketplace." in body
    assert "- Shipped React Native with secure payments." in body
    assert "## Why Arbisoft" in body
    assert "## Next step" in body
    assert "Book a working session on current bottlenecks." in body


def test_pitch_markdown_omits_empty_next_step() -> None:
    body = pitch_markdown_from_sections(
        {
            "hook": "Need clearer ops visibility.",
            "what_we_will_do": "Stand up dashboards and alerts.",
            "proof": ["Built monitoring for a logistics platform.", "Reduced incident noise."],
            "why_arbisoft": "Strong data and platform delivery.",
            "next_step": "",
        }
    )
    assert "## Next step" not in body
    assert body.startswith("## Why this matters")
