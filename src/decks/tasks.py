"""Celery task for deck ingestion. The API process only enqueues this."""

from __future__ import annotations

import logging
from uuid import UUID

from celery import shared_task

from src.decks import service as deck_service
from src.decks.ingest_log import IngestPhase, ingest_info

logger = logging.getLogger(__name__)


@shared_task(
    name="decks.ingest_deck",
    acks_late=True,
    max_retries=0,
)
def ingest_deck(deck_import_id: str) -> None:
    deck_id = UUID(deck_import_id)
    ingest_info(deck_id, IngestPhase.READ, "ingest task started.")
    deck_service.run_import(deck_id)
