"""Pitch HTTP routes: create/follow-up as SSE, plus hydrate endpoints."""

from uuid import UUID

from asyncer import asyncify
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from src.exceptions import ConflictError, NotFoundError
from src.pitches import service as pitch_service
from src.pitches.dependencies import CurrentUserDep
from src.pitches.exceptions import BothInputsError, EmptyInputError
from src.pitches.extract import ALLOWED_EXTENSIONS
from src.pitches.schemas import FollowUpMessage, MatchResponse, MessageResponse, PitchResponse
from src.schemas import NOT_AUTHENTICATED_RESPONSE, error_responses

router = APIRouter(prefix="/pitches", tags=["pitches"], responses=NOT_AUTHENTICATED_RESPONSE)

_INPUT_ERRORS = error_responses(
    HTTP_422_UNPROCESSABLE_CONTENT=(
        "Send a message or a file, not both",
        "Send a message or a file, not both",
    ),
)
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Start a pitch thread and stream the first assistant turn",
    responses=_INPUT_ERRORS,
)
async def create_pitch(request: Request, user: CurrentUserDep) -> StreamingResponse:
    message, file_name, data = await _one_input(request)
    try:
        if data is not None and file_name is not None:
            pitch = await asyncify(pitch_service.create_from_file)(
                user_id=user.id,
                file_name=file_name,
                data=data,
            )
        else:
            pitch = await asyncify(pitch_service.create_from_message)(
                user_id=user.id,
                message=message or "",
            )
        opening = await asyncify(pitch_service.materialize_opening_turn)(pitch.id)
    except BothInputsError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Send a message or a file, not both",
        ) from exc
    except EmptyInputError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Send a message or a .txt, .pdf, or .docx file",
        ) from exc

    async def events():
        try:
            async for chunk in pitch_service.stream_turn(
                pitch_id=pitch.id,
                turn_text=opening.body,
                emit_ready=True,
            ):
                yield chunk
        except ConflictError:
            yield pitch_service.format_sse(
                "error", {"detail": "A turn is already running for this pitch"}
            )
            yield pitch_service.format_sse("done", {})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        status_code=status.HTTP_201_CREATED,
        headers=_SSE_HEADERS,
    )


@router.post(
    "/{pitch_id}/messages",
    summary="Send a follow-up message and stream the assistant turn",
    responses=_INPUT_ERRORS,
)
async def post_message(
    pitch_id: UUID,
    payload: FollowUpMessage,
    user: CurrentUserDep,
) -> StreamingResponse:
    try:
        row = await asyncify(pitch_service.append_user_message)(
            pitch_id=pitch_id,
            user_id=user.id,
            message=payload.message,
        )
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pitch not found"
        ) from exc
    except EmptyInputError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Send a message or a .txt, .pdf, or .docx file",
        ) from exc

    async def events():
        try:
            async for chunk in pitch_service.stream_turn(
                pitch_id=pitch_id,
                turn_text=row.body,
                emit_ready=False,
            ):
                yield chunk
        except ConflictError:
            yield pitch_service.format_sse(
                "error", {"detail": "A turn is already running for this pitch"}
            )
            yield pitch_service.format_sse("done", {})

    return StreamingResponse(events(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("", summary="List pitches for the current user")
async def list_pitches(user: CurrentUserDep) -> list[PitchResponse]:
    pitches = await asyncify(pitch_service.list_pitches)(user.id)
    return [_pitch_response(pitch) for pitch in pitches]


@router.get("/{pitch_id}", summary="Pitch thread")
async def get_pitch(pitch_id: UUID, user: CurrentUserDep) -> PitchResponse:
    try:
        pitch = await asyncify(pitch_service.get_pitch)(pitch_id, user.id)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pitch not found"
        ) from exc
    return _pitch_response(pitch)


@router.get("/{pitch_id}/matches", summary="Ranked studies and reasons")
async def get_matches(pitch_id: UUID, user: CurrentUserDep) -> list[MatchResponse]:
    try:
        rows = await asyncify(pitch_service.list_matches)(pitch_id, user.id)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pitch not found"
        ) from exc
    return [MatchResponse(**row) for row in rows]


@router.get("/{pitch_id}/messages", summary="Pitch chat")
async def get_messages(pitch_id: UUID, user: CurrentUserDep) -> list[MessageResponse]:
    try:
        messages = await asyncify(pitch_service.list_messages)(pitch_id, user.id)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pitch not found"
        ) from exc
    return [
        MessageResponse(
            id=message.id, role=message.role, body=message.body, created_at=message.created_at
        )
        for message in messages
    ]


async def _one_input(request: Request) -> tuple[str | None, str | None, bytes | None]:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = await request.json()
        message = str(payload.get("message") or "")
        has_file = "file" in payload and payload.get("file") not in (None, "")
        if message.strip() and has_file:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Send a message or a file, not both",
            )
        if has_file:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Send the file as multipart form data",
            )
        return message, None, None
    if "multipart/form-data" in content_type:
        form = await request.form()
        raw_message = form.get("message")
        message = raw_message if isinstance(raw_message, str) else None
        upload = form.get("file")
        filename = getattr(upload, "filename", None)
        has_file = bool(filename)
        if message and message.strip() and has_file:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Send a message or a file, not both",
            )
        if has_file:
            suffix = filename.lower().rsplit(".", 1)
            ext = f".{suffix[-1]}" if len(suffix) == 2 else ""
            if ext not in ALLOWED_EXTENSIONS:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Send a message or a .txt, .pdf, or .docx file",
                )
            data = await upload.read()
            return None, filename, data
        return message, None, None
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="Send a message or a file, not both",
    )


def _pitch_response(pitch) -> PitchResponse:  # noqa: ANN001
    return PitchResponse(
        id=pitch.id,
        title=pitch.title,
        created_at=pitch.created_at,
        updated_at=pitch.updated_at,
    )
