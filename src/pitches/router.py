"""Pitch HTTP routes: create/follow-up as SSE, plus hydrate endpoints."""

from typing import Annotated
from uuid import UUID

from asyncer import asyncify
from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from src.exceptions import ConflictError, NotFoundError
from src.pitches import service as pitch_service
from src.pitches.dependencies import CurrentUserDep
from src.pitches.exceptions import (
    BothInputsError,
    EmptyInputError,
    EmptySelectionError,
    UnknownSelectionError,
)
from src.pitches.extract import ALLOWED_EXTENSIONS
from src.pitches.schemas import (
    FollowUpMessage,
    MatchResponse,
    MessageResponse,
    PageResponse,
    PageStudyResponse,
    PageUpdateRequest,
    PitchResponse,
    SelectionRequest,
)
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
        matched = await asyncify(pitch_service.has_matches)(pitch_id)
        page = await asyncify(pitch_service.has_page)(pitch_id)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pitch not found"
        ) from exc
    return _pitch_response(pitch, has_matches=matched, has_page=page)


@router.put(
    "/{pitch_id}/selection",
    summary="Select studies and stream the generated pitch link",
    responses=error_responses(
        HTTP_404_NOT_FOUND=("Pitch or study not found", "Pitch not found"),
        HTTP_409_CONFLICT=(
            "Pitch already generated or a turn is running",
            "A pitch page already exists for this pitch",
        ),
        HTTP_422_UNPROCESSABLE_CONTENT=(
            "Select at least one study",
            "Select at least one study",
        ),
    ),
)
async def put_selection(
    pitch_id: UUID,
    payload: SelectionRequest,
    user: CurrentUserDep,
) -> StreamingResponse:
    try:
        # Validate before opening the stream so empty/unknown ids stay JSON errors.
        await asyncify(pitch_service.apply_selection)(
            pitch_id=pitch_id,
            user_id=user.id,
            study_ids=payload.study_ids,
        )
    except EmptySelectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Select at least one study",
        ) from exc
    except UnknownSelectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Study is not a match for this pitch",
        ) from exc
    except ConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A pitch page already exists for this pitch",
        ) from exc
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pitch not found"
        ) from exc

    async def events():
        try:
            # Selection already applied above; stream_selection re-applies safely.
            async for chunk in pitch_service.stream_selection(
                pitch_id=pitch_id,
                user_id=user.id,
                study_ids=payload.study_ids,
            ):
                yield chunk
        except ConflictError:
            yield pitch_service.format_sse(
                "error", {"detail": "A turn is already running for this pitch"}
            )
            yield pitch_service.format_sse("done", {})

    return StreamingResponse(events(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/{pitch_id}/page", summary="Latest generated pitch page")
async def get_page(pitch_id: UUID, user: CurrentUserDep) -> PageResponse:
    try:
        row = await asyncify(pitch_service.get_page)(pitch_id, user.id)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pitch page not found"
        ) from exc
    return _page_response(row)


@router.patch(
    "/{pitch_id}/page",
    summary="Edit the pitch page (new revision)",
    responses=error_responses(
        HTTP_404_NOT_FOUND=("Pitch page not found", "Pitch page not found"),
        HTTP_422_UNPROCESSABLE_CONTENT=(
            "Title and body are required",
            "Title and body are required",
        ),
    ),
)
async def patch_page(
    pitch_id: UUID,
    payload: PageUpdateRequest,
    user: CurrentUserDep,
) -> PageResponse:
    try:
        row = await asyncify(pitch_service.update_page)(
            pitch_id=pitch_id,
            user_id=user.id,
            title=payload.title,
            body=payload.body,
        )
    except EmptyInputError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Title and body are required",
        ) from exc
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pitch page not found"
        ) from exc
    return _page_response(row)


@router.delete(
    "/{pitch_id}/page/studies/{study_id}",
    summary="Remove a selected study from the pitch page",
    responses=error_responses(
        HTTP_404_NOT_FOUND=("Pitch page or study not found", "Pitch page not found"),
    ),
)
async def delete_page_study(
    pitch_id: UUID,
    study_id: UUID,
    user: CurrentUserDep,
    rewrite: Annotated[
        bool,
        Query(description="When true, also rewrite title and body from remaining studies"),
    ] = False,
) -> PageResponse:
    try:
        row = await asyncify(pitch_service.remove_page_study)(
            pitch_id=pitch_id,
            user_id=user.id,
            study_id=study_id,
            rewrite=rewrite,
        )
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pitch page not found"
        ) from exc
    return _page_response(row)


def _page_response(row: dict) -> PageResponse:  # noqa: ANN001
    return PageResponse(
        title=row["title"],
        body=row["body"],
        studies=[PageStudyResponse(**study) for study in row["studies"]],
    )


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


def _pitch_response(  # noqa: ANN001
    pitch,
    *,
    has_matches: bool = False,
    has_page: bool = False,
) -> PitchResponse:
    return PitchResponse(
        id=pitch.id,
        title=pitch.title,
        created_at=pitch.created_at,
        updated_at=pitch.updated_at,
        has_matches=has_matches,
        has_page=has_page,
    )
