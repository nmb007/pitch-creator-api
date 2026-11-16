"""Compare one deck's PPTX blocks with the ingest tables."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlmodel import Session, select

from src.decks.audit.labels import (
    is_audit_banner,
    is_field_heading,
    is_forbidden_title,
)
from src.decks.audit.types import AuditReport, CheckResult, StudyReport
from src.decks.blocks import SlideBlocks, TextBlock, read_pptx_blocks
from src.decks.drafts import FIELD_NAMES, ImageDraft
from src.decks.models import DeckImport, Study, StudyChunk, StudyImage, StudyVersion
from src.decks.service import SUMMARY_CHARS, deck_path, storage_root
from src.embeddings import embed_texts

COSINE_THRESHOLD = 0.999


@dataclass
class StoredStudy:
    study: Study
    version: StudyVersion
    chunks: list[StudyChunk]
    images: list[StudyImage]
    kept_from_earlier: bool


def audit_deck(session: Session, deck_id: UUID) -> AuditReport:
    deck = session.get(DeckImport, deck_id)
    if deck is None:
        raise LookupError(f"Deck import {deck_id} was not found")

    report = AuditReport(
        deck_id=deck.id,
        file_name=deck.file_name,
        deck_status=deck.status,
        file_hash_ok=None,
    )
    path = deck_path(deck.id)
    if not path.exists():
        report.deck_checks.append(
            CheckResult("missing", f"deck file is missing at {path}")
        )
        report.attention.extend(report.deck_checks)
        return report

    file_bytes = path.read_bytes()
    digest = hashlib.sha256(file_bytes).hexdigest()
    if digest == deck.sha256:
        report.file_hash_ok = True
        report.deck_checks.append(CheckResult("match", "file hash matches deck_imports.sha256"))
    else:
        report.file_hash_ok = False
        report.deck_checks.append(
            CheckResult(
                "incorrect",
                f"file hash is {digest[:12]}…; deck_imports.sha256 is {deck.sha256[:12]}…",
            )
        )

    slides = read_pptx_blocks(path, deck_id=None)
    blocks = [block for slide in slides for block in slide.blocks]
    slides_by_number = {slide.number: slide for slide in slides}

    stored = _load_stored_studies(session, deck)
    divider_marks = _divider_marks(stored, blocks)
    claimed_numbers: set[int] = set()
    is_latest = _is_latest_completed(session, deck)

    for item in stored:
        study_report = _audit_study(
            item,
            blocks=blocks,
            slides_by_number=slides_by_number,
            divider_marks=divider_marks,
            stored_titles=_stored_title_starts(stored, blocks),
            claimed_numbers=claimed_numbers,
        )
        report.studies.append(study_report)

    for block in blocks:
        if is_audit_banner(block.text):
            report.unclaimed.append(
                CheckResult(
                    "match",
                    f"slide {block.slide}, block {block.number}: banner correctly absent",
                )
            )
            continue
        if block.number in claimed_numbers:
            continue
        if _block_accounted_for(block, report.studies, stored):
            claimed_numbers.add(block.number)
            continue
        report.unclaimed.append(
            CheckResult(
                "missing",
                f'slide {block.slide}, block {block.number}: "{_short(block.text)}" '
                "is not in any study",
            )
        )

    if is_latest:
        report.deck_checks.extend(_extra_active_studies(session, stored, blocks))
    else:
        later = _later_completed(session, deck)
        if later is not None:
            report.deck_checks.append(
                CheckResult(
                    "match",
                    f"skipping active/hidden status; later completed deck {later.id} owns that",
                )
            )

    report.attention = [
        check
        for check in report.all_checks()
        if check.status != "match"
    ]
    return report


def _load_stored_studies(session: Session, deck: DeckImport) -> list[StoredStudy]:
    versions = list(
        session.exec(
            select(StudyVersion).where(StudyVersion.deck_import_id == deck.id)
        ).all()
    )
    by_study: dict[UUID, StoredStudy] = {}
    for version in versions:
        study = session.get(Study, version.study_id)
        if study is None:
            continue
        by_study[study.id] = StoredStudy(
            study=study,
            version=version,
            chunks=_chunks_for(session, version.id),
            images=_images_for(session, version.id),
            kept_from_earlier=False,
        )

    # Hash-skipped studies: current version belongs to an earlier deck, text still in file.
    current_versions = list(
        session.exec(select(StudyVersion).where(StudyVersion.is_current.is_(True))).all()
    )
    for version in current_versions:
        if version.study_id in by_study:
            continue
        if version.deck_import_id == deck.id:
            continue
        study = session.get(Study, version.study_id)
        if study is None:
            continue
        by_study[study.id] = StoredStudy(
            study=study,
            version=version,
            chunks=_chunks_for(session, version.id),
            images=_images_for(session, version.id),
            kept_from_earlier=True,
        )
    return sorted(by_study.values(), key=lambda item: (item.study.section, item.study.title))


def _chunks_for(session: Session, version_id: UUID) -> list[StudyChunk]:
    rows = list(
        session.exec(
            select(StudyChunk)
            .where(StudyChunk.study_version_id == version_id)
            .order_by(StudyChunk.position)
        ).all()
    )
    return rows


def _images_for(session: Session, version_id: UUID) -> list[StudyImage]:
    return list(
        session.exec(
            select(StudyImage).where(StudyImage.study_version_id == version_id)
        ).all()
    )


def _divider_marks(stored: Sequence[StoredStudy], blocks: list[TextBlock]) -> list[tuple[int, str]]:
    names = sorted(
        {
            item.study.section
            for item in stored
            if item.study.section and item.study.section != "General"
        }
    )
    marks: list[tuple[int, str]] = []
    for name in names:
        for block in blocks:
            if block.text.strip() == name:
                marks.append((block.number, name))
                break
    marks.sort(key=lambda item: item[0])
    return marks


def _section_for(block_number: int, marks: list[tuple[int, str]]) -> str:
    current = "General"
    for mark_block, name in marks:
        if mark_block > block_number:
            break
        current = name
    return current


def _find_title_blocks(title: str, blocks: list[TextBlock]) -> list[TextBlock] | None:
    compact = " ".join(title.split())
    for block in blocks:
        if block.text.strip() == title or " ".join(block.text.split()) == compact:
            return [block]
    for index, first in enumerate(blocks[:-1]):
        second = blocks[index + 1]
        joined = f"{first.text.strip()} {second.text.strip()}"
        if joined == title or " ".join(joined.split()) == compact:
            return [first, second]
    return None


def _stored_title_starts(
    stored: Sequence[StoredStudy],
    blocks: list[TextBlock],
) -> list[tuple[int, str]]:
    starts: list[tuple[int, str]] = []
    for item in stored:
        found = _find_title_blocks(item.study.title, blocks)
        if found:
            starts.append((found[0].number, item.study.title))
    starts.sort(key=lambda item: item[0])
    return starts


def _span_blocks(
    title_blocks: list[TextBlock],
    title: str,
    title_starts: list[tuple[int, str]],
    blocks: list[TextBlock],
    divider_marks: list[tuple[int, str]] | None = None,
) -> list[TextBlock]:
    del title
    start = title_blocks[0].number
    last_title = max(block.number for block in title_blocks)
    next_start: int | None = None
    for number, _other_title in title_starts:
        if number <= start:
            continue
        next_start = number
        break
    # A later section divider ends this study before the next title.
    for mark_block, _name in divider_marks or []:
        if mark_block <= last_title:
            continue
        if next_start is None or mark_block < next_start:
            next_start = mark_block
        break
    span: list[TextBlock] = []
    for block in blocks:
        if block.number <= last_title:
            continue
        if next_start is not None and block.number >= next_start:
            break
        span.append(block)
    return span


def _audit_study(
    item: StoredStudy,
    *,
    blocks: list[TextBlock],
    slides_by_number: dict[int, SlideBlocks],
    divider_marks: list[tuple[int, str]],
    stored_titles: list[tuple[int, str]],
    claimed_numbers: set[int],
) -> StudyReport:
    study = item.study
    version = item.version
    key = f"{study.section}\n{study.title}"
    report = StudyReport(
        section=study.section,
        title=study.title,
        slide_numbers=list(version.slide_numbers or []),
        kept_from_earlier=item.kept_from_earlier,
    )
    if item.kept_from_earlier:
        report.checks.append(
            CheckResult(
                "match",
                "kept from an earlier import (no new study_versions row for this deck)",
                key,
            )
        )

    title_blocks = _find_title_blocks(study.title, blocks)
    if title_blocks is None:
        report.checks.append(
            CheckResult(
                "missing",
                f'title "{study.title}" is not a block (or adjacent pair) in this file',
                key,
            )
        )
    elif is_forbidden_title(study.title):
        kind = "a field heading" if is_field_heading(study.title) else "a banner"
        report.checks.append(
            CheckResult(
                "incorrect",
                f'title "{study.title}" is {kind}, not a case study',
                key,
            )
        )
        for block in title_blocks:
            claimed_numbers.add(block.number)
    else:
        label = (
            f"title is block {title_blocks[0].number}"
            if len(title_blocks) == 1
            else f"title is blocks {title_blocks[0].number}–{title_blocks[1].number}"
        )
        report.checks.append(CheckResult("match", label, key))
        for block in title_blocks:
            claimed_numbers.add(block.number)

    if title_blocks is not None:
        expected = _section_for(title_blocks[0].number, divider_marks)
        if study.section == expected:
            report.checks.append(
                CheckResult(
                    "match",
                    f'section "{study.section}" matches the divider before the title',
                    key,
                )
            )
        else:
            report.checks.append(
                CheckResult(
                    "incorrect",
                    f'"{study.title}" is stored as {study.section}; '
                    f"the divider before its title is {expected}",
                    key,
                )
            )
        for block in blocks:
            if block.text.strip() == study.section and study.section != "General":
                claimed_numbers.add(block.number)

    if study.client_name is None or not str(study.client_name).strip():
        report.checks.append(
            CheckResult("missing", f'"{study.title}" has no client_name', key)
        )
    elif study.client_name.strip().lower() == "arbisoft":
        report.checks.append(
            CheckResult("incorrect", f'"{study.title}" client_name is Arbisoft (vendor)', key)
        )
    else:
        source = _study_source_text(version, title_blocks, stored_titles, blocks)
        if study.client_name.lower() in source.lower():
            report.checks.append(
                CheckResult(
                    "match",
                    f'client_name "{study.client_name}" appears in the study text',
                    key,
                )
            )
        else:
            report.checks.append(
                CheckResult(
                    "incorrect",
                    f'client_name "{study.client_name}" does not appear in the study slide text',
                    key,
                )
            )

    field_lines: dict[str, list[str]] = {}
    for name in FIELD_NAMES:
        text = getattr(version, name) or ""
        lines = [line for line in text.splitlines() if line.strip()]
        field_lines[name] = lines
        for line in lines:
            report.checks.append(_line_check(name, line, version, slides_by_number, key))

    raw_lines = [line for line in (version.raw_text or "").splitlines() if line.strip()]
    for line in raw_lines:
        report.checks.append(_line_check("raw_text", line, version, slides_by_number, key))

    divider_names = {name for _number, name in divider_marks}
    if title_blocks is not None:
        span = _span_blocks(
            title_blocks,
            study.title,
            stored_titles,
            blocks,
            divider_marks=divider_marks,
        )
        body = [
            block
            for block in span
            if block.text.strip()
            and not is_audit_banner(block.text)
            and not is_field_heading(block.text)
            and block.text.strip() not in divider_names
        ]
        for block in span:
            if is_audit_banner(block.text) or is_field_heading(block.text):
                claimed_numbers.add(block.number)
            if block.text.strip() in divider_names:
                claimed_numbers.add(block.number)
        all_field_lines = [
            (name, line) for name, lines in field_lines.items() for line in lines
        ]
        missing_body = 0
        duplicate_body = 0
        for block in body:
            claimed_numbers.add(block.number)
            owners = {
                name
                for name, line in all_field_lines
                if line == block.text.strip() or line == block.text
            }
            if not owners:
                missing_body += 1
                report.checks.append(
                    CheckResult(
                        "missing",
                        f'"{study.title}" left out a body block on slide {block.slide}: '
                        f'"{_short(block.text)}"',
                        key,
                    )
                )
            elif len(owners) > 1:
                duplicate_body += 1
                report.checks.append(
                    CheckResult(
                        "incorrect",
                        f'"{study.title}" stores slide {block.slide} text in '
                        f"{', '.join(sorted(owners))}",
                        key,
                    )
                )
        if body and missing_body == 0 and duplicate_body == 0:
            report.checks.append(
                CheckResult(
                    "match",
                    f"six fields cover {len(body)} body blocks in the study span",
                    key,
                )
            )

        expected_slides = sorted(
            {block.slide for block in title_blocks} | {block.slide for block in span}
        )
        stored_slides = sorted(version.slide_numbers or [])
        if stored_slides == expected_slides:
            report.checks.append(
                CheckResult("match", f"slide_numbers {stored_slides}", key)
            )
        else:
            report.checks.append(
                CheckResult(
                    "incorrect",
                    f"slide_numbers are {stored_slides}; expected {expected_slides}",
                    key,
                )
            )

    expected_hash = hashlib.sha256((version.raw_text or "").encode()).hexdigest()
    if version.content_hash == expected_hash:
        report.checks.append(CheckResult("match", "content_hash matches raw_text", key))
    else:
        report.checks.append(
            CheckResult(
                "incorrect",
                "content_hash does not match sha256(raw_text)",
                key,
            )
        )

    report.checks.extend(_chunk_checks(item, key))
    report.checks.extend(_embedding_checks(item, key))
    report.checks.extend(
        _image_checks(item, slides_by_number=slides_by_number, study_key=key)
    )
    return report


def _line_check(
    field: str,
    line: str,
    version: StudyVersion,
    slides_by_number: dict[int, SlideBlocks],
    key: str,
) -> CheckResult:
    if is_audit_banner(line):
        return CheckResult(
            "incorrect",
            f'"{_title_from_key(key)}" {field} contains banner "{_short(line)}"',
            key,
        )
    slide_numbers = list(version.slide_numbers or [])
    candidates: list[str] = []
    for number in slide_numbers:
        slide = slides_by_number.get(number)
        if slide is None:
            continue
        for block in slide.blocks:
            candidates.append(block.text.strip())
    if not candidates:
        for slide in slides_by_number.values():
            for block in slide.blocks:
                candidates.append(block.text.strip())
    stripped = line.strip()
    if stripped in candidates:
        return CheckResult("match", f'{field} line matches a Docling block', key)
    # Partial line of a longer block
    for candidate in candidates:
        if stripped and stripped != candidate and stripped in candidate:
            return CheckResult(
                "incorrect",
                f'{field} line "{_short(stripped)}" is only part of a longer block',
                key,
            )
    return CheckResult(
        "incorrect",
        f'{field} line "{_short(stripped)}" is not a Docling block on the study slides',
        key,
    )


def _chunk_checks(item: StoredStudy, key: str) -> list[CheckResult]:
    version = item.version
    expected = [
        getattr(version, name)
        for name in FIELD_NAMES
        if getattr(version, name)
    ]
    if not expected:
        expected = [version.raw_text] if version.raw_text else ["Untitled study"]
    results: list[CheckResult] = []
    chunks = sorted(item.chunks, key=lambda row: row.position)
    if [row.position for row in chunks] != list(range(1, len(chunks) + 1)):
        results.append(
            CheckResult(
                "incorrect",
                f"chunk positions are {[row.position for row in chunks]}; "
                f"expected {list(range(1, len(expected) + 1))}",
                key,
            )
        )
    if len(chunks) != len(expected):
        results.append(
            CheckResult(
                "incorrect",
                f"{len(chunks)} chunks stored; expected {len(expected)}",
                key,
            )
        )
        return results
    for chunk, text in zip(chunks, expected, strict=True):
        if chunk.text == text:
            results.append(
                CheckResult("match", f"chunk {chunk.position} matches field text", key)
            )
        else:
            results.append(
                CheckResult(
                    "incorrect",
                    f"chunk {chunk.position} text does not match the expected field",
                    key,
                )
            )
    return results


def _embedding_checks(item: StoredStudy, key: str) -> list[CheckResult]:
    study = item.study
    version = item.version
    summary = f"{study.section}\n{study.title}\n{(version.raw_text or '')[:SUMMARY_CHARS]}"
    chunk_texts = [chunk.text for chunk in sorted(item.chunks, key=lambda row: row.position)]
    vectors = embed_texts([summary, *chunk_texts])
    results: list[CheckResult] = []
    summary_vector = version.summary_embedding
    if _is_zero(summary_vector):
        results.append(CheckResult("incorrect", "summary_embedding is a zero vector", key))
    elif len(summary_vector) != 384:
        results.append(
            CheckResult(
                "incorrect",
                f"summary_embedding has {len(summary_vector)} dimensions; expected 384",
                key,
            )
        )
    elif _cosine(summary_vector, vectors[0]) >= COSINE_THRESHOLD:
        results.append(CheckResult("match", "summary_embedding matches a fresh embedding", key))
    else:
        results.append(
            CheckResult("incorrect", "summary_embedding does not match a fresh embedding", key)
        )
    for index, chunk in enumerate(sorted(item.chunks, key=lambda row: row.position)):
        expected = vectors[index + 1]
        if _is_zero(chunk.embedding):
            results.append(
                CheckResult("incorrect", f"chunk {chunk.position} embedding is a zero vector", key)
            )
        elif len(chunk.embedding) != 384:
            results.append(
                CheckResult(
                    "incorrect",
                    f"chunk {chunk.position} embedding has {len(chunk.embedding)} dimensions",
                    key,
                )
            )
        elif _cosine(chunk.embedding, expected) >= COSINE_THRESHOLD:
            results.append(
                CheckResult(
                    "match",
                    f"chunk {chunk.position} embedding matches a fresh embedding",
                    key,
                )
            )
        else:
            results.append(
                CheckResult(
                    "incorrect",
                    f"chunk {chunk.position} embedding does not match a fresh embedding",
                    key,
                )
            )
    return results


def _image_checks(
    item: StoredStudy,
    *,
    slides_by_number: dict[int, SlideBlocks],
    study_key: str,
) -> list[CheckResult]:
    version = item.version
    study = item.study
    expected: list[ImageDraft] = []
    for number in version.slide_numbers or []:
        slide = slides_by_number.get(number)
        if slide is not None:
            expected.extend(slide.images)
    results: list[CheckResult] = []
    stored = list(item.images)
    expected_digests = [hashlib.sha256(image.data).hexdigest() for image in expected]
    for index, _image in enumerate(expected):
        digest = expected_digests[index]
        role = "logo" if index == 0 and study.client_name else "other"
        match = next((row for row in stored if row.sha256 == digest), None)
        if match is None:
            results.append(
                CheckResult(
                    "missing",
                    f'"{study.title}" is missing image sha256 {digest[:12]}…',
                    study_key,
                )
            )
            continue
        path = storage_root() / match.storage_key
        if not path.exists():
            results.append(
                CheckResult(
                    "missing",
                    f'image file missing at {match.storage_key}',
                    study_key,
                )
            )
        else:
            file_digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if file_digest != match.sha256:
                results.append(
                    CheckResult(
                        "incorrect",
                        "image file hash does not match study_images.sha256",
                        study_key,
                    )
                )
            else:
                results.append(
                    CheckResult("match", f"image {digest[:12]}… file and hash match", study_key)
                )
        if match.role != role:
            results.append(
                CheckResult(
                    "incorrect",
                    f'image role is "{match.role}"; expected "{role}"',
                    study_key,
                )
            )
        else:
            results.append(CheckResult("match", f'image role is "{role}"', study_key))
    stored_extra = [row for row in stored if row.sha256 not in expected_digests]
    for row in stored_extra:
        results.append(
            CheckResult(
                "extra",
                f'"{study.title}" has image sha256 {row.sha256[:12]}… not on the study slides',
                study_key,
            )
        )
    if expected and not stored_extra and len(stored) == len(expected):
        if not any(check.status != "match" for check in results):
            pass
    elif not expected and not stored:
        results.append(CheckResult("match", "no study images expected or stored", study_key))
    return results


def _study_source_text(
    version: StudyVersion,
    title_blocks: list[TextBlock] | None,
    stored_titles: list[tuple[int, str]],
    blocks: list[TextBlock],
) -> str:
    parts = [version.raw_text or ""]
    for name in FIELD_NAMES:
        parts.append(getattr(version, name) or "")
    if title_blocks is not None:
        span = _span_blocks(title_blocks, "", stored_titles, blocks)
        parts.extend(block.text for block in title_blocks)
        parts.extend(block.text for block in span)
    return "\n".join(parts)


def _block_accounted_for(
    block: TextBlock,
    studies: list[StudyReport],
    stored: Sequence[StoredStudy],
) -> bool:
    text = block.text.strip()
    for item in stored:
        if item.study.title == text or item.study.section == text:
            return True
        version = item.version
        if text and text in (version.raw_text or "").splitlines():
            return True
        for name in FIELD_NAMES:
            field = getattr(version, name) or ""
            if text in field.splitlines() or text == field.strip():
                return True
        # wrapped title piece
        if text and text in item.study.title:
            return True
    return False


def _extra_active_studies(
    session: Session,
    stored: Sequence[StoredStudy],
    blocks: list[TextBlock],
) -> list[CheckResult]:
    stored_ids = {item.study.id for item in stored}
    results: list[CheckResult] = []
    for study in session.exec(select(Study).where(Study.status == "active")).all():
        if study.id in stored_ids:
            continue
        found = _find_title_blocks(study.title, blocks)
        if found is None:
            results.append(
                CheckResult(
                    "extra",
                    f'"{study.title}" is still active and its text is not in this file',
                )
            )
    return results


def _is_latest_completed(session: Session, deck: DeckImport) -> bool:
    if deck.status != "completed":
        return False
    later = _later_completed(session, deck)
    return later is None


def _later_completed(session: Session, deck: DeckImport) -> DeckImport | None:
    rows = list(
        session.exec(
            select(DeckImport).where(
                DeckImport.status == "completed",
                DeckImport.imported_at > deck.imported_at,
            )
        ).all()
    )
    if not rows:
        return None
    return min(rows, key=lambda row: row.imported_at)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm_l = math.sqrt(sum(a * a for a in left))
    norm_r = math.sqrt(sum(b * b for b in right))
    if norm_l == 0.0 or norm_r == 0.0:
        return 0.0
    return dot / (norm_l * norm_r)


def _is_zero(vector: Sequence[float]) -> bool:
    return bool(vector) and all(value == 0.0 for value in vector)


def _short(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _title_from_key(key: str) -> str:
    if "\n" not in key:
        return key
    return key.split("\n", 1)[1]
