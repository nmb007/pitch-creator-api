"""Outline ingest with a fake model produces study drafts."""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import uuid4

from pptx import Presentation
from pptx.util import Inches, Pt
from src.decks.blocks import TextBlock, read_pptx_blocks
from src.decks.parse import parse_pptx
from src.decks.validate import (
    StructureValidationError,
    build_raw_text,
    is_banner,
    outline_from_payload,
)

_HEADINGS = {
    "the challenge": "challenge",
    "challenges": "challenge",
    "the approach": "approach",
    "background": "background",
    "the outcome": "outcome",
    "outcome": "outcome",
    "solution": "solution",
    "solutions": "solution",
    "technology stack": "tech_stack",
    "the technology stack": "tech_stack",
    "tech stack": "tech_stack",
}


def test_period_ending_slide_continues_until_next_title(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    first = presentation.slides.add_slide(blank)
    _write(
        first,
        [
            "Elevating KAYAK's Mobile Experience in Travel Tech",
            "KAYAK, a travel search company, needed a faster mobile app.",
            "Since 2008, Arbisoft has partnered with KAYAK.",
        ],
    )
    second = presentation.slides.add_slide(blank)
    _write(
        second,
        [
            "Arbisoft Proposal for XYZ Ltd",
            "learning, and test automation services, with 40 QA specialists.",
            "Tech Stack: Native Android, Native iOS, Backend",
            "Enabling In-Office 3D Printing for Orthodontics",
            "Braces on Demand needed a platform for remote printing.",
        ],
    )
    presentation.save(path)

    drafts = parse_pptx(
        path,
        complete=_outline_complete(
            titles={
                "Elevating KAYAK's Mobile Experience in Travel Tech",
                "Enabling In-Office 3D Printing for Orthodontics",
            }
        ),
    )
    assert [draft.title for draft in drafts] == [
        "Elevating KAYAK's Mobile Experience in Travel Tech",
        "Enabling In-Office 3D Printing for Orthodontics",
    ]
    kayak = drafts[0]
    assert "faster mobile app" in kayak.raw_text
    assert "learning, and test automation" in kayak.raw_text
    assert "Native Android" in kayak.raw_text
    assert "Arbisoft Proposal" not in kayak.raw_text
    assert "Braces on Demand" not in kayak.raw_text
    assert kayak.slide_numbers == [1, 2]
    assert kayak.client_name == "KAYAK"


def test_lowercase_line_stays_in_open_study(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    first = presentation.slides.add_slide(blank)
    _write(
        first,
        [
            "Elevating KAYAK's Mobile Experience",
            "The Approach",
            "KAYAK needed a faster mobile app.",
        ],
    )
    second = presentation.slides.add_slide(blank)
    _write(
        second,
        [
            "learning, and test automation services, with 40 QA specialists.",
            "Next Study Title About Orthodontics",
            "Braces on Demand needed a platform.",
        ],
    )
    presentation.save(path)

    drafts = parse_pptx(
        path,
        complete=_outline_complete(
            titles={
                "Elevating KAYAK's Mobile Experience",
                "Next Study Title About Orthodontics",
            }
        ),
    )
    assert len(drafts) == 2
    assert "learning, and test automation" in drafts[0].raw_text
    assert "learning, and test automation" in drafts[0].approach
    assert "learning, and test automation" not in drafts[1].raw_text


def test_two_titles_on_one_slide(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    slide = presentation.slides.add_slide(blank)
    _write(
        slide,
        [
            "Landit, a career platform, needed a mobile app.",
            "The Challenge",
            "Moonbeam, a podcast product, needed recommendations.",
        ],
    )
    presentation.save(path)

    drafts = parse_pptx(
        path,
        complete=_outline_complete(
            titles={
                "Landit, a career platform, needed a mobile app.",
                "Moonbeam, a podcast product, needed recommendations.",
            }
        ),
    )
    assert [draft.title for draft in drafts] == [
        "Landit, a career platform, needed a mobile app.",
        "Moonbeam, a podcast product, needed recommendations.",
    ]
    assert "The Challenge" in drafts[0].raw_text
    assert "The Challenge" not in drafts[1].raw_text


def test_wrapped_title(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    slide = presentation.slides.add_slide(blank)
    _write_at(slide, 0.3, ["Arbisoft E-commerce Case Studies"], size=10)
    _write_at(slide, 3.0, ["The Challenge", "Eileen set out to give brands visibility."], size=16)
    _write_at(slide, 1.2, ["Enabling Brands With", "Real-Time Store-Level Insights"], size=24)
    presentation.save(path)

    def complete(message: str) -> dict[str, Any]:
        blocks = _block_rows(message)
        first = _find(blocks, "Enabling Brands With")
        second = _find(blocks, "Real-Time Store-Level Insights")
        return _outline_payload(
            blocks,
            title_ids={first, second},
            section_ids={_find(blocks, "Arbisoft E-commerce Case Studies")},
        )

    drafts = parse_pptx(path, complete=complete)
    assert len(drafts) == 1
    assert drafts[0].title == "Enabling Brands With Real-Time Store-Level Insights"
    assert "Eileen set out" in drafts[0].raw_text
    assert "Arbisoft E-commerce" not in drafts[0].raw_text
    assert drafts[0].section == "Arbisoft E-commerce Case Studies"


def test_outline_rejects_missing_block_id() -> None:
    blocks = {
        1: TextBlock(1, 1, "Title One", "paragraph"),
        2: TextBlock(2, 1, "Body text.", "paragraph"),
    }
    try:
        outline_from_payload(
            {
                "blocks": [
                    {"block_id": 1, "block_type": "title"},
                ]
            },
            blocks,
            batch_numbers={1, 2},
        )
    except StructureValidationError as exc:
        assert "omitted" in str(exc).lower()
    else:
        raise AssertionError("expected StructureValidationError")


def test_outline_demotes_fake_section_and_promotes_cover() -> None:
    blocks = {
        1: TextBlock(1, 1, "Arbisoft Case Studies", "paragraph"),
        2: TextBlock(2, 1, "www.arbisoft.com", "paragraph"),
        3: TextBlock(3, 2, "General Format", "paragraph"),
        4: TextBlock(4, 2, "What the Company Is?", "paragraph"),
        5: TextBlock(5, 2, "What We Did?", "paragraph"),
        6: TextBlock(6, 2, "Covers all industries", "paragraph"),
        7: TextBlock(7, 11, "Format", "paragraph"),
        8: TextBlock(
            8,
            108,
            "Design Case Study: View Our Design Case Studies here",
            "paragraph",
        ),
        9: TextBlock(9, 50, "Arbisoft Case Study for Zain Cash", "paragraph"),
    }
    labeled = outline_from_payload(
        {
            "blocks": [
                {"block_id": 1, "block_type": "header_note", "field": "", "children": []},
                {"block_id": 2, "block_type": "header_note", "field": "", "children": []},
                {"block_id": 3, "block_type": "section", "field": "", "children": []},
                {
                    "block_id": 4,
                    "block_type": "heading",
                    "field": "background",
                    "children": [],
                },
                {
                    "block_id": 5,
                    "block_type": "heading",
                    "field": "approach",
                    "children": [],
                },
                {"block_id": 6, "block_type": "paragraph", "field": "", "children": []},
                {"block_id": 7, "block_type": "section", "field": "", "children": []},
                {"block_id": 8, "block_type": "title", "field": "", "children": []},
                {"block_id": 9, "block_type": "section", "field": "", "children": []},
            ]
        },
        blocks,
        batch_numbers={1, 2, 3, 4, 5, 6, 7, 8, 9},
    )
    by_id = {item["block_id"]: item for item in labeled}
    assert by_id[1]["block_type"] == "section"
    assert by_id[3]["block_type"] == "header_note"
    assert by_id[4]["block_type"] == "header_note"
    assert by_id[5]["block_type"] == "header_note"
    assert by_id[6]["block_type"] == "paragraph"
    assert by_id[7]["block_type"] == "header_note"
    assert by_id[8]["block_type"] == "header_note"
    assert by_id[9]["block_type"] == "header_note"
    assert is_banner("Design Case Study: View Our Design Case Studies here")
    assert is_banner("Arbisoft Case Study for Zain Cash")


def test_outline_promotes_known_divider_titles_to_section() -> None:
    for text in (
        "ERP, Dashboards, and Reporting Solutions for Non-profit Organisations",
        "AI-powered training content support platforms",
        "Axim Proposals",
        "Content Management System  (CMS)",
        "Cross platform App Case Studies",
        "Data Scraping",
        "Design (Nurture)",
        "Ecommerce",
        "Fintech Design",
        "Healthcare",
        "Instructional Design (Content Management, Development & Course Authoring)",
        "Managed Hosting",
        "ML/AI",
        "Mobile App Development",
        "Multi-Language Support",
        "Multi-tier training academies (vendor-partner-user training)",
        "QA",
        "SaaS",
        "Web / Mobile App Development (Search interface)",
    ):
        blocks = {1: TextBlock(1, 1, text, "paragraph")}
        labeled = outline_from_payload(
            {
                "blocks": [
                    {"block_id": 1, "block_type": "title", "field": "", "children": []},
                ]
            },
            blocks,
            batch_numbers={1},
        )
        assert labeled[0]["block_type"] == "section", text
        assert is_banner(text)


def test_outline_retries_once_when_section_and_headings_lack_title(
    tmp_path,  # noqa: ANN001
    monkeypatch: Any,
) -> None:
    """Section + field headings with 0 titles triggers one extra model call."""
    from src.config import settings

    monkeypatch.setattr(settings, "STRUCTURE_BATCH_SLIDES", 2)
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    divider = presentation.slides.add_slide(blank)
    _write(divider, ["Data Scraping", "www.arbisoft.com"])
    study = presentation.slides.add_slide(blank)
    _write(
        study,
        [
            "Innovative Data Solutions for the Food & Beverage Industry",
            "The Challenge",
            "BurgerIndex faced unstructured data.",
        ],
    )
    presentation.save(path)

    calls = {"n": 0}

    def complete(message: str) -> dict[str, Any]:
        calls["n"] += 1
        blocks = _block_rows(message)
        items: list[dict[str, Any]] = []
        for number, _slide, text in blocks:
            if text == "Data Scraping":
                items.append(
                    {"block_id": number, "block_type": "section", "field": "", "children": []}
                )
            elif text == "Innovative Data Solutions for the Food & Beverage Industry":
                # First reply misses the title; retry corrects it.
                block_type = "paragraph" if calls["n"] == 1 else "title"
                items.append(
                    {
                        "block_id": number,
                        "block_type": block_type,
                        "field": "",
                        "children": [],
                    }
                )
            elif text == "The Challenge":
                items.append(
                    {
                        "block_id": number,
                        "block_type": "heading",
                        "field": "challenge",
                        "children": [],
                    }
                )
            elif is_banner(text):
                items.append(
                    {"block_id": number, "block_type": "header_note", "field": "", "children": []}
                )
            else:
                items.append(
                    {"block_id": number, "block_type": "paragraph", "field": "", "children": []}
                )
        return {"blocks": items}

    drafts = parse_pptx(path, complete=complete)
    assert calls["n"] == 2
    assert len(drafts) == 1
    assert drafts[0].title.startswith("Innovative Data Solutions")
    assert drafts[0].section == "Data Scraping"


def test_outline_skips_title_miss_retry_without_headings(
    tmp_path,  # noqa: ANN001
    monkeypatch: Any,
) -> None:
    """A section-only divider batch must not trigger the title-miss retry."""
    from src.config import settings

    monkeypatch.setattr(settings, "STRUCTURE_BATCH_SLIDES", 1)
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    divider = presentation.slides.add_slide(blank)
    _write(divider, ["Data Scraping", "www.arbisoft.com"])
    presentation.save(path)

    calls = {"n": 0}

    def complete(message: str) -> dict[str, Any]:
        calls["n"] += 1
        blocks = _block_rows(message)
        return _outline_payload(blocks, title_ids=set(), section_ids={_find(blocks, "Data Scraping")})

    assert parse_pptx(path, complete=complete) == []
    assert calls["n"] == 1


def test_joined_travel_divider_becomes_section_not_study(tmp_path) -> None:  # noqa: ANN001
    """Docling can split a divider across lines; joined text must still be a section."""
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    divider = presentation.slides.add_slide(blank)
    _write(
        divider,
        [
            "Travel",
            "(General+",
            "Global Distribution Systems)",
        ],
    )
    study = presentation.slides.add_slide(blank)
    _write(
        study,
        [
            "Case Study: Optimizing Travel Booking through GDS Integration",
            "KAYAK needed GDS connectivity.",
        ],
    )
    presentation.save(path)

    def complete(message: str) -> dict[str, Any]:
        blocks = _block_rows(message)
        items: list[dict[str, Any]] = []
        for number, _slide, text in blocks:
            if text in {
                "Travel",
                "(General+",
                "Global Distribution Systems)",
                "Case Study: Optimizing Travel Booking through GDS Integration",
            }:
                items.append(
                    {"block_id": number, "block_type": "title", "field": "", "children": []}
                )
            elif is_banner(text):
                items.append(
                    {"block_id": number, "block_type": "header_note", "field": "", "children": []}
                )
            else:
                items.append(
                    {"block_id": number, "block_type": "paragraph", "field": "", "children": []}
                )
        return {"blocks": items}

    drafts = parse_pptx(path, complete=complete)
    assert len(drafts) == 1
    assert drafts[0].title.startswith("Case Study: Optimizing Travel Booking")
    assert drafts[0].section == "Travel (General+ Global Distribution Systems)"


def test_parse_discards_invalid_outline_then_commits(
    tmp_path,  # noqa: ANN001
    monkeypatch: Any,
) -> None:
    """Invalid replies must not stay cached; only accepted payloads are committed."""
    from src.config import settings
    from src.decks import parse as parse_mod

    monkeypatch.setattr(settings, "STRUCTURE_LLM_RETRY_LIMIT", 1)
    commits: list[dict[str, Any]] = []
    discards: list[str] = []
    monkeypatch.setattr(
        parse_mod,
        "commit_outline_cache",
        lambda message, payload: commits.append(payload),
    )
    monkeypatch.setattr(
        parse_mod,
        "discard_outline_cache",
        lambda message: discards.append(message),
    )

    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    slide = presentation.slides.add_slide(blank)
    _write(slide, ["Elevating KAYAK's Mobile Experience", "KAYAK needed a faster app."])
    presentation.save(path)

    calls = {"n": 0}

    def fake_outline(message: str) -> dict[str, Any]:
        calls["n"] += 1
        blocks = _block_rows(message)
        good = _outline_payload(
            blocks,
            title_ids={_find(blocks, "Elevating KAYAK's Mobile Experience")},
        )
        if calls["n"] == 1:
            return {
                "blocks": [
                    {
                        "block_id": 5,
                        "block_type": "heading",
                        "field": "challenge",
                        "children": [],
                    },
                    *good["blocks"],
                ]
            }
        return good

    monkeypatch.setattr(parse_mod, "structure_outline", fake_outline)
    drafts = parse_pptx(path)
    assert len(drafts) == 1
    assert calls["n"] == 2
    assert len(discards) == 1
    assert len(commits) == 1
    assert all(item["block_id"] != 5 for item in commits[0]["blocks"])


def test_outline_debug_file_written(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from src.decks import service as deck_service

    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setattr(deck_service, "storage_root", lambda: storage)
    deck_id = uuid4()
    (storage / "decks").mkdir()
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    slide = presentation.slides.add_slide(blank)
    _write(
        slide,
        [
            "Elevating KAYAK's Mobile Experience",
            "KAYAK needed a faster mobile app.",
        ],
    )
    presentation.save(path)

    drafts = parse_pptx(
        path,
        deck_id=deck_id,
        complete=_outline_complete(titles={"Elevating KAYAK's Mobile Experience"}),
    )
    assert len(drafts) == 1
    outline_path = storage / "decks" / f"{deck_id}.outline.json"
    assert outline_path.exists()
    payload = json.loads(outline_path.read_text())
    assert payload["deck_id"] == str(deck_id)
    assert payload["batches"]
    first = payload["batches"][0]["accepted"][0]
    assert "block_text" not in first
    assert "block_id" in first


def test_field_text_copied_from_heading_children(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    slide = presentation.slides.add_slide(blank)
    _write(
        slide,
        [
            "Case Study: Optimizing Travel Booking through GDS Integration",
            "Background",
            "KAYAK serves millions of users operating in 30 countries.",
            "Challenges",
            "As KAYAK expanded its services globally, managing travel data became complex.",
            "Technology Stack",
            "Tech Stack: Python, Django",
        ],
    )
    presentation.save(path)

    drafts = parse_pptx(
        path,
        complete=_outline_complete(
            titles={"Case Study: Optimizing Travel Booking through GDS Integration"}
        ),
    )
    assert len(drafts) == 1
    assert drafts[0].background == "KAYAK serves millions of users operating in 30 countries."
    assert "managing travel data" in drafts[0].challenge
    assert drafts[0].tech_stack == "Tech Stack: Python, Django"
    assert drafts[0].client_name == "KAYAK"
    assert "Background" in drafts[0].raw_text
    assert drafts[0].challenge != "Challenges"


def test_client_name_from_llm_when_named_in_study(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    slide = presentation.slides.add_slide(blank)
    _write(
        slide,
        [
            "Driving Global Science Education with Harvard’s LabXchange",
            "Harvard led the LabXchange science platform.",
        ],
    )
    presentation.save(path)

    drafts = parse_pptx(
        path,
        complete=_outline_complete(
            titles={"Driving Global Science Education with Harvard’s LabXchange"}
        ),
    )
    assert drafts[0].client_name == "Harvard"


def test_client_name_left_empty_when_slide_says_the_client(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    slide = presentation.slides.add_slide(blank)
    _write(
        slide,
        [
            "Enhancing Retail Analytics with Advanced Data Services",
            "The client needed to extract retail data from many websites.",
        ],
    )
    presentation.save(path)

    drafts = parse_pptx(
        path,
        complete=_outline_complete(
            titles={"Enhancing Retail Analytics with Advanced Data Services"}
        ),
    )
    assert drafts[0].client_name is None


def test_later_duplicate_title_overwrites(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from src.config import settings

    monkeypatch.setattr(settings, "STRUCTURE_BATCH_SLIDES", 1)
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    first = presentation.slides.add_slide(blank)
    _write(
        first,
        [
            "Elevating KAYAK's Mobile Experience in Travel Tech",
            "First copy body that should be replaced.",
        ],
    )
    second = presentation.slides.add_slide(blank)
    _write(
        second,
        [
            "Elevating KAYAK's Mobile Experience in Travel Tech",
            "Later copy body that remains.",
        ],
    )
    presentation.save(path)

    drafts = parse_pptx(
        path,
        complete=_outline_complete(titles={"Elevating KAYAK's Mobile Experience in Travel Tech"}),
    )
    assert len(drafts) == 1
    assert "Later copy body that remains" in drafts[0].raw_text
    assert "First copy body" not in drafts[0].raw_text
    assert drafts[0].slide_numbers == [2]


def test_banner_marked_as_title_becomes_header_note(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    slide = presentation.slides.add_slide(blank)
    _write(
        slide,
        [
            "Arbisoft Proposal for XYZ Ltd",
            "Elevating KAYAK's Mobile Experience",
            "KAYAK needed a faster mobile app.",
        ],
    )
    presentation.save(path)

    drafts = parse_pptx(
        path,
        complete=_outline_complete(
            titles={
                "Arbisoft Proposal for XYZ Ltd",
                "Elevating KAYAK's Mobile Experience",
            }
        ),
    )
    assert len(drafts) == 1
    assert drafts[0].title == "Elevating KAYAK's Mobile Experience"


def test_banner_only_batch_builds_no_study(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    slide = presentation.slides.add_slide(blank)
    _write(slide, ["Arbisoft Proposal for XYZ Ltd", "Confidential"])
    presentation.save(path)

    assert (
        parse_pptx(
            path,
            complete=_outline_complete(titles={"Arbisoft Proposal for XYZ Ltd", "Confidential"}),
        )
        == []
    )


def test_section_slide_sets_section(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from src.config import settings

    monkeypatch.setattr(settings, "STRUCTURE_BATCH_SLIDES", 1)
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    divider = presentation.slides.add_slide(blank)
    _write(divider, ["Mobile App Development"])
    study = presentation.slides.add_slide(blank)
    _write(
        study,
        [
            "Elevating KAYAK's Mobile Experience",
            "KAYAK needed a faster mobile app.",
        ],
    )
    presentation.save(path)

    drafts = parse_pptx(
        path,
        complete=_outline_complete(
            titles={"Elevating KAYAK's Mobile Experience"},
            sections={"Mobile App Development"},
        ),
    )
    assert len(drafts) == 1
    assert drafts[0].section == "Mobile App Development"
    assert drafts[0].slide_numbers == [2]


def test_cover_beats_general_format_section(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """Cover banner becomes the section even when the model marks General Format."""
    from src.config import settings

    monkeypatch.setattr(settings, "STRUCTURE_BATCH_SLIDES", 2)
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    cover = presentation.slides.add_slide(blank)
    _write(cover, ["Arbisoft Case Studies", "www.arbisoft.com"])
    legend = presentation.slides.add_slide(blank)
    _write(
        legend,
        [
            "General Format",
            "What the Company Is?",
            "What We Did?",
            "Covers all industries",
        ],
    )
    study = presentation.slides.add_slide(blank)
    _write(
        study,
        [
            "Elevating KAYAK's Mobile Experience",
            "KAYAK needed a faster mobile app.",
        ],
    )
    presentation.save(path)

    def complete(message: str) -> dict[str, Any]:
        blocks = _block_rows(message)
        items: list[dict[str, Any]] = []
        for number, _slide, text in blocks:
            lowered = text.strip().lower()
            if text == "Arbisoft Case Studies":
                items.append(
                    {"block_id": number, "block_type": "header_note", "field": "", "children": []}
                )
            elif text == "General Format":
                items.append(
                    {"block_id": number, "block_type": "section", "field": "", "children": []}
                )
            elif text == "Elevating KAYAK's Mobile Experience":
                items.append(
                    {"block_id": number, "block_type": "title", "field": "", "children": []}
                )
            elif lowered in {"what the company is?", "what we did?"}:
                items.append(
                    {
                        "block_id": number,
                        "block_type": "heading",
                        "field": "background",
                        "children": [],
                    }
                )
            elif is_banner(text):
                items.append(
                    {"block_id": number, "block_type": "header_note", "field": "", "children": []}
                )
            else:
                items.append(
                    {"block_id": number, "block_type": "paragraph", "field": "", "children": []}
                )
        return {"blocks": items}

    drafts = parse_pptx(path, complete=complete)
    assert len(drafts) == 1
    assert drafts[0].section == "Arbisoft Case Studies"
    assert drafts[0].title == "Elevating KAYAK's Mobile Experience"


def test_footer_only_next_slide_dropped_from_slide_numbers(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from src.config import settings

    monkeypatch.setattr(settings, "STRUCTURE_BATCH_SLIDES", 1)
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    first = presentation.slides.add_slide(blank)
    _write(
        first,
        [
            "Analytics and Device Management for Teach the World",
            "Teach the World needed device analytics.",
        ],
    )
    second = presentation.slides.add_slide(blank)
    _write(
        second,
        [
            "Arbisoft Case Studies for Non-Profit Organizations",
            "Confidential",
        ],
    )
    third = presentation.slides.add_slide(blank)
    _write(
        third,
        [
            "Next Study After Banner",
            "Next body.",
        ],
    )
    presentation.save(path)

    drafts = parse_pptx(
        path,
        complete=_outline_complete(
            titles={
                "Analytics and Device Management for Teach the World",
                "Next Study After Banner",
            },
            sections={"Arbisoft Case Studies for Non-Profit Organizations"},
        ),
    )
    teach = next(
        draft for draft in drafts if draft.title.startswith("Analytics and Device Management")
    )
    assert teach.slide_numbers == [1]


def test_heading_id_not_stored_as_field_text(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    slide = presentation.slides.add_slide(blank)
    _write(
        slide,
        [
            "Cross-Platform Development and Security Enhancement for Landit",
            "The Challenge",
            "Landit needed a mobile app.",
            "The Outcome",
            "Landit launched a secure mobile experience.",
        ],
    )
    presentation.save(path)

    drafts = parse_pptx(
        path,
        complete=_outline_complete(
            titles={"Cross-Platform Development and Security Enhancement for Landit"}
        ),
    )
    assert drafts[0].challenge == "Landit needed a mobile app."
    assert drafts[0].outcome == "Landit launched a secure mobile experience."
    assert drafts[0].challenge != "The Challenge"


def test_raw_text_drops_title_and_banners(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    slide = presentation.slides.add_slide(blank)
    _write(
        slide,
        [
            "Arbisoft Proposal for XYZ Ltd",
            "Edly Proposal for Axim Collaborative",
            "Confidential",
            "Proposal valid till December 1, 2026",
            "Proposal valid  till",
            "February 13, 2025",
            "Page ‹#›",
            "Cross-Platform Development and Security Enhancement for Landit",
            "The Challenge",
            "Landit needed a mobile app.",
            "The Approach",
            "We recommended React Native.",
        ],
    )
    presentation.save(path)
    slides = read_pptx_blocks(path)
    blocks = {block.number: block for slide in slides for block in slide.blocks}
    numbers = sorted(blocks)
    raw = build_raw_text(
        numbers,
        "Cross-Platform Development and Security Enhancement for Landit",
        blocks,
    )
    assert "Arbisoft Proposal" not in raw
    assert "Edly Proposal" not in raw
    assert "Confidential" not in raw
    assert "valid till" not in raw
    assert "February 13" not in raw
    assert "Page" not in raw
    assert "Landit needed" in raw
    assert "The Challenge" in raw
    assert "React Native" in raw
    assert "Cross-Platform Development" not in raw
    assert is_banner("Proposal valid  till")
    assert is_banner("Proposal Valid Until November 13, 2025")
    assert is_banner("Page ‹#›")


def test_one_chunk_per_filled_field(monkeypatch) -> None:  # noqa: ANN001
    from sqlmodel import select
    from src.auth.models import User
    from src.decks import service as deck_service
    from src.decks.drafts import StudyDraft
    from src.decks.models import DeckImport, StudyChunk

    monkeypatch.setattr(
        deck_service,
        "embed_texts",
        lambda texts: [[0.1] * 384 for _ in texts],
    )
    draft = StudyDraft(
        section="Mobile",
        title="Full fields",
        client_name="KAYAK",
        raw_text="body",
        background="bg",
        challenge="ch",
        approach="ap",
        solution="so",
        outcome="ou",
        tech_stack="tech",
    )
    with deck_service.get_sync_session() as session:
        user = User(username="person@example.com", password_hash="x", name="Person")
        session.add(user)
        session.flush()
        deck = DeckImport(
            uploaded_by=user.id, file_name="v1.pptx", sha256="a" * 64, status="running"
        )
        session.add(deck)
        session.flush()
        deck_service.persist_drafts(session, deck, [draft])
        session.commit()
        chunks = list(session.exec(select(StudyChunk)).all())
    assert len(chunks) == 6
    assert [chunk.position for chunk in chunks] == [1, 2, 3, 4, 5, 6]


def test_section_headers_stay_out_of_raw_text() -> None:
    headers = (
        "Arbisoft E-commerce Case Studies",
        "Arbisoft Case Studies for Humanitarian and Nonprofit Organizations",
        "Arbisoft Case Studies for Non-Profit Organizations",
        "Arbisoft Case Study for Zain Cash",
        "Broader IT Solutions for non-profit Organisations",
        "ERP, Dashboards, and Reporting Solutions for Non-profit Organisations",
        "AI-powered training content support platforms",
        "Axim Proposals",
        "Content Management System  (CMS)",
        "Cross platform App Case Studies",
        "Data Scraping",
        "Design (Nurture)",
        "Ecommerce",
        "Fintech Design",
        "Healthcare",
        "Instructional Design (Content Management, Development & Course Authoring)",
        "Managed Hosting",
        "ML/AI",
        "Mobile App Development",
        "Multi-Language Support",
        "Multi-tier training academies (vendor-partner-user training)",
        "QA",
        "SaaS",
        "Travel (General+ Global Distribution Systems)",
        "Web / Mobile App Development (Search interface)",
        "Edly Instructional Design "
        "(Content Management, Development & Course Authoring) Case Studies",
        "Edly Case Studies",
    )
    for header in headers:
        assert is_banner(header)
    blocks = {
        1: TextBlock(1, 1, "Edly Case Studies", "paragraph"),
        2: TextBlock(2, 1, "A client story", "paragraph"),
        3: TextBlock(3, 1, "Edly Case Studies", "paragraph"),
        4: TextBlock(4, 1, "The team shipped the portal.", "paragraph"),
    }
    raw = build_raw_text(
        [1, 2, 3, 4],
        "A client story",
        blocks,
        title_block_numbers=[2],
    )
    assert raw == "The team shipped the portal."


def _outline_complete(
    *,
    titles: set[str],
    sections: set[str] | None = None,
) -> Any:
    section_names = sections or set()

    def complete(message: str) -> dict[str, Any]:
        blocks = _block_rows(message)
        title_ids: set[int] = set()
        section_ids: set[int] = set()
        for number, _slide, text in blocks:
            if text in titles:
                title_ids.add(number)
            if text in section_names:
                section_ids.add(number)
        return _outline_payload(blocks, title_ids=title_ids, section_ids=section_ids)

    return complete


def _outline_payload(
    blocks: list[tuple[int, int, str]],
    *,
    title_ids: set[int],
    section_ids: set[int] | None = None,
) -> dict[str, Any]:
    section_ids = section_ids or set()
    nested: set[int] = set()
    items: list[dict[str, Any]] = []
    open_heading: dict[str, Any] | None = None
    for number, _slide, text in blocks:
        lowered = text.strip().rstrip(":").strip().lower()
        if number in title_ids:
            open_heading = None
            items.append({"block_id": number, "block_type": "title"})
            continue
        if number in section_ids:
            open_heading = None
            items.append({"block_id": number, "block_type": "section"})
            continue
        if is_banner(text):
            items.append({"block_id": number, "block_type": "header_note"})
            continue
        if lowered in _HEADINGS:
            entry = {
                "block_id": number,
                "block_type": "heading",
                "field": _HEADINGS[lowered],
                "children": [],
            }
            items.append(entry)
            open_heading = entry
            continue
        if text.lower().startswith("tech stack:"):
            if open_heading is not None and open_heading.get("field") == "tech_stack":
                open_heading["children"].append(number)
                nested.add(number)
            items.append({"block_id": number, "block_type": "paragraph"})
            continue
        if open_heading is not None:
            open_heading["children"].append(number)
            nested.add(number)
        items.append({"block_id": number, "block_type": "paragraph"})
    return {"blocks": items}


def _block_rows(message: str) -> list[tuple[int, int, str]]:
    rows: list[tuple[int, int, str]] = []
    for line in message.splitlines():
        match = re.match(r"^(\d+)\. slide=(\d+) text=(.*)$", line)
        if match:
            rows.append((int(match.group(1)), int(match.group(2)), match.group(3)))
    return rows


def _find(blocks: list[tuple[int, int, str]], needle: str) -> int:
    for number, _slide, text in blocks:
        if needle in text:
            return number
    raise AssertionError(f"Missing block containing {needle!r}")


def _write(slide, lines: list[str]) -> None:  # noqa: ANN001
    _write_at(slide, 0.4, lines)


def _write_at(slide, top: float, lines: list[str], size: int | None = None) -> None:  # noqa: ANN001
    box = slide.shapes.add_textbox(Inches(0.4), Inches(top), Inches(8), Inches(3))
    frame = box.text_frame
    frame.text = lines[0]
    _set_size(frame.paragraphs[0], size)
    for line in lines[1:]:
        paragraph = frame.add_paragraph()
        paragraph.text = line
        _set_size(paragraph, size)


def _set_size(paragraph, size: int | None) -> None:  # noqa: ANN001
    if size is None:
        return
    for run in paragraph.runs:
        run.font.size = Pt(size)
