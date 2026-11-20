"""Outline prompt helpers and validated-only structure cache."""

from __future__ import annotations

from typing import Any

import pytest

from src.decks import structure_model as sm


def test_held_heading_message_forbids_relisting_block_id() -> None:
    message = sm.build_outline_message(
        section="General Format",
        blocks=[(8, 3, "Body line")],
        held_heading={"block_id": 5, "field": "challenge", "text": "The Challenge"},
    )
    assert "do not re-list this block_id" in message
    assert '"block_id": 5' in message
    assert "Never re-list a Held open heading" in sm.OUTLINE_SYSTEM_PROMPT
    assert "master PowerPoint of client case studies" in sm.OUTLINE_SYSTEM_PROMPT
    assert "Never label a case-study headline as paragraph" in sm.OUTLINE_SYSTEM_PROMPT


def test_structure_outline_caches_only_after_commit(
    tmp_path,  # noqa: ANN001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sm, "_cache_dir", lambda: tmp_path)
    payload: dict[str, Any] = {
        "blocks": [
            {
                "block_id": 1,
                "block_type": "title",
                "field": "",
                "children": [],
            }
        ]
    }
    calls = {"n": 0}

    seen: list[dict[str, Any]] = []

    def fake_complete_json(**kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        seen.append(kwargs)
        return payload

    monkeypatch.setattr(sm, "complete_json", fake_complete_json)
    message = "Current section: General\nBlocks:\n1. slide=1 text=Title One"

    assert sm.structure_outline(
        message,
        deck_id="deck-1",
        slide_start=11,
        slide_end=12,
        outline_call=6,
    ) == payload
    assert calls["n"] == 1
    assert list(tmp_path.glob("*.json")) == []
    assert seen[0]["tags"] == ["deck_ingest", "outline"]
    assert seen[0]["metadata"]["pipeline"] == "deck_ingest"
    assert seen[0]["metadata"]["kind"] == "outline"
    assert seen[0]["metadata"]["deck_id"] == "deck-1"
    assert seen[0]["metadata"]["slide_start"] == 11
    assert seen[0]["metadata"]["slide_end"] == 12
    assert seen[0]["run_name"] == "outline slides 11–12"

    sm.commit_outline_cache(message, payload)
    cached = list(tmp_path.glob("*.json"))
    assert len(cached) == 1
    assert cached[0].read_text()

    def boom(**_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("cache hit should not call the LLM")

    monkeypatch.setattr(sm, "complete_json", boom)
    assert sm.structure_outline(message) == payload

    sm.discard_outline_cache(message)
    assert list(tmp_path.glob("*.json")) == []
