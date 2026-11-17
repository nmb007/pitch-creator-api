"""Title-span ingest with a fake model produces study drafts."""

from __future__ import annotations

import re
from typing import Any

from pptx import Presentation
from pptx.util import Inches, Pt
from src.decks.blocks import TextBlock, read_pptx_blocks
from src.decks.parse import parse_pptx
from src.decks.validate import build_raw_text, titles_from_payload


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

    def complete(message: str) -> dict[str, Any]:
        if _is_field_message(message):
            return _fields_for(message)
        blocks = _blocks_from_message(message)
        return {
            "sections": [],
            "titles": [
                _title(blocks, "Elevating KAYAK's Mobile Experience in Travel Tech"),
                _title(blocks, "Enabling In-Office 3D Printing for Orthodontics"),
            ],
        }

    drafts = parse_pptx(path, complete=complete)
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


def test_lowercase_line_stays_in_open_study(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    first = presentation.slides.add_slide(blank)
    _write(
        first,
        [
            "Elevating KAYAK's Mobile Experience",
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

    def complete(message: str) -> dict[str, Any]:
        if _is_field_message(message):
            return _fields_for(message)
        blocks = _blocks_from_message(message)
        return {
            "sections": [],
            "titles": [
                _title(blocks, "Elevating KAYAK's Mobile Experience"),
                _title(blocks, "Next Study Title About Orthodontics"),
            ],
        }

    drafts = parse_pptx(path, complete=complete)
    assert len(drafts) == 2
    assert "learning, and test automation" in drafts[0].raw_text
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

    def complete(message: str) -> dict[str, Any]:
        if _is_field_message(message):
            return _fields_for(message)
        blocks = _blocks_from_message(message)
        return {
            "sections": [],
            "titles": [
                _title(blocks, "Landit, a career platform, needed a mobile app."),
                _title(blocks, "Moonbeam, a podcast product, needed recommendations."),
            ],
        }

    drafts = parse_pptx(path, complete=complete)
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
        if _is_field_message(message):
            return _fields_for(message)
        blocks = _blocks_from_message(message)
        first = _find(blocks, "Enabling Brands With")
        second = _find(blocks, "Real-Time Store-Level Insights")
        return {
            "sections": [],
            "titles": [{"block_numbers": [first, second]}],
        }

    drafts = parse_pptx(path, complete=complete)
    assert len(drafts) == 1
    assert drafts[0].title == "Enabling Brands With Real-Time Store-Level Insights"
    assert "Eileen set out" in drafts[0].raw_text
    assert "Arbisoft E-commerce" not in drafts[0].raw_text


def test_non_adjacent_title_pair_splits_into_two_titles() -> None:
    blocks = {
        613: TextBlock(613, 63, "QA Collaboration with edX: A Decade of Innovation", "paragraph"),
        614: TextBlock(614, 63, "The Challenge", "paragraph"),
        626: TextBlock(626, 64, "Global Crew Accommodation Solutions Platform", "paragraph"),
    }

    _sections, titles, held = titles_from_payload(
        {"sections": [], "titles": [{"block_numbers": [613, 626]}]},
        blocks,
        batch_numbers={613, 614, 626},
    )

    assert held is None
    assert [item["title"] for item in titles] == [
        "QA Collaboration with edX: A Decade of Innovation",
        "Global Crew Accommodation Solutions Platform",
    ]
    assert [item["block_numbers"] for item in titles] == [[613], [626]]


def test_wrapped_title_hold_across_slides(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from src.config import settings

    monkeypatch.setattr(settings, "STRUCTURE_BATCH_SLIDES", 1)
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    first = presentation.slides.add_slide(blank)
    _write(first, ["Enabling Brands With"])
    second = presentation.slides.add_slide(blank)
    _write(
        second,
        [
            "Real-Time Store-Level Insights",
            "Eileen set out to give brands visibility.",
        ],
    )
    presentation.save(path)
    calls: list[str] = []

    def complete(message: str) -> dict[str, Any]:
        if _is_field_message(message):
            return _fields_for(message)
        calls.append(message)
        blocks = _blocks_from_message(message)
        if "Held open wrapped title" not in message:
            first = _find(blocks, "Enabling Brands With")
            return {
                "sections": [],
                "titles": [{"block_numbers": [first], "incomplete": True}],
            }
        # Held first line is only in the held JSON; second line is in this batch.
        held_match = re.search(r'"block_numbers":\s*\[(\d+)\]', message)
        assert held_match is not None
        held_first = int(held_match.group(1))
        second = _find(blocks, "Real-Time Store-Level Insights")
        return {
            "sections": [],
            "titles": [{"block_numbers": [held_first, second]}],
        }

    drafts = parse_pptx(path, complete=complete)
    assert len(drafts) == 1
    assert len(calls) == 2
    assert "Held open wrapped title" in calls[1]
    assert drafts[0].title == "Enabling Brands With Real-Time Store-Level Insights"
    assert "Eileen set out" in drafts[0].raw_text


def test_field_text_copied_from_block_numbers(tmp_path) -> None:  # noqa: ANN001
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
            "Tech Stack: Python, Django",
        ],
    )
    presentation.save(path)

    def complete(message: str) -> dict[str, Any]:
        if _is_field_message(message):
            blocks = _blocks_from_message(message)
            return {
                "client_name": "KAYAK",
                "background": [_find(blocks, "KAYAK serves millions")],
                "challenge": [_find(blocks, "managing travel data")],
                "approach": [],
                "solution": [],
                "outcome": [],
                "tech_stack": [_find(blocks, "Tech Stack: Python")],
            }
        blocks = _blocks_from_message(message)
        return {
            "sections": [],
            "titles": [
                _title(
                    blocks,
                    "Case Study: Optimizing Travel Booking through GDS Integration",
                )
            ],
        }

    drafts = parse_pptx(path, complete=complete)
    assert len(drafts) == 1
    assert drafts[0].background == "KAYAK serves millions of users operating in 30 countries."
    assert "managing travel data" in drafts[0].challenge
    assert drafts[0].tech_stack == "Tech Stack: Python, Django"
    assert drafts[0].client_name == "KAYAK"
    assert "Background" in drafts[0].raw_text


def test_client_name_dropped_when_not_in_blocks(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    slide = presentation.slides.add_slide(blank)
    _write(
        slide,
        [
            "Expanding Access to Education for Global Social Impact",
            "The work expanded access to education.",
        ],
    )
    presentation.save(path)

    def complete(message: str) -> dict[str, Any]:
        if _is_field_message(message):
            return {
                "client_name": "Philanthropy University",
                "background": [],
                "challenge": [],
                "approach": [],
                "solution": [],
                "outcome": [],
                "tech_stack": [],
            }
        blocks = _blocks_from_message(message)
        return {
            "sections": [],
            "titles": [_title(blocks, "Expanding Access to Education for Global Social Impact")],
        }

    drafts = parse_pptx(path, complete=complete)
    assert len(drafts) == 1
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

    def complete(message: str) -> dict[str, Any]:
        if _is_field_message(message):
            blocks = _blocks_from_message(message)
            if "Later copy" in message:
                return {
                    "client_name": "KAYAK",
                    "background": [_find(blocks, "Later copy body")],
                    "challenge": [],
                    "approach": [],
                    "solution": [],
                    "outcome": [],
                    "tech_stack": [],
                }
            return {
                "client_name": "KAYAK",
                "background": [_find(blocks, "First copy body")],
                "challenge": [],
                "approach": [],
                "solution": [],
                "outcome": [],
                "tech_stack": [],
            }
        blocks = _blocks_from_message(message)
        return {
            "sections": [],
            "titles": [_title(blocks, "Elevating KAYAK's Mobile Experience in Travel Tech")],
        }

    drafts = parse_pptx(path, complete=complete)
    assert len(drafts) == 1
    assert "Later copy body that remains" in drafts[0].raw_text
    assert "First copy body" not in drafts[0].raw_text
    assert drafts[0].background == "Later copy body that remains."
    assert drafts[0].slide_numbers == [2]


def test_banner_marked_as_title_is_dropped(tmp_path) -> None:  # noqa: ANN001
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

    def complete(message: str) -> dict[str, Any]:
        if _is_field_message(message):
            return _fields_for(message)
        blocks = _blocks_from_message(message)
        return {
            "sections": [],
            "titles": [
                _title(blocks, "Arbisoft Proposal for XYZ Ltd"),
                _title(blocks, "Elevating KAYAK's Mobile Experience"),
            ],
        }

    drafts = parse_pptx(path, complete=complete)
    assert len(drafts) == 1
    assert drafts[0].title == "Elevating KAYAK's Mobile Experience"


def test_banner_only_title_marks_are_skipped(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    slide = presentation.slides.add_slide(blank)
    _write(slide, ["Arbisoft Proposal for XYZ Ltd", "Confidential"])
    presentation.save(path)

    def complete(message: str) -> dict[str, Any]:
        if _is_field_message(message):
            return _fields_for(message)
        blocks = _blocks_from_message(message)
        return {
            "sections": [],
            "titles": [
                {"block_numbers": [_find(blocks, "Arbisoft Proposal")]},
                {"block_numbers": [_find(blocks, "Confidential")]},
            ],
        }

    assert parse_pptx(path, complete=complete) == []


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

    def complete(message: str) -> dict[str, Any]:
        if _is_field_message(message):
            return _fields_for(message)
        blocks = _blocks_from_message(message)
        if "Elevating KAYAK" not in message:
            number = _find(blocks, "Mobile App Development")
            return {
                "sections": [{"block_number": number}],
                "titles": [],
            }
        return {
            "sections": [],
            "titles": [_title(blocks, "Elevating KAYAK's Mobile Experience")],
        }

    drafts = parse_pptx(path, complete=complete)
    assert len(drafts) == 1
    assert drafts[0].section == "Mobile App Development"


def test_empty_title_window_is_skipped(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from src.config import settings

    monkeypatch.setattr(settings, "STRUCTURE_BATCH_SLIDES", 1)
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    fmt = presentation.slides.add_slide(blank)
    _write(fmt, ["General Format", "What the Company Is?", "What We Did?"])
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
        if _is_field_message(message):
            return _fields_for(message)
        blocks = _blocks_from_message(message)
        if "Elevating KAYAK" not in message:
            return {"sections": [], "titles": []}
        return {
            "sections": [],
            "titles": [_title(blocks, "Elevating KAYAK's Mobile Experience")],
        }

    drafts = parse_pptx(path, complete=complete)
    assert len(drafts) == 1
    assert drafts[0].title == "Elevating KAYAK's Mobile Experience"


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
    assert "Landit needed" in raw
    assert "The Challenge" in raw
    assert "React Native" in raw
    assert "Cross-Platform Development" not in raw


def test_out_of_span_field_blocks_ignored(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    slide = presentation.slides.add_slide(blank)
    _write(
        slide,
        [
            "Elevating KAYAK's Mobile Experience in Travel Tech",
            "KAYAK needed a faster mobile app.",
            "Enabling In-Office 3D Printing for Orthodontics",
            "Braces on Demand needed a platform for remote printing.",
        ],
    )
    presentation.save(path)

    braces_block: list[int] = []

    def complete(message: str) -> dict[str, Any]:
        if _is_field_message(message):
            blocks = _blocks_from_message(message)
            if "Elevating KAYAK" in message.split("Blocks:", 1)[0]:
                # Include a next-study block number; code must ignore it.
                return {
                    "client_name": "KAYAK",
                    "background": [_find(blocks, "KAYAK needed"), braces_block[0]],
                    "challenge": [],
                    "approach": [],
                    "solution": [],
                    "outcome": [],
                    "tech_stack": [],
                }
            return _fields_for(message)
        blocks = _blocks_from_message(message)
        braces_block.clear()
        braces_block.append(_find(blocks, "Braces on Demand"))
        return {
            "sections": [],
            "titles": [
                _title(blocks, "Elevating KAYAK's Mobile Experience in Travel Tech"),
                _title(blocks, "Enabling In-Office 3D Printing for Orthodontics"),
            ],
        }

    drafts = parse_pptx(path, complete=complete)
    assert "Braces on Demand" not in drafts[0].raw_text
    assert "Braces on Demand" not in drafts[0].background
    assert drafts[0].background == "KAYAK needed a faster mobile app."


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


def _is_field_message(message: str) -> bool:
    return message.startswith("Section:")


def _fields_for(message: str) -> dict[str, Any]:
    """Put every body block into background so drafts still have searchable text."""
    blocks = _blocks_from_message(message)
    return {
        "client_name": "",
        "background": [number for number, _ in blocks],
        "challenge": [],
        "approach": [],
        "solution": [],
        "outcome": [],
        "tech_stack": [],
    }


def _title(blocks: list[tuple[int, str]], text: str) -> dict[str, Any]:
    return {"block_numbers": [_find(blocks, text)]}


def _blocks_from_message(message: str) -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    for line in message.splitlines():
        match = re.match(r"^(\d+)\. slide=(\d+) text=(.*)$", line)
        if match:
            rows.append((int(match.group(1)), line))
    return rows


def _find(blocks: list[tuple[int, str]], needle: str) -> int:
    for number, line in blocks:
        if needle in line:
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
