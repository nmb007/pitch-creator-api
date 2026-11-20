"""Deck upload and the import that the worker runs."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from uuid import UUID

from sqlmodel import Session, select

from src.config import settings
from src.database import get_sync_session
from src.decks.drafts import StudyDraft, chunk_texts, image_role
from src.decks.ingest_log import IngestPhase, ingest_info
from src.decks.models import (
    BrandAsset,
    DeckImport,
    Study,
    StudyChunk,
    StudyImage,
    StudyVersion,
)
from src.decks.parse import parse_pptx
from src.embeddings import embed_texts
from src.exceptions import NotFoundError

logger = logging.getLogger(__name__)

SUMMARY_CHARS = 2000


def storage_root() -> Path:
    root = Path(settings.STORAGE_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def deck_path(deck_id: UUID) -> Path:
    folder = storage_root() / "decks"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{deck_id}.pptx"


def save_deck_file(deck_id: UUID, data: bytes) -> None:
    deck_path(deck_id).write_bytes(data)


def create_import(*, user_id: UUID, file_name: str, data: bytes) -> DeckImport:
    digest = hashlib.sha256(data).hexdigest()
    with get_sync_session() as session:
        deck = DeckImport(
            uploaded_by=user_id,
            file_name=file_name,
            sha256=digest,
            status="queued",
        )
        session.add(deck)
        session.commit()
        session.refresh(deck)
        save_deck_file(deck.id, data)
        deck_id = deck.id
        session.expunge(deck)
    enqueue_ingest(deck_id)
    return deck


def enqueue_ingest(deck_id: UUID) -> None:
    from src.decks.tasks import ingest_deck

    ingest_deck.delay(str(deck_id))


def get_deck(deck_id: UUID) -> DeckImport:
    with get_sync_session() as session:
        deck = session.get(DeckImport, deck_id)
        if deck is None:
            raise NotFoundError()
        session.expunge(deck)
        return deck


def deck_counts(deck_id: UUID) -> tuple[int, int, int]:
    with get_sync_session() as session:
        return _counts(session, deck_id)


def list_active_studies() -> list[Study]:
    with get_sync_session() as session:
        studies = list(session.exec(select(Study).where(Study.status == "active")).all())
        for study in studies:
            session.expunge(study)
        return studies


def run_import(deck_id: UUID) -> None:
    with get_sync_session() as session:
        deck = session.get(DeckImport, deck_id)
        if deck is None:
            logger.error("Deck import %s disappeared before the worker ran", deck_id)
            return
        deck.status = "running"
        session.add(deck)
        session.commit()
        file_name = deck.file_name
    path = deck_path(deck_id)
    try:
        ingest_info(deck_id, IngestPhase.READ, f"reading {file_name}.")
        drafts = parse_pptx(path, deck_id=deck_id)
        with get_sync_session() as session:
            deck = session.get(DeckImport, deck_id)
            if deck is None:
                return
            persist_drafts(session, deck, drafts)
            deck.status = "completed"
            session.add(deck)
            session.commit()
        ingest_info(
            deck_id,
            IngestPhase.DONE,
            f"import completed. {len(drafts)} studies saved.",
        )
    except Exception:
        logger.exception("Deck import %s failed", deck_id)
        ingest_info(deck_id, IngestPhase.FAILED, "import failed.")
        mark_failed(deck_id)
        raise


def mark_failed(deck_id: UUID) -> None:
    with get_sync_session() as session:
        deck = session.get(DeckImport, deck_id)
        if deck is None or deck.status == "completed":
            return
        deck.status = "failed"
        session.add(deck)
        session.commit()


def persist_drafts(session: Session, deck: DeckImport, drafts: list[StudyDraft]) -> None:
    seen: set[tuple[str, str]] = set()
    pending: list[tuple[Study, StudyVersion, list[str], str | None]] = []
    embed_inputs: list[str] = []
    skipped = 0
    changed = 0

    ingest_info(deck.id, IngestPhase.PERSIST, f"saving studies. {len(drafts)} drafts.")
    for draft in drafts:
        seen.add((draft.section, draft.title))
        study = session.exec(
            select(Study).where(Study.section == draft.section, Study.title == draft.title)
        ).first()
        if study is None:
            study = Study(
                section=draft.section,
                title=draft.title,
                client_name=draft.client_name,
                status="active",
            )
            session.add(study)
            session.flush()
        else:
            study.section = draft.section
            study.title = draft.title
            study.status = "active"
            session.add(study)
        digest = hashlib.sha256(draft.raw_text.encode()).hexdigest()
        current = session.exec(
            select(StudyVersion).where(
                StudyVersion.study_id == study.id,
                StudyVersion.is_current.is_(True),
            )
        ).first()
        if current is not None and current.content_hash == digest:
            skipped += 1
            continue
        changed += 1
        study.client_name = draft.client_name
        session.add(study)
        if current is not None:
            current.is_current = False
            session.add(current)
        chunks = chunk_texts(draft)
        # Empty raw_text: keep a zero summary vector and do not invent chunk text.
        summary = (
            f"{draft.section}\n{draft.title}\n{draft.raw_text[:SUMMARY_CHARS]}"
            if draft.raw_text.strip()
            else None
        )
        version = StudyVersion(
            study_id=study.id,
            deck_import_id=deck.id,
            slide_numbers=list(draft.slide_numbers),
            content_hash=digest,
            background=draft.background,
            challenge=draft.challenge,
            approach=draft.approach,
            solution=draft.solution,
            outcome=draft.outcome,
            tech_stack=draft.tech_stack,
            raw_text=draft.raw_text,
            summary_embedding=_zero(),
            is_current=True,
        )
        session.add(version)
        session.flush()
        _save_images(session, version, draft)
        pending.append((study, version, chunks, summary))
        if summary is not None:
            embed_inputs.append(summary)
        embed_inputs.extend(chunks)

    ingest_info(
        deck.id,
        IngestPhase.PERSIST,
        f"finished saving. {changed} changed, {skipped} unchanged.",
    )
    if embed_inputs:
        ingest_info(
            deck.id,
            IngestPhase.EMBED,
            f"embedding {len(embed_inputs)} texts for {len(pending)} studies.",
        )
        vectors = embed_texts(embed_inputs)
        cursor = 0
        for _study, version, chunks, summary in pending:
            if summary is not None:
                version.summary_embedding = vectors[cursor]
                cursor += 1
                session.add(version)
            for position, text in enumerate(chunks, start=1):
                session.add(
                    StudyChunk(
                        study_version_id=version.id,
                        position=position,
                        text=text,
                        embedding=vectors[cursor],
                    )
                )
                cursor += 1
        ingest_info(deck.id, IngestPhase.EMBED, "finished embedding.")
    else:
        ingest_info(deck.id, IngestPhase.EMBED, "no new embeddings needed.")

    hidden = 0
    for study in session.exec(select(Study).where(Study.status == "active")).all():
        if (study.section, study.title) not in seen:
            study.status = "hidden"
            session.add(study)
            hidden += 1
    ingest_info(
        deck.id,
        IngestPhase.HIDE,
        f"hid {hidden} studies that were missing from this file.",
    )


def ensure_brand_asset() -> None:
    root = storage_root() / "brand"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "arbisoft.png"
    if not path.exists():
        path.write_bytes(_placeholder_png())
    with get_sync_session() as session:
        existing = session.exec(select(BrandAsset).where(BrandAsset.name == "Arbisoft")).first()
        if existing is None:
            session.add(BrandAsset(name="Arbisoft", storage_key="brand/arbisoft.png"))
            session.commit()


def _counts(session: Session, deck_id: UUID) -> tuple[int, int, int]:
    versions = list(
        session.exec(select(StudyVersion).where(StudyVersion.deck_import_id == deck_id)).all()
    )
    added = 0
    updated = 0
    imported_ids: set[UUID] = set()
    for version in versions:
        imported_ids.add(version.study_id)
        earlier = session.exec(
            select(StudyVersion).where(
                StudyVersion.study_id == version.study_id,
                StudyVersion.deck_import_id != deck_id,
            )
        ).first()
        if earlier is None:
            added += 1
        else:
            updated += 1
    hidden = 0
    for study in session.exec(select(Study).where(Study.status == "hidden")).all():
        if study.id not in imported_ids:
            hidden += 1
    return added, updated, hidden


def _save_images(session: Session, version: StudyVersion, draft: StudyDraft) -> None:
    folder = storage_root() / "study-images"
    folder.mkdir(parents=True, exist_ok=True)
    for index, image in enumerate(draft.images):
        digest = hashlib.sha256(image.data).hexdigest()
        ext = image.ext.lower().lstrip(".") or "png"
        key = f"study-images/{digest}.{ext}"
        target = storage_root() / key
        if not target.exists():
            target.write_bytes(image.data)
        role = image_role(index, draft.client_name)
        session.add(
            StudyImage(
                study_version_id=version.id,
                role=role,
                storage_key=key,
                sha256=digest,
            )
        )


def _zero() -> list[float]:
    from src.config import settings as app_settings

    return [0.0] * app_settings.EMBEDDING_DIMENSIONS


def _placeholder_png() -> bytes:
    # 1x1 PNG. The real Arbisoft file can replace this path later.
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
        "0000000c4944415408d763f8cfc0000003010100c9fef6e80000000049454e44ae426082"
    )
