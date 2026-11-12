"""Pitch HTTP routes for Week 1 search."""

from uuid import UUID

from asyncer import asyncify
from fastapi import APIRouter, HTTPException, Request, status

from src.exceptions import ConflictError, NotFoundError
from src.pitches import service as pitch_service
from src.pitches.dependencies import CurrentUserDep
from src.pitches.exceptions import BothInputsError, EmptyInputError
from src.pitches.extract import ALLOWED_EXTENSIONS
from src.pitches.schemas import MatchResponse, MessageResponse, PitchResponse
from src.schemas import NOT_AUTHENTICATED_RESPONSE, error_responses

router = APIRouter(prefix="/pitches", tags=["pitches"], responses=NOT_AUTHENTICATED_RESPONSE)

_INPUT_ERRORS = error_responses(
    HTTP_422_UNPROCESSABLE_CONTENT=(
        "Send a message or a file, not both",
        "Send a message or a file, not both",
    ),
)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Start a pitch from one message or one file",
    responses=_INPUT_ERRORS,
)
async def create_pitch(request: Request, user: CurrentUserDep) -> PitchResponse:
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
        await pitch_service.start_search(pitch.id)
        pitch = await asyncify(pitch_service.get_pitch)(pitch.id, user.id)
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
    except ConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A search is already running for this pitch",
        ) from exc
    return _pitch_response(pitch)


@router.get("/{pitch_id}", summary="Pitch status")
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
        status=pitch.status,
        prospect_text=pitch.prospect_text,
        created_at=pitch.created_at,
        updated_at=pitch.updated_at,
    )
