"""Create a pitch from one message or one file, then run search."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import UUID

from sqlmodel import select

from src import runtime
from src.config import settings
from src.database import get_sync_session
from src.decks.models import Study
from src.exceptions import NotFoundError
from src.pitches.exceptions import EmptyInputError
from src.pitches.extract import ALLOWED_EXTENSIONS, extract_text
from src.pitches.models import Pitch, PitchInput, PitchMatch, PitchMessage

logger = logging.getLogger(__name__)

_active: set[str] = set()
_guard = asyncio.Lock()


def create_from_message(*, user_id: UUID, message: str) -> Pitch:
    text = message.strip()
    if not text:
        raise EmptyInputError()
    return _insert(user_id=user_id, kind="message", body=text, file_name=None, storage_key=None)


def create_from_file(*, user_id: UUID, file_name: str, data: bytes) -> Pitch:
    suffix = Path(file_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS or not data:
        raise EmptyInputError()
    safe_name = Path(file_name).name
    scratch = Path(settings.STORAGE_DIR) / "prospects" / "incoming"
    scratch.mkdir(parents=True, exist_ok=True)
    scratch_path = scratch / safe_name
    scratch_path.write_bytes(data)
    try:
        extracted = extract_text(scratch_path)
    finally:
        scratch_path.unlink(missing_ok=True)
    if not extracted:
        raise EmptyInputError()
    pitch = _insert(
        user_id=user_id,
        kind="file",
        body=extracted,
        file_name=safe_name,
        storage_key=None,
    )
    folder = Path(settings.STORAGE_DIR) / "prospects" / str(pitch.id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / safe_name).write_bytes(data)
    relative = f"prospects/{pitch.id}/{safe_name}"
    with get_sync_session() as session:
        incoming = session.exec(select(PitchInput).where(PitchInput.pitch_id == pitch.id)).one()
        incoming.storage_key = relative
        session.add(incoming)
        session.commit()
    return pitch


def _insert(
    *,
    user_id: UUID,
    kind: str,
    body: str | None,
    file_name: str | None,
    storage_key: str | None,
) -> Pitch:
    with get_sync_session() as session:
        pitch = Pitch(created_by=user_id, status="collecting", prospect_text="")
        session.add(pitch)
        session.flush()
        session.add(
            PitchInput(
                pitch_id=pitch.id,
                kind=kind,
                body=body,
                file_name=file_name,
                storage_key=storage_key,
                created_by=user_id,
            )
        )
        session.commit()
        session.refresh(pitch)
        session.expunge(pitch)
        return pitch


async def start_search(pitch_id: UUID) -> None:
    key = str(pitch_id)
    async with _guard:
        if key in _active:
            from src.exceptions import ConflictError

            raise ConflictError()
        _active.add(key)
    logger.info("Starting search for pitch %s", key)
    try:
        graph = runtime.graph
        if graph is None:
            raise RuntimeError("Search graph is not ready")
        config = {
            "configurable": {"thread_id": key},
            "metadata": {"pitch_id": key, "model": settings.GROQ_MODEL},
            "tags": [key, settings.GROQ_MODEL],
        }
        try:
            await graph.ainvoke(
                {"pitch_id": key, "shortlist": []},
                config=config,
            )
        except Exception as exc:
            if exc.__class__.__name__ == "GraphInterrupt":
                return
            raise
    finally:
        _active.discard(key)


def get_pitch(pitch_id: UUID, user_id: UUID) -> Pitch:
    with get_sync_session() as session:
        pitch = session.get(Pitch, pitch_id)
        if pitch is None or pitch.created_by != user_id:
            raise NotFoundError()
        session.expunge(pitch)
        return pitch


def list_matches(pitch_id: UUID, user_id: UUID) -> list[dict]:
    with get_sync_session() as session:
        pitch = session.get(Pitch, pitch_id)
        if pitch is None or pitch.created_by != user_id:
            raise NotFoundError()
        rows = session.exec(
            select(PitchMatch, Study)
            .join(Study, Study.id == PitchMatch.study_id)
            .where(PitchMatch.pitch_id == pitch_id)
            .order_by(PitchMatch.rank)
        ).all()
        return [
            {
                "rank": match.rank,
                "reason": match.reason,
                "source_quote": match.source_quote,
                "section": study.section,
                "title": study.title,
                "study_id": study.id,
                "selected": match.selected,
            }
            for match, study in rows
        ]


def list_messages(pitch_id: UUID, user_id: UUID) -> list[PitchMessage]:
    with get_sync_session() as session:
        pitch = session.get(Pitch, pitch_id)
        if pitch is None or pitch.created_by != user_id:
            raise NotFoundError()
        messages = list(
            session.exec(
                select(PitchMessage)
                .where(PitchMessage.pitch_id == pitch_id)
                .order_by(PitchMessage.created_at)
            ).all()
        )
        for message in messages:
            session.expunge(message)
        return messages
