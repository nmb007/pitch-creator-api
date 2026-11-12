"""Turn the master PowerPoint into study drafts.

Section-divider slides set the section. Format-only slides are skipped. Text is read
from top to bottom, so a title that sits above the body is kept even when the file
stores the body shape first. A slide that continues a sentence is joined to the open
study. A slide that starts another client is split. Footer lines and the small running
header are removed before the text is hashed or embedded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

FIELD_NAMES = ("background", "challenge", "approach", "solution", "outcome", "tech_stack")

HEADINGS = {
    "the challenge": "challenge",
    "challenge": "challenge",
    "challenges": "challenge",
    "challenges faced": "challenge",
    "the approach": "approach",
    "approach": "approach",
    "the outcome": "outcome",
    "outcome": "outcome",
    "impact": "outcome",
    "background": "background",
    "the background": "background",
    "solution": "solution",
    "solutions": "solution",
    "the solution": "solution",
    "technology stack": "tech_stack",
    "the technology stack": "tech_stack",
    "tech stack": "tech_stack",
    "technology": "tech_stack",
}

FORMAT_FIRST_LINES = {"general format", "format"}

DATE_LINE = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2},\s+\d{4}$",
    re.IGNORECASE,
)

CONTINUATION_WORDS = {
    "and",
    "with",
    "which",
    "this",
    "that",
    "these",
    "those",
    "additionally",
    "also",
    "furthermore",
    "moreover",
    "however",
    "meanwhile",
    "finally",
    "similarly",
    "consequently",
    "therefore",
}
DANGLING_TITLE_WORDS = {
    "a",
    "an",
    "and",
    "by",
    "for",
    "from",
    "in",
    "of",
    "on",
    "the",
    "to",
    "with",
}
NEW_STUDY_FIELDS = {"challenge", "background"}
LATER_FIELDS = {"approach", "outcome", "solution", "tech_stack"}
RUNNING_HEADER_PT = 11


@dataclass
class ImageDraft:
    data: bytes
    ext: str


@dataclass
class StudyDraft:
    section: str
    title: str
    client_name: str | None
    raw_text: str
    background: str
    challenge: str
    approach: str
    solution: str
    outcome: str
    tech_stack: str
    slide_numbers: list[int] = field(default_factory=list)
    images: list[ImageDraft] = field(default_factory=list)


def parse_pptx(path: Path) -> list[StudyDraft]:
    presentation = Presentation(str(path))
    section = "General"
    force_new = False
    current: _OpenStudy | None = None
    drafts: list[StudyDraft] = []

    for number, slide in enumerate(presentation.slides, start=1):
        lines = _slide_lines(slide)
        images = _slide_images(slide)
        if not lines or _is_format_slide(lines):
            continue
        if _is_section_divider(lines):
            section = lines[0].strip()
            force_new = True
            continue
        blocks = _split_blocks(lines)
        for index, block in enumerate(blocks):
            block_images = images if index == 0 else []
            if current is not None and not force_new and not _starts_new_study(block, True):
                current.add(block, number, block_images)
                continue
            if current is not None:
                drafts.append(current.finish())
            current = _OpenStudy(section, block, number, block_images)
            force_new = False
    if current is not None:
        drafts.append(current.finish())
    return _merge_same_key(drafts)


def is_footer_line(line: str) -> bool:
    compact = " ".join(line.split())
    lowered = compact.lower()
    if lowered in {"www.arbisoft.com", "confidential", "arbisoft case studies"}:
        return True
    if lowered.startswith("arbisoft proposal"):
        return True
    if "valid till" in lowered or lowered.startswith("proposal valid"):
        return True
    return DATE_LINE.match(compact) is not None


class _OpenStudy:
    def __init__(
        self,
        section: str,
        lines: list[str],
        slide_number: int,
        images: list[ImageDraft],
    ) -> None:
        self.section = section
        self.lines = list(lines)
        self.slide_numbers = [slide_number]
        self.images = list(images)

    def add(self, lines: list[str], slide_number: int, images: list[ImageDraft]) -> None:
        self.lines.extend(lines)
        if slide_number not in self.slide_numbers:
            self.slide_numbers.append(slide_number)
        self.images.extend(images)

    def finish(self) -> StudyDraft:
        title, title_lines = _title_from(self.lines)
        fields = _map_fields(self.lines, title, title_lines)
        body_lines = [line for line in self.lines if heading_key(line) is None]
        raw_text = "\n".join(body_lines).strip()
        return StudyDraft(
            section=self.section,
            title=title,
            client_name=None,
            raw_text=raw_text,
            slide_numbers=list(self.slide_numbers),
            images=list(self.images),
            **fields,
        )


def heading_key(line: str) -> str | None:
    return HEADINGS.get(line.strip().lower().rstrip(":"))


def _slide_lines(slide) -> list[str]:  # noqa: ANN001
    """Read text from top to bottom. The file often stores the body shape first."""
    frames: list[tuple[int, int, list[str]]] = []

    def walk(shapes, dx: int, dy: int) -> None:  # noqa: ANN001
        for shape in shapes:
            left = int(shape.left or 0) + dx
            top = int(shape.top or 0) + dy
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                walk(shape.shapes, left, top)
                continue
            if not getattr(shape, "has_text_frame", False) or _is_running_header(shape):
                continue
            lines: list[str] = []
            for paragraph in shape.text_frame.paragraphs:
                text = "".join(run.text for run in paragraph.runs).strip()
                if text and not is_footer_line(text):
                    lines.append(text)
            if lines:
                frames.append((top, left, lines))

    walk(slide.shapes, 0, 0)
    frames.sort()
    return [line for _, _, lines in frames for line in lines]


def _is_running_header(shape) -> bool:  # noqa: ANN001
    """The repeated 10pt banner at the top of a slide is not the study title."""
    sizes = [
        run.font.size.pt
        for paragraph in shape.text_frame.paragraphs
        for run in paragraph.runs
        if run.text.strip() and run.font.size is not None
    ]
    return bool(sizes) and max(sizes) <= RUNNING_HEADER_PT


def _slide_images(slide) -> list[ImageDraft]:  # noqa: ANN001
    images: list[ImageDraft] = []
    for shape in _iter_shapes(slide.shapes):
        if shape.shape_type != MSO_SHAPE_TYPE.PICTURE:
            continue
        image = shape.image
        images.append(ImageDraft(data=image.blob, ext=image.ext or "png"))
    return images


def _iter_shapes(shapes):  # noqa: ANN001, ANN202
    for shape in shapes:
        yield shape
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _iter_shapes(shape.shapes)


def _is_format_slide(lines: list[str]) -> bool:
    first = lines[0].strip().lower().rstrip("?")
    if first in FORMAT_FIRST_LINES:
        return True
    joined = " / ".join(line.strip().lower().rstrip("?") for line in lines)
    known = {
        "what the company is / what we did",
        "the challenge / the approach / the outcome / technology stack",
        "background / challenges / solutions / technology stack",
    }
    return joined in known or first in known


def _is_section_divider(lines: list[str]) -> bool:
    if len(lines) != 1:
        return False
    text = lines[0].strip()
    if len(text) > 140 or text.endswith("."):
        return False
    lowered = text.lower()
    if lowered.startswith("case study") or lowered.startswith("the "):
        return False
    return heading_key(text) is None


def _split_blocks(lines: list[str]) -> list[list[str]]:
    """Split a slide only when a second study starts after the first study's text.

    "Background" followed by "Challenges" is one study. A second "The Challenge",
    a "Case Study" line, or a new title after a Tech Stack line starts another client.
    A sentence that wraps under Challenge stays with the open study.
    """
    blocks: list[list[str]] = []
    current: list[str] = []
    seen_body = False
    seen_opening: set[str] = set()
    for index, line in enumerate(lines):
        key = heading_key(line)
        previous = current[-1] if current else ""
        previous_last = previous.split()[-1].lower().strip(".,:;&") if previous.split() else ""
        wrapped_title = previous_last in DANGLING_TITLE_WORDS and _looks_like_title(line)
        repeated_opening = key in NEW_STUDY_FIELDS and key in seen_opening
        case_study_line = seen_body and line.lower().startswith("case study")
        title_break = (
            _looks_like_title(line)
            and index < len(lines) - 1
            and not wrapped_title
            and previous.lower().startswith("tech stack")
        )
        if current and seen_body and (repeated_opening or case_study_line or title_break):
            blocks.append(current)
            current = [line]
            seen_body = False
            seen_opening = {key} if key in NEW_STUDY_FIELDS else set()
            continue
        current.append(line)
        if key in NEW_STUDY_FIELDS:
            seen_opening.add(key)
        elif key is None and not line.lower().startswith("tech stack"):
            seen_body = True
    if current:
        blocks.append(current)
    return blocks


def _looks_like_title(line: str) -> bool:
    if heading_key(line) is not None or line.lower().startswith("tech stack"):
        return False
    if _is_continuation(line) or line.endswith((".", ":")):
        return False
    return len(line) <= 110


def _starts_new_study(lines: list[str], study_open: bool) -> bool:
    first = lines[0]
    key = heading_key(first)
    if key in NEW_STUDY_FIELDS or first.lower().startswith("case study"):
        return True
    if study_open and (_is_continuation(first) or key in LATER_FIELDS):
        return False
    if len(first) <= 110 and not first.endswith((".", ":")) and key is None:
        return True
    return not study_open


def _is_continuation(line: str) -> bool:
    stripped = line.strip()
    if stripped[:1].islower():
        return True
    first_word = stripped.split(maxsplit=1)[0].lower().strip(".,:;")
    return first_word in CONTINUATION_WORDS


def _title_from(lines: list[str]) -> tuple[str, set[int]]:
    """Use the case-study title, not the first sentence under Challenge.

    A wrapped title such as "Enabling Brands With" / "Real-Time Store-Level Insights"
    is joined. The returned indexes are left out of the field text.
    """
    for index, line in enumerate(lines):
        if heading_key(line) is not None or _is_continuation(line):
            continue
        if _looks_like_title(line):
            chosen = [index]
            words = line.split()
            last = words[-1].lower().strip(".,:;&") if words else ""
            nxt = index + 1
            if last in DANGLING_TITLE_WORDS and nxt < len(lines) and _looks_like_title(lines[nxt]):
                chosen.append(nxt)
            return " ".join(lines[i] for i in chosen)[:500], set(chosen)
        sentence = re.split(r"[.]", line, maxsplit=1)[0].strip()
        return (sentence or line)[:140], set()
    return "Untitled study", set()


def _map_fields(lines: list[str], title: str, title_lines: set[int]) -> dict[str, str]:
    buckets: dict[str, list[str]] = {name: [] for name in FIELD_NAMES}
    current = "background"
    for index, line in enumerate(lines):
        if index in title_lines or line == title:
            continue
        key = heading_key(line)
        if key is not None:
            current = key
            continue
        buckets[current].append(line)
    return {name: "\n".join(parts).strip() for name, parts in buckets.items()}


def _merge_same_key(drafts: list[StudyDraft]) -> list[StudyDraft]:
    merged: dict[str, StudyDraft] = {}
    order: list[str] = []
    for draft in drafts:
        key = f"{draft.section}\n{draft.title}"
        existing = merged.get(key)
        if existing is None:
            merged[key] = draft
            order.append(key)
            continue
        existing.raw_text = f"{existing.raw_text}\n{draft.raw_text}".strip()
        for name in FIELD_NAMES:
            extra = getattr(draft, name)
            if not extra:
                continue
            current = getattr(existing, name)
            setattr(existing, name, f"{current}\n{extra}".strip() if current else extra)
        for number in draft.slide_numbers:
            if number not in existing.slide_numbers:
                existing.slide_numbers.append(number)
        existing.images.extend(draft.images)
        existing.client_name = existing.client_name or draft.client_name
    return [merged[key] for key in order]
