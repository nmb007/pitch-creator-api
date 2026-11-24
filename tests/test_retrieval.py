"""Unit tests for hybrid retrieval helpers. No live embed or rerank models."""

from __future__ import annotations

from src.pitches.retrieval import (
    cap_chunks_per_study,
    chunk_part_name,
    escape_like,
    group_studies_by_best_chunk,
    rrf_merge,
)


def test_rrf_merge_prefers_items_in_both_lists() -> None:
    fused = rrf_merge(
        ["landit", "umwella", "wikimedia"],
        ["zain", "landit", "cavago"],
        k=60,
    )
    assert fused[0] == "landit"
    assert "zain" in fused[:3]
    assert fused.index("umwella") < fused.index("wikimedia")


def test_cap_chunks_per_study_keeps_diversity() -> None:
    # Six Landit chunks would fill a pool of 5 without a per-study cap.
    ordered = [f"l{i}" for i in range(1, 7)] + ["z1", "u1"]
    chunk_study = {f"l{i}": "landit" for i in range(1, 7)}
    chunk_study["z1"] = "zain"
    chunk_study["u1"] = "umwella"
    kept = cap_chunks_per_study(
        ordered,
        chunk_study,
        pool_limit=5,
        max_per_study=3,
    )
    assert kept == ["l1", "l2", "l3", "z1", "u1"]
    assert sum(1 for chunk_id in kept if chunk_study[chunk_id] == "landit") == 3


def test_group_studies_by_best_chunk_orders_and_filters() -> None:
    scored = [
        _chunk("landit", "L1", 0.96, part="tech_stack"),
        _chunk("landit", "L2", 0.91, part="approach"),
        _chunk("landit", "L3", 0.84, part="solution"),
        _chunk("landit", "L4", 0.79, part="outcome"),
        _chunk("zain", "Z1", 0.88, part="tech_stack"),
        _chunk("umwella", "U1", 0.77, part="tech_stack"),
        _chunk("jobcase", "J1", 0.69, part="tech_stack"),
        _chunk("wikimedia", "W1", 0.31, part="solution"),
        _chunk("genplus", "G1", 0.22, part="tech_stack"),
    ]
    studies = group_studies_by_best_chunk(
        scored,
        study_limit=5,
        max_chunks_per_study=3,
        min_score=0.5,
    )
    assert [item["study_id"] for item in studies] == [
        "landit",
        "zain",
        "umwella",
        "jobcase",
    ]
    landit = studies[0]
    assert landit["best_score"] == 0.96
    assert [chunk["text"] for chunk in landit["chunks"]] == ["L1", "L2", "L3"]
    assert all("score" not in chunk for chunk in landit["chunks"])
    assert all(item["best_score"] >= 0.5 for item in studies)


def test_group_keeps_second_study_when_first_has_many_chunks() -> None:
    scored = [
        *[_chunk("landit", f"L{i}", 0.9 - i * 0.01) for i in range(10)],
        _chunk("zain", "Z1", 0.85),
    ]
    studies = group_studies_by_best_chunk(
        scored,
        study_limit=5,
        max_chunks_per_study=3,
        min_score=0.0,
    )
    assert [item["study_id"] for item in studies] == ["landit", "zain"]
    assert len(studies[0]["chunks"]) == 3
    assert studies[1]["chunks"][0]["text"] == "Z1"


def test_chunk_part_name_matches_field_or_body() -> None:
    fields = {
        "background": "bg",
        "challenge": "ch",
        "approach": "",
        "solution": "sol",
        "outcome": "",
        "tech_stack": "We used React Native",
    }
    assert chunk_part_name("We used React Native", fields) == "tech_stack"
    assert chunk_part_name("sol", fields) == "solution"
    assert chunk_part_name("raw body only", fields) == "body"


def test_escape_like() -> None:
    assert escape_like("100%_done") == "100\\%\\_done"


def _chunk(
    study_id: str,
    text: str,
    score: float,
    *,
    part: str = "body",
) -> dict:
    return {
        "study_id": study_id,
        "version_id": f"v-{study_id}",
        "title": study_id.title(),
        "category": "Case Studies",
        "part": part,
        "text": text,
        "score": score,
    }
