"""Create pitches and stream conversational turns over SSE."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from langchain_core.messages import HumanMessage
from sqlmodel import select

from src import runtime
from src.config import settings
from src.database import get_sync_session
from src.decks.models import Study
from src.exceptions import NotFoundError
from src.models import utcnow
from src.pitches.exceptions import EmptyInputError
from src.pitches.extract import ALLOWED_EXTENSIONS, extract_text
from src.pitches.models import Pitch, PitchInput, PitchMatch, PitchMessage

logger = logging.getLogger(__name__)

TITLE_MAX = 80

_active: set[str] = set()
_guard = asyncio.Lock()


def thread_title(*, kind: str, body: str | None, file_name: str | None) -> str:
    if kind == "file" and file_name:
        return file_name[:200]
    text = (body or "").strip().replace("\n", " ")
    if len(text) <= TITLE_MAX:
        return text[:200]
    return text[: TITLE_MAX - 1].rstrip() + "…"


def create_from_message(*, user_id: UUID, message: str) -> Pitch:
    text = message.strip()
    if not text:
        raise EmptyInputError()
    return _insert(
        user_id=user_id,
        kind="message",
        body=text,
        file_name=None,
        storage_key=None,
        title=thread_title(kind="message", body=text, file_name=None),
    )


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
        title=thread_title(kind="file", body=extracted, file_name=safe_name),
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


def append_user_message(*, pitch_id: UUID, user_id: UUID, message: str) -> PitchMessage:
    text = message.strip()
    if not text:
        raise EmptyInputError()
    with get_sync_session() as session:
        pitch = session.get(Pitch, pitch_id)
        if pitch is None or pitch.created_by != user_id:
            raise NotFoundError()
        row = PitchMessage(pitch_id=pitch_id, role="user", body=text)
        pitch.updated_at = utcnow()
        session.add(row)
        session.add(pitch)
        session.commit()
        session.refresh(row)
        session.expunge(row)
        return row


def _insert(
    *,
    user_id: UUID,
    kind: str,
    body: str | None,
    file_name: str | None,
    storage_key: str | None,
    title: str,
) -> Pitch:
    with get_sync_session() as session:
        pitch = Pitch(created_by=user_id, title=title[:200])
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


def format_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def materialize_opening_turn(pitch_id: UUID) -> PitchMessage:
    """Ensure the first user message exists from pitch_inputs; return it."""
    with get_sync_session() as session:
        existing = session.exec(
            select(PitchMessage)
            .where(PitchMessage.pitch_id == pitch_id)
            .where(PitchMessage.role == "user")
            .order_by(PitchMessage.created_at)
        ).first()
        if existing is not None:
            session.expunge(existing)
            return existing
        incoming = session.exec(select(PitchInput).where(PitchInput.pitch_id == pitch_id)).first()
        if incoming is None or not incoming.body:
            raise EmptyInputError()
        if incoming.kind == "file" and incoming.file_name:
            label = f"Uploaded {incoming.file_name}\n\n{incoming.body}"
        else:
            label = incoming.body
        row = PitchMessage(pitch_id=pitch_id, role="user", body=label)
        pitch = session.get(Pitch, pitch_id)
        if pitch is not None:
            pitch.updated_at = utcnow()
            session.add(pitch)
        session.add(row)
        session.commit()
        session.refresh(row)
        session.expunge(row)
        return row


async def stream_turn(
    *,
    pitch_id: UUID,
    turn_text: str,
    emit_ready: bool = False,
) -> AsyncIterator[str]:
    turn = turn_text.strip()
    if not turn:
        raise EmptyInputError()
    key = str(pitch_id)
    async with _guard:
        if key in _active:
            from src.exceptions import ConflictError

            raise ConflictError()
        _active.add(key)
    try:
        if emit_ready:
            yield format_sse("ready", {"pitch_id": key})
        user_payload = _latest_user_message(pitch_id)
        if user_payload is not None:
            yield format_sse("message.user", user_payload)
        graph = runtime.graph
        if graph is None:
            body = (
                "I can help with Arbisoft case studies and pitch shortlists. "
                "What should we look for?"
            )
            message = _store_assistant(pitch_id, body)
            yield format_sse("assistant.delta", {"text": body})
            yield format_sse("assistant.message", message)
            yield format_sse("done", {})
            return
        config = {
            "configurable": {"thread_id": key},
            "metadata": {"pitch_id": key, "model": settings.GROQ_MODEL},
            "tags": [key, settings.GROQ_MODEL],
        }
        state_in = {
            "pitch_id": key,
            "messages": [HumanMessage(content=turn)],
            "awaiting_selection": False,
        }
        interrupted = False
        try:
            async for event in graph.astream_events(state_in, config=config, version="v2"):
                for chunk in _sse_from_graph_event(event, pitch_id=pitch_id):
                    yield chunk
        except Exception as exc:
            if exc.__class__.__name__ == "GraphInterrupt":
                interrupted = True
            else:
                logger.exception("Turn failed for pitch %s", key)
                yield format_sse("error", {"detail": "The assistant turn failed."})
                yield format_sse("done", {})
                return
        assistant = _latest_assistant_message(pitch_id)
        if assistant is not None:
            yield format_sse("assistant.message", assistant)
        matches = list_matches_public(pitch_id)
        if matches:
            yield format_sse("matches", {"items": matches})
        if interrupted:
            yield format_sse("interrupted", {"pitch_id": key})
        yield format_sse("done", {})
    finally:
        _active.discard(key)


async def start_search(pitch_id: UUID) -> None:
    opening = materialize_opening_turn(pitch_id)
    async for _ in stream_turn(pitch_id=pitch_id, turn_text=opening.body, emit_ready=False):
        pass


def get_pitch(pitch_id: UUID, user_id: UUID) -> Pitch:
    with get_sync_session() as session:
        pitch = session.get(Pitch, pitch_id)
        if pitch is None or pitch.created_by != user_id:
            raise NotFoundError()
        session.expunge(pitch)
        return pitch


def list_pitches(user_id: UUID) -> list[Pitch]:
    with get_sync_session() as session:
        pitches = list(
            session.exec(
                select(Pitch)
                .where(Pitch.created_by == user_id)
                .order_by(Pitch.updated_at.desc())
            ).all()
        )
        for pitch in pitches:
            session.expunge(pitch)
        return pitches


def has_matches(pitch_id: UUID) -> bool:
    with get_sync_session() as session:
        row = session.exec(
            select(PitchMatch.id).where(PitchMatch.pitch_id == pitch_id).limit(1)
        ).first()
        return row is not None


def list_matches(pitch_id: UUID, user_id: UUID) -> list[dict]:
    with get_sync_session() as session:
        pitch = session.get(Pitch, pitch_id)
        if pitch is None or pitch.created_by != user_id:
            raise NotFoundError()
        return list_matches_public(pitch_id)


def list_matches_public(pitch_id: UUID) -> list[dict]:
    with get_sync_session() as session:
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


def _latest_user_message(pitch_id: UUID) -> dict | None:
    with get_sync_session() as session:
        message = session.exec(
            select(PitchMessage)
            .where(PitchMessage.pitch_id == pitch_id)
            .where(PitchMessage.role == "user")
            .order_by(PitchMessage.created_at.desc())
        ).first()
        if message is None:
            return None
        return {
            "id": message.id,
            "role": message.role,
            "body": message.body,
            "created_at": message.created_at,
        }


def _latest_assistant_message(pitch_id: UUID) -> dict | None:
    with get_sync_session() as session:
        message = session.exec(
            select(PitchMessage)
            .where(PitchMessage.pitch_id == pitch_id)
            .where(PitchMessage.role == "assistant")
            .order_by(PitchMessage.created_at.desc())
        ).first()
        if message is None:
            return None
        return {
            "id": message.id,
            "role": message.role,
            "body": message.body,
            "created_at": message.created_at,
        }


def _store_assistant(pitch_id: UUID, body: str) -> dict:
    with get_sync_session() as session:
        row = PitchMessage(pitch_id=pitch_id, role="assistant", body=body)
        pitch = session.get(Pitch, pitch_id)
        if pitch is not None:
            pitch.updated_at = utcnow()
            session.add(pitch)
        session.add(row)
        session.commit()
        session.refresh(row)
        return {
            "id": row.id,
            "role": row.role,
            "body": row.body,
            "created_at": row.created_at,
        }


def _chunk_text(chunk: object) -> str:
    if chunk is None:
        return ""
    content = getattr(chunk, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return ""


def _langgraph_node(event: dict) -> str:
    meta = event.get("metadata") or {}
    node = meta.get("langgraph_node")
    if isinstance(node, str) and node:
        return node
    name = event.get("name")
    return name if isinstance(name, str) else ""


def _sse_from_graph_event(event: dict, *, pitch_id: UUID):
    """Map LangGraph astream_events → SSE. Skip policy_gate model streams."""
    kind = event.get("event")
    node = _langgraph_node(event)

    if kind == "on_chat_model_stream":
        if node == "policy_gate":
            return
        chunk = event.get("data", {}).get("chunk")
        text = _chunk_text(chunk)
        if text:
            yield format_sse("assistant.delta", {"text": text})
        return

    if kind == "on_chain_end" and node == "policy_gate":
        output = event.get("data", {}).get("output")
        gate = output.get("gate") if isinstance(output, dict) else None
        if gate == "allow_tools":
            yield format_sse("status", {"text": "Hang tight, we're working on it!"})
        return

    if kind == "on_chat_model_end":
        if node == "policy_gate":
            return
        output = event.get("data", {}).get("output")
        for call in _tool_calls_from_model_output(output):
            yield format_sse(
                "tool.selected",
                {
                    "name": call["name"],
                    "args": call["args"],
                    "id": call["id"],
                },
            )
        return

    if kind == "on_tool_start":
        tool_name = event.get("name") or "tool"
        yield format_sse(
            "tool.started",
            {
                "name": tool_name,
                "input": event.get("data", {}).get("input"),
            },
        )
        return

    if kind == "on_tool_end":
        tool_name = event.get("name") or "tool"
        raw_output = event.get("data", {}).get("output")
        body = _tool_result_body(tool_name, raw_output)
        summary = _tool_result_summary(tool_name, raw_output)
        yield format_sse(
            "tool.finished",
            {
                "name": tool_name,
                "ok": summary.get("ok", True),
                "summary": summary.get("label", ""),
                "body": body,
            },
        )
        if tool_name == "recommend_pitch_studies":
            matches = list_matches_public(pitch_id)
            if matches:
                yield format_sse("matches", {"items": matches})
        return


def _tool_calls_from_model_output(output: object) -> list[dict]:
    raw_calls = getattr(output, "tool_calls", None) or []
    calls: list[dict] = []
    for item in raw_calls:
        if isinstance(item, dict):
            name = str(item.get("name") or "tool")
            args = item.get("args") if isinstance(item.get("args"), dict) else {}
            call_id = str(item.get("id") or "")
        else:
            name = str(getattr(item, "name", None) or "tool")
            args = getattr(item, "args", None)
            args = args if isinstance(args, dict) else {}
            call_id = str(getattr(item, "id", None) or "")
        calls.append({"name": name, "args": args, "id": call_id})
    return calls


def _tool_result_body(tool_name: str, raw_output: object) -> str:
    if hasattr(raw_output, "content"):
        text = _chunk_text(raw_output)
    elif isinstance(raw_output, str):
        text = raw_output
    else:
        text = json.dumps(raw_output, default=str)
    text = (text or "").strip()
    if text.startswith(f"{tool_name}:"):
        return text
    return f"{tool_name}: {text}" if text else tool_name


def _tool_result_summary(tool_name: str, raw_output: object) -> dict:
    text = _tool_result_body(tool_name, raw_output)
    payload_text = text
    prefix = f"{tool_name}:"
    if payload_text.startswith(prefix):
        payload_text = payload_text[len(prefix) :].strip()
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return {"ok": True, "label": tool_name}
    if not isinstance(payload, dict):
        return {"ok": True, "label": tool_name}
    ok = bool(payload.get("ok", True))
    studies = payload.get("studies")
    if isinstance(studies, list):
        count = len(studies)
        label = "1 study" if count == 1 else f"{count} studies"
        return {"ok": ok, "label": label}
    if payload.get("error"):
        return {"ok": False, "label": str(payload.get("error"))}
    return {"ok": ok, "label": tool_name}
