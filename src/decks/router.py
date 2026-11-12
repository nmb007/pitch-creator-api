"""Deck HTTP routes. Parsing happens in the Celery worker, not here."""

from typing import Annotated
from uuid import UUID

from asyncer import asyncify
from fastapi import APIRouter, File, HTTPException, UploadFile, status

from src.decks import service as deck_service
from src.decks.dependencies import CurrentUserDep
from src.decks.schemas import DeckResponse, StudyResponse
from src.exceptions import NotFoundError
from src.schemas import NOT_AUTHENTICATED_RESPONSE, error_responses

router = APIRouter(tags=["decks"], responses=NOT_AUTHENTICATED_RESPONSE)


@router.post(
    "/decks",
    status_code=status.HTTP_201_CREATED,
    summary="Upload a master PowerPoint",
    responses=error_responses(
        HTTP_422_UNPROCESSABLE_CONTENT=("The file is not a PowerPoint", "Upload a .pptx file"),
    ),
)
async def upload_deck(
    user: CurrentUserDep,
    file: Annotated[UploadFile, File()],
) -> DeckResponse:
    name = file.filename or ""
    if not name.lower().endswith(".pptx"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Upload a .pptx file"
        )
    data = await file.read()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Upload a .pptx file"
        )
    deck = await asyncify(deck_service.create_import)(
        user_id=user.id,
        file_name=name,
        data=data,
    )
    return DeckResponse(
        id=deck.id,
        file_name=deck.file_name,
        status=deck.status,
        imported_at=deck.imported_at,
        studies_added=0,
        studies_updated=0,
        studies_hidden=0,
    )


@router.get("/decks/{deck_id}", summary="Deck import status")
async def get_deck(deck_id: UUID, user: CurrentUserDep) -> DeckResponse:
    del user
    try:
        deck = await asyncify(deck_service.get_deck)(deck_id)
        added, updated, hidden = await asyncify(deck_service.deck_counts)(deck_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deck not found") from exc
    return DeckResponse(
        id=deck.id,
        file_name=deck.file_name,
        status=deck.status,
        imported_at=deck.imported_at,
        studies_added=added,
        studies_updated=updated,
        studies_hidden=hidden,
    )


@router.get("/studies", summary="Active studies")
async def list_studies(user: CurrentUserDep) -> list[StudyResponse]:
    del user
    studies = await asyncify(deck_service.list_active_studies)()
    return [
        StudyResponse(
            id=study.id,
            section=study.section,
            title=study.title,
            client_name=study.client_name,
            status=study.status,
        )
        for study in studies
    ]
