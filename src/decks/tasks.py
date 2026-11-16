"""Celery task for deck ingestion. The API process only enqueues this."""

from __future__ import annotations

import logging
from uuid import UUID

from celery import shared_task

from src.decks import service as deck_service

logger = logging.getLogger(__name__)


@shared_task(
    name="decks.ingest_deck",
    acks_late=True,
    max_retries=0,
)
def ingest_deck(deck_import_id: str) -> None:
    logger.info("Ingest task started for deck import %s", deck_import_id)
    deck_service.run_import(UUID(deck_import_id))
    logger.info("Ingest task finished for deck import %s", deck_import_id)
