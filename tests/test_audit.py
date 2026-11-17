"""Deck ingest audit checks the database against PPTX blocks without calling gpt-oss."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID, uuid4

from pptx import Presentation
from pptx.util import Inches
from sqlmodel import select
from src.auth.models import User
from src.decks import service as deck_service
from src.decks.audit import checks as audit_checks
from src.decks.audit import format_report, run_audit
from src.decks.blocks import SlideBlocks, TextBlock
from src.decks.drafts import ImageDraft
from src.decks.models import DeckImport, Study, StudyChunk, StudyImage, StudyVersion


def test_clean_study_scores_fully(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    deck_id, slides = _seed_clean(monkeypatch, tmp_path)
    monkeypatch.setattr(audit_checks, "read_pptx_blocks", lambda path, deck_id=None: slides)
    monkeypatch.setattr(audit_checks, "embed_texts", _fake_embed)
    report = run_audit(deck_id)
    assert report.score_percent == 100.0
    assert report.studies_fully_matched == 1
    assert report.unclaimed_count == 0
    assert (Path(deck_service.storage_root()) / "reports" / f"{deck_id}.md").exists()
    text = format_report(report)
    assert "Deck-only issues" in text
    assert "Studies with issues" in text
    assert "Text in the file stored by no study" in text
    assert "Field overlaps (review)" in text
    assert "Needs attention" not in text
    assert "Match:" not in text
    assert text.count("- None") >= 4


def test_forbidden_title_the_challenge(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    slides = [
        SlideBlocks(
            number=1,
            blocks=[
                TextBlock(1, 1, "The Challenge", "paragraph"),
                TextBlock(2, 1, "KAYAK needed a faster app.", "paragraph"),
            ],
        )
    ]
    deck_id = _seed_study(
        monkeypatch,
        tmp_path,
        slides=slides,
        section="General",
        title="The Challenge",
        client_name="KAYAK",
        background="KAYAK needed a faster app.",
        raw_text="KAYAK needed a faster app.",
        slide_numbers=[1],
    )
    monkeypatch.setattr(audit_checks, "read_pptx_blocks", lambda path, deck_id=None: slides)
    monkeypatch.setattr(audit_checks, "embed_texts", _fake_embed)
    report = run_audit(deck_id)
    assert any(
        check.status == "incorrect" and "field heading" in check.message
        for check in report.attention
    )


def test_banner_titles(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    titles = (
        "Edly Proposal for XYZ Ltd",
        "Edly Proposal for Axim Collaborative",
        "Axim Proposals",
        "edly.io",
    )
    for title in titles:
        slides = [
            SlideBlocks(
                number=1,
                blocks=[
                    TextBlock(1, 1, title, "paragraph"),
                    TextBlock(2, 1, "Client needed help.", "paragraph"),
                ],
            )
        ]
        deck_id = _seed_study(
            monkeypatch,
            tmp_path,
            slides=slides,
            section="General",
            title=title,
            client_name="Client",
            background="Client needed help.",
            raw_text="Client needed help.",
            slide_numbers=[1],
        )

        def fake_read(path, deck_id=None, *, _slides=slides):  # noqa: ANN001
            del path, deck_id
            return _slides

        monkeypatch.setattr(audit_checks, "read_pptx_blocks", fake_read)
        monkeypatch.setattr(audit_checks, "embed_texts", _fake_embed)
        report = run_audit(deck_id)
        assert any(
            check.status == "incorrect" and "banner" in check.message
            for check in report.attention
        ), title


def test_wrong_section_divider(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    slides = [
        SlideBlocks(number=1, blocks=[TextBlock(1, 1, "Mobile App Development", "paragraph")]),
        SlideBlocks(
            number=2,
            blocks=[
                TextBlock(2, 2, "KAYAK mobile", "paragraph"),
                TextBlock(3, 2, "KAYAK needed a faster app.", "paragraph"),
            ],
        ),
        SlideBlocks(number=3, blocks=[TextBlock(4, 3, "Ecommerce", "paragraph")]),
        SlideBlocks(
            number=4,
            blocks=[
                TextBlock(5, 4, "Wanderu travel", "paragraph"),
                TextBlock(6, 4, "Wanderu added carriers.", "paragraph"),
            ],
        ),
    ]
    deck_id = _seed_study(
        monkeypatch,
        tmp_path,
        slides=slides,
        section="Ecommerce",
        title="KAYAK mobile",
        client_name="KAYAK",
        background="KAYAK needed a faster app.",
        raw_text="KAYAK needed a faster app.",
        slide_numbers=[2],
        extra_studies=[
            {
                "section": "Ecommerce",
                "title": "Wanderu travel",
                "client_name": "Wanderu",
                "background": "Wanderu added carriers.",
                "raw_text": "Wanderu added carriers.",
                "slide_numbers": [4],
            }
        ],
    )
    monkeypatch.setattr(audit_checks, "read_pptx_blocks", lambda path, deck_id=None: slides)
    monkeypatch.setattr(audit_checks, "embed_texts", _fake_embed)
    report = run_audit(deck_id)
    # Title lives under Mobile App Development; stored section Ecommerce finds no title there.
    assert any(
        check.status == "missing"
        and "KAYAK mobile" in check.message
        and 'section "Ecommerce"' in check.message
        for check in report.attention
    )


def test_null_client_name(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    deck_id, slides = _seed_clean(monkeypatch, tmp_path, client_name=None)
    monkeypatch.setattr(audit_checks, "read_pptx_blocks", lambda path, deck_id=None: slides)
    monkeypatch.setattr(audit_checks, "embed_texts", _fake_embed)
    report = run_audit(deck_id)
    assert any(
        check.status == "missing" and "no client_name" in check.message
        for check in report.attention
    )


def test_banner_in_raw_text(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    slides = [
        SlideBlocks(
            number=1,
            blocks=[
                TextBlock(1, 1, "KAYAK mobile", "paragraph"),
                TextBlock(2, 1, "KAYAK needed a faster app.", "paragraph"),
                TextBlock(3, 1, "Edly Proposal for XYZ Ltd", "paragraph"),
            ],
        )
    ]
    deck_id = _seed_study(
        monkeypatch,
        tmp_path,
        slides=slides,
        section="General",
        title="KAYAK mobile",
        client_name="KAYAK",
        background="KAYAK needed a faster app.",
        raw_text="KAYAK needed a faster app.\nEdly Proposal for XYZ Ltd",
        slide_numbers=[1],
    )
    monkeypatch.setattr(audit_checks, "read_pptx_blocks", lambda path, deck_id=None: slides)
    monkeypatch.setattr(audit_checks, "embed_texts", _fake_embed)
    report = run_audit(deck_id)
    assert any(
        check.status == "incorrect" and "banner" in check.message.lower()
        for check in report.attention
    )


def test_invented_field_line(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    deck_id, slides = _seed_clean(
        monkeypatch,
        tmp_path,
        background="This sentence is not on any slide.",
        raw_text="KAYAK needed a faster app.",
    )
    monkeypatch.setattr(audit_checks, "read_pptx_blocks", lambda path, deck_id=None: slides)
    monkeypatch.setattr(audit_checks, "embed_texts", _fake_embed)
    report = run_audit(deck_id)
    assert any(
        check.status == "incorrect" and "not a Docling block" in check.message
        for check in report.attention
    )


def test_field_overlap_is_review_not_hard_issue(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    slides = [
        SlideBlocks(
            number=1,
            blocks=[
                TextBlock(1, 1, "Toastmasters LMS", "paragraph"),
                TextBlock(2, 1, "They built a tiered learning path on Open edX.", "paragraph"),
            ],
        )
    ]
    shared = "They built a tiered learning path on Open edX."
    deck_id = _seed_study(
        monkeypatch,
        tmp_path,
        slides=slides,
        section="General",
        title="Toastmasters LMS",
        client_name="Toastmasters",
        approach=shared,
        solution=shared,
        raw_text=shared,
        slide_numbers=[1],
    )
    monkeypatch.setattr(audit_checks, "read_pptx_blocks", lambda path, deck_id=None: slides)
    monkeypatch.setattr(audit_checks, "embed_texts", _fake_embed)
    report = run_audit(deck_id)
    study = report.studies[0]
    assert study.fully_matched
    assert study.issue_count == 0
    assert study.review_count == 1
    assert any(
        check.status == "review"
        and "approach" in check.message
        and "solution" in check.message
        for check in study.checks
    )
    text = format_report(report)
    assert "Field overlaps (review)" in text
    assert "Stores slide 1 text in approach, solution" in text or (
        "approach, solution" in text and "Review:" in text
    )
    assert "Studies with issues" in text
    # Overlaps must not appear under hard study issues.
    studies_section = text.split("## Studies with issues", 1)[1].split(
        "## Text in the file stored by no study", 1
    )[0]
    assert "approach, solution" not in studies_section


def test_body_block_missing_from_fields(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    slides = [
        SlideBlocks(
            number=1,
            blocks=[
                TextBlock(1, 1, "KAYAK mobile", "paragraph"),
                TextBlock(2, 1, "The Challenge", "paragraph"),
                TextBlock(3, 1, "KAYAK needed a faster app.", "paragraph"),
                TextBlock(4, 1, "and the team shipped the iOS client.", "paragraph"),
                TextBlock(5, 1, "Next Study Title", "paragraph"),
                TextBlock(6, 1, "Other body.", "paragraph"),
            ],
        )
    ]
    deck_id = _seed_study(
        monkeypatch,
        tmp_path,
        slides=slides,
        section="General",
        title="KAYAK mobile",
        client_name="KAYAK",
        challenge="KAYAK needed a faster app.",
        raw_text="KAYAK needed a faster app.\nand the team shipped the iOS client.",
        slide_numbers=[1],
        extra_studies=[
            {
                "section": "General",
                "title": "Next Study Title",
                "client_name": "Other",
                "background": "Other body.",
                "raw_text": "Other body.",
                "slide_numbers": [1],
            }
        ],
    )
    monkeypatch.setattr(audit_checks, "read_pptx_blocks", lambda path, deck_id=None: slides)
    monkeypatch.setattr(audit_checks, "embed_texts", _fake_embed)
    report = run_audit(deck_id)
    assert any(
        check.status == "missing"
        and "left out a body block" in check.message
        and "iOS client" in check.message
        for check in report.attention
    )


def test_same_title_in_two_sections_uses_each_section_span(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    slides = [
        SlideBlocks(number=1, blocks=[TextBlock(1, 1, "Mobile App Development", "paragraph")]),
        SlideBlocks(
            number=2,
            blocks=[
                TextBlock(
                    2,
                    2,
                    "Building a Scalable LMS for Tiered Learning at Toastmasters",
                    "paragraph",
                ),
                TextBlock(3, 2, "Mobile Toastmasters needed an LMS.", "paragraph"),
            ],
        ),
        SlideBlocks(number=3, blocks=[TextBlock(4, 3, "Edly Case Studies", "paragraph")]),
        SlideBlocks(
            number=4,
            blocks=[
                TextBlock(
                    5,
                    4,
                    "Building a Scalable LMS for Tiered Learning at Toastmasters",
                    "paragraph",
                ),
                TextBlock(6, 4, "Edly Toastmasters needed a tiered LMS.", "paragraph"),
                TextBlock(7, 4, "They shipped on Open edX.", "paragraph"),
            ],
        ),
    ]
    toastmasters_title = "Building a Scalable LMS for Tiered Learning at Toastmasters"
    deck_id = _seed_study(
        monkeypatch,
        tmp_path,
        slides=slides,
        section="Edly Case Studies",
        title=toastmasters_title,
        client_name="Toastmasters",
        background="Edly Toastmasters needed a tiered LMS.",
        challenge="They shipped on Open edX.",
        raw_text="Edly Toastmasters needed a tiered LMS.\nThey shipped on Open edX.",
        slide_numbers=[4],
        extra_studies=[
            {
                "section": "Mobile App Development",
                "title": toastmasters_title,
                "client_name": "Toastmasters",
                "background": "Mobile Toastmasters needed an LMS.",
                "raw_text": "Mobile Toastmasters needed an LMS.",
                "slide_numbers": [2],
            }
        ],
    )
    monkeypatch.setattr(audit_checks, "read_pptx_blocks", lambda path, deck_id=None: slides)
    monkeypatch.setattr(audit_checks, "embed_texts", _fake_embed)
    report = run_audit(deck_id)
    edly = next(study for study in report.studies if study.section == "Edly Case Studies")
    mobile = next(
        study for study in report.studies if study.section == "Mobile App Development"
    )
    assert edly.fully_matched
    assert mobile.fully_matched
    assert edly.slide_numbers == [4]
    assert mobile.slide_numbers == [2]
    assert not any(
        "slide_numbers are [4]" in check.message and "expected [2]" in check.message
        for check in edly.checks
    )


def test_banner_only_slide_not_in_expected_slide_numbers(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    slides = [
        SlideBlocks(
            number=38,
            blocks=[
                TextBlock(
                    1,
                    38,
                    "Analytics and Device Management for Teach the World",
                    "paragraph",
                ),
                TextBlock(2, 38, "Teach the World needed device analytics.", "paragraph"),
            ],
        ),
        SlideBlocks(
            number=39,
            blocks=[
                TextBlock(
                    3,
                    39,
                    "Arbisoft Case Studies for Non-Profit Organizations",
                    "paragraph",
                ),
            ],
        ),
        SlideBlocks(
            number=40,
            blocks=[
                TextBlock(4, 40, "Next Study After Banner", "paragraph"),
                TextBlock(5, 40, "Next body.", "paragraph"),
            ],
        ),
    ]
    deck_id = _seed_study(
        monkeypatch,
        tmp_path,
        slides=slides,
        section="General",
        title="Analytics and Device Management for Teach the World",
        client_name="Teach the World",
        background="Teach the World needed device analytics.",
        raw_text="Teach the World needed device analytics.",
        slide_numbers=[38, 39],
        extra_studies=[
            {
                "section": "General",
                "title": "Next Study After Banner",
                "client_name": "Next",
                "background": "Next body.",
                "raw_text": "Next body.",
                "slide_numbers": [40],
            }
        ],
    )
    monkeypatch.setattr(audit_checks, "read_pptx_blocks", lambda path, deck_id=None: slides)
    monkeypatch.setattr(audit_checks, "embed_texts", _fake_embed)
    report = run_audit(deck_id)
    assert any(
        check.status == "incorrect"
        and "slide_numbers are [38, 39]" in check.message
        and "expected [38]" in check.message
        for check in report.attention
    )


def test_section_banner_and_heading_not_missing_from_fields(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    slides = [
        SlideBlocks(
            number=1,
            blocks=[
                TextBlock(1, 1, "Rizq digital presence", "paragraph"),
                TextBlock(2, 1, "Rizq needed donation tracking.", "paragraph"),
                TextBlock(3, 1, "The Technology Stack", "paragraph"),
                TextBlock(4, 1, "Python and Django", "paragraph"),
                TextBlock(
                    5,
                    1,
                    "Arbisoft Case Studies for Non-Profit Organizations",
                    "paragraph",
                ),
                TextBlock(6, 1, "Mobile App Development", "paragraph"),
                TextBlock(7, 1, "Next mobile study", "paragraph"),
                TextBlock(8, 1, "Next body text.", "paragraph"),
            ],
        )
    ]
    deck_id = _seed_study(
        monkeypatch,
        tmp_path,
        slides=slides,
        section="General",
        title="Rizq digital presence",
        client_name="Rizq",
        background="Rizq needed donation tracking.",
        tech_stack="Python and Django",
        raw_text="Rizq needed donation tracking.\nPython and Django",
        slide_numbers=[1],
        extra_studies=[
            {
                "section": "Mobile App Development",
                "title": "Next mobile study",
                "client_name": "Next",
                "background": "Next body text.",
                "raw_text": "Next body text.",
                "slide_numbers": [1],
            }
        ],
    )
    monkeypatch.setattr(audit_checks, "read_pptx_blocks", lambda path, deck_id=None: slides)
    monkeypatch.setattr(audit_checks, "embed_texts", _fake_embed)
    report = run_audit(deck_id)
    rizq = next(study for study in report.studies if study.title == "Rizq digital presence")
    assert not any(
        check.status == "missing" and "left out" in check.message for check in rizq.checks
    )


def test_missing_chunk(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    deck_id, slides = _seed_clean(monkeypatch, tmp_path, drop_chunks=True)
    monkeypatch.setattr(audit_checks, "read_pptx_blocks", lambda path, deck_id=None: slides)
    monkeypatch.setattr(audit_checks, "embed_texts", _fake_embed)
    report = run_audit(deck_id)
    assert any(
        check.status == "incorrect" and "chunks stored" in check.message
        for check in report.attention
    )


def test_picture_hash_mismatch(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    image = ImageDraft(data=b"png-bytes-one", ext="png")
    slides = [
        SlideBlocks(
            number=1,
            blocks=[
                TextBlock(1, 1, "KAYAK mobile", "paragraph"),
                TextBlock(2, 1, "KAYAK needed a faster app.", "paragraph"),
            ],
            images=[image],
        )
    ]
    deck_id = _seed_study(
        monkeypatch,
        tmp_path,
        slides=slides,
        section="General",
        title="KAYAK mobile",
        client_name="KAYAK",
        background="KAYAK needed a faster app.",
        raw_text="KAYAK needed a faster app.",
        slide_numbers=[1],
        image_sha256="0" * 64,
        write_image=False,
    )
    monkeypatch.setattr(audit_checks, "read_pptx_blocks", lambda path, deck_id=None: slides)
    monkeypatch.setattr(audit_checks, "embed_texts", _fake_embed)
    report = run_audit(deck_id)
    assert any(
        check.status in {"missing", "incorrect"} and "image" in check.message.lower()
        for check in report.attention
    )


def test_unclaimed_body_block(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    slides = [
        SlideBlocks(
            number=1,
            blocks=[
                TextBlock(1, 1, "Enabling Brands With", "paragraph"),
                TextBlock(2, 1, "KAYAK mobile", "paragraph"),
                TextBlock(3, 1, "KAYAK needed a faster app.", "paragraph"),
            ],
        )
    ]
    deck_id = _seed_study(
        monkeypatch,
        tmp_path,
        slides=slides,
        section="General",
        title="KAYAK mobile",
        client_name="KAYAK",
        background="KAYAK needed a faster app.",
        raw_text="KAYAK needed a faster app.",
        slide_numbers=[1],
    )
    monkeypatch.setattr(audit_checks, "read_pptx_blocks", lambda path, deck_id=None: slides)
    monkeypatch.setattr(audit_checks, "embed_texts", _fake_embed)
    report = run_audit(deck_id)
    assert report.unclaimed_count >= 1
    assert any("Enabling Brands With" in check.message for check in report.unclaimed)


def _fake_embed(texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for text in texts:
        digest = hashlib.sha256(text.encode()).digest()
        values = [((digest[i % len(digest)] / 255.0) - 0.5) for i in range(384)]
        vectors.append(values)
    return vectors


def _seed_clean(
    monkeypatch,  # noqa: ANN001
    tmp_path,  # noqa: ANN001
    *,
    client_name: str | None = "KAYAK",
    background: str = "KAYAK needed a faster app.",
    raw_text: str = "KAYAK needed a faster app.",
    drop_chunks: bool = False,
) -> tuple[UUID, list[SlideBlocks]]:
    slides = [
        SlideBlocks(
            number=1,
            blocks=[TextBlock(1, 1, "Mobile App Development", "paragraph")],
        ),
        SlideBlocks(
            number=2,
            blocks=[
                TextBlock(2, 2, "KAYAK mobile", "paragraph"),
                TextBlock(3, 2, "KAYAK needed a faster app.", "paragraph"),
            ],
        ),
    ]
    deck_id = _seed_study(
        monkeypatch,
        tmp_path,
        slides=slides,
        section="Mobile App Development",
        title="KAYAK mobile",
        client_name=client_name,
        background=background,
        raw_text=raw_text,
        slide_numbers=[2],
        drop_chunks=drop_chunks,
    )
    return deck_id, slides


def _seed_study(  # noqa: ANN001
    monkeypatch,
    tmp_path,
    *,
    slides: list[SlideBlocks],
    section: str,
    title: str,
    client_name: str | None,
    background: str = "",
    challenge: str = "",
    approach: str = "",
    solution: str = "",
    outcome: str = "",
    tech_stack: str = "",
    raw_text: str,
    slide_numbers: list[int],
    drop_chunks: bool = False,
    image_sha256: str | None = None,
    write_image: bool = True,
    extra_studies: list[dict] | None = None,
) -> UUID:
    path = tmp_path / "deck.pptx"
    _write_pptx(path)
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    with deck_service.get_sync_session() as session:
        user = session.exec(select(User).limit(1)).first()
        if user is None:
            user = User(
                username=f"auditor-{uuid4().hex[:12]}@example.com",
                password_hash="x",
                name="Auditor",
            )
            session.add(user)
            session.flush()
        deck = DeckImport(
            uploaded_by=user.id,
            file_name="deck.pptx",
            sha256=digest,
            status="completed",
        )
        session.add(deck)
        session.flush()
        deck_service.save_deck_file(deck.id, data)
        studies = [
            {
                "section": section,
                "title": title,
                "client_name": client_name,
                "background": background,
                "challenge": challenge,
                "approach": approach,
                "solution": solution,
                "outcome": outcome,
                "tech_stack": tech_stack,
                "raw_text": raw_text,
                "slide_numbers": slide_numbers,
            }
        ]
        if extra_studies:
            studies.extend(extra_studies)
        for spec in studies:
            study = Study(
                section=spec["section"],
                title=spec["title"],
                client_name=spec.get("client_name"),
                status="active",
            )
            session.add(study)
            session.flush()
            version_raw = spec["raw_text"]
            summary = (
                f"{spec['section']}\n{spec['title']}\n{version_raw[: deck_service.SUMMARY_CHARS]}"
            )
            field_parts = [
                spec.get(name) or ""
                for name in (
                    "background",
                    "challenge",
                    "approach",
                    "solution",
                    "outcome",
                    "tech_stack",
                )
                if spec.get(name)
            ]
            chunk_texts = field_parts or ([version_raw] if version_raw else ["Untitled study"])
            vectors = _fake_embed([summary, *chunk_texts])
            version = StudyVersion(
                study_id=study.id,
                deck_import_id=deck.id,
                slide_numbers=list(spec["slide_numbers"]),
                content_hash=hashlib.sha256(version_raw.encode()).hexdigest(),
                background=spec.get("background") or "",
                challenge=spec.get("challenge") or "",
                approach=spec.get("approach") or "",
                solution=spec.get("solution") or "",
                outcome=spec.get("outcome") or "",
                tech_stack=spec.get("tech_stack") or "",
                raw_text=version_raw,
                summary_embedding=vectors[0],
                is_current=True,
            )
            session.add(version)
            session.flush()
            if not drop_chunks:
                for position, text in enumerate(chunk_texts, start=1):
                    session.add(
                        StudyChunk(
                            study_version_id=version.id,
                            position=position,
                            text=text,
                            embedding=vectors[position],
                        )
                    )
            if image_sha256 is not None and spec["title"] == title:
                key = f"study-images/{image_sha256}.png"
                if write_image:
                    target = deck_service.storage_root() / key
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"wrong-bytes")
                session.add(
                    StudyImage(
                        study_version_id=version.id,
                        role="logo" if client_name else "other",
                        storage_key=key,
                        sha256=image_sha256,
                    )
                )
        session.commit()
        return deck.id


def _write_pptx(path: Path) -> None:
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    slide = presentation.slides.add_slide(blank)
    box = slide.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(8), Inches(1))
    box.text_frame.text = "placeholder"
    presentation.save(path)
