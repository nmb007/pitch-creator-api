"""Celery application. Deck ingestion is the only task in Week 1."""

from celery import Celery

from src.config import settings

celery_app = Celery(
    "pitch_creator",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_BROKER_URL,
    include=["src.decks.tasks"],
)

celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)
