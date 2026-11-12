"""Footer lines never become study text."""

from pptx import Presentation
from pptx.util import Inches, Pt
from src.decks.parse import parse_pptx


def test_footer_text_absent_from_raw_text(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    divider = presentation.slides.add_slide(blank)
    _write(divider, ["Mobile App Development"])
    study = presentation.slides.add_slide(blank)
    _write(
        study,
        [
            "www.arbisoft.com",
            "Arbisoft Proposal for XYZ Ltd",
            "Confidential",
            "Proposal valid till January 13, 2025",
            "Elevating KAYAK's Mobile Experience",
            "KAYAK, a travel search company, needed a faster mobile app.",
        ],
    )
    continued = presentation.slides.add_slide(blank)
    _write(continued, ["and the team shipped the iOS client the same quarter."])
    presentation.save(path)

    drafts = parse_pptx(path)
    assert len(drafts) == 1
    raw = drafts[0].raw_text
    assert "Arbisoft Proposal" not in raw
    assert "Confidential" not in raw
    assert "valid till" not in raw.lower()
    assert "www.arbisoft.com" not in raw
    assert "January 13, 2025" not in raw
    assert "faster mobile app" in raw
    assert "iOS client" in raw
    assert drafts[0].section == "Mobile App Development"
    assert drafts[0].client_name is None


def test_second_client_on_one_slide_splits(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    slide = presentation.slides.add_slide(blank)
    _write(
        slide,
        [
            "The Challenge",
            "Landit, a career platform, needed a mobile app.",
            "The Challenge",
            "Moonbeam, a podcast product, needed recommendations.",
        ],
    )
    presentation.save(path)
    drafts = parse_pptx(path)
    assert len(drafts) == 2
    assert [draft.title for draft in drafts] == [
        "Landit, a career platform, needed a mobile app",
        "Moonbeam, a podcast product, needed recommendations",
    ]


def test_title_above_the_challenge_is_kept(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    slide = presentation.slides.add_slide(blank)
    # Body shape is stored first, which is how the Landit slide is saved.
    _write_at(
        slide,
        3.2,
        [
            "The Challenge",
            "Landit, a leader in personalized career pathing technology, needed a mobile app.",
            "The Approach",
            "To provide a seamless solution, we recommended React Native.",
            "Additionally, to enhance user experience across platforms, we developed custom deep",
        ],
    )
    _write_at(slide, 1.2, ["Cross-Platform Development and Security Enhancement for Landit"])
    continued = presentation.slides.add_slide(blank)
    _write_at(
        continued,
        1.5,
        [
            "and universal linking solutions, eliminating reliance on third-party services.",
            "The Outcome",
            "Landit launched a secure mobile experience.",
        ],
    )
    presentation.save(path)

    drafts = parse_pptx(path)
    assert len(drafts) == 1
    assert drafts[0].title == "Cross-Platform Development and Security Enhancement for Landit"
    assert "Additionally" in drafts[0].raw_text
    assert "universal linking" in drafts[0].raw_text
    assert "Landit launched" in drafts[0].outcome


def test_background_then_challenges_stays_one_study(tmp_path) -> None:  # noqa: ANN001
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
        ],
    )
    presentation.save(path)

    drafts = parse_pptx(path)
    assert len(drafts) == 1
    assert drafts[0].title == "Case Study: Optimizing Travel Booking through GDS Integration"
    assert "millions of users" in drafts[0].background
    assert "managing travel data" in drafts[0].challenge


def test_challenges_faced_stays_with_the_case_study(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    slide = presentation.slides.add_slide(blank)
    _write(
        slide,
        [
            "Case Study: Edly Collaborates with Gen+ to Revolutionize Meta-Skills Education",
            "Background",
            "Gen+ is a Scotland-based organization.",
            "Challenges Faced",
            "Gen+ aimed to develop an engaging learning platform.",
        ],
    )
    presentation.save(path)

    drafts = parse_pptx(path)
    assert len(drafts) == 1
    assert drafts[0].title.startswith("Case Study: Edly Collaborates with Gen+")
    assert "engaging learning platform" in drafts[0].challenge


def test_tech_stack_line_starts_the_next_study(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    slide = presentation.slides.add_slide(blank)
    _write(
        slide,
        [
            "Elevating KAYAK's Mobile Experience in Travel Tech",
            "KAYAK needed a faster mobile app.",
            "Tech Stack: Native Android, Native iOS",
            "Enabling In-Office 3D Printing for Orthodontics",
            "Braces on Demand needed a platform for remote printing.",
        ],
    )
    presentation.save(path)

    drafts = parse_pptx(path)
    assert [draft.title for draft in drafts] == [
        "Elevating KAYAK's Mobile Experience in Travel Tech",
        "Enabling In-Office 3D Printing for Orthodontics",
    ]


def test_outcome_list_stays_with_the_open_study(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    study = presentation.slides.add_slide(blank)
    _write(
        study,
        [
            "Case Study: MIT Achieves Administrative Efficiency Through Data-Driven Insights",
            "The Challenge",
            "MIT faced key challenges related to metrics and data analytics.",
        ],
    )
    continued = presentation.slides.add_slide(blank)
    _write(
        continued,
        [
            "The integration of analytics tools improved MIT’s data-driven approach:",
            "Optimized Administrative Workflow: Reduced the complexity of course management.",
        ],
    )
    presentation.save(path)

    drafts = parse_pptx(path)
    assert len(drafts) == 1
    assert drafts[0].title.startswith("Case Study: MIT Achieves")
    assert "Optimized Administrative Workflow" in drafts[0].raw_text


def test_running_header_and_wrapped_title(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "deck.pptx"
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    slide = presentation.slides.add_slide(blank)
    _write_at(slide, 0.3, ["Arbisoft E-commerce Case Studies"], size=10)
    _write_at(slide, 3.0, ["The Challenge", "Eileen set out to give brands visibility."], size=16)
    _write_at(slide, 1.2, ["Enabling Brands With", "Real-Time Store-Level Insights"], size=24)
    presentation.save(path)

    drafts = parse_pptx(path)
    assert len(drafts) == 1
    assert drafts[0].title == "Enabling Brands With Real-Time Store-Level Insights"
    assert "Arbisoft E-commerce" not in drafts[0].raw_text


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
