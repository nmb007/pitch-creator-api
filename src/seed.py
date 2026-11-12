"""Create the demo user and the Arbisoft brand row. Safe to run more than once."""

import logging

from src.auth import service as auth_service
from src.config import settings
from src.decks import service as deck_service

logger = logging.getLogger(__name__)


def seed() -> None:
    deck_service.ensure_brand_asset()
    if not settings.SEED_USER_EMAIL or not settings.SEED_USER_PASSWORD:
        logger.info("Seed user skipped. Set SEED_USER_EMAIL and SEED_USER_PASSWORD.")
        return
    auth_service.ensure_user(
        username=settings.SEED_USER_EMAIL,
        password=settings.SEED_USER_PASSWORD,
        name=settings.SEED_USER_NAME,
    )
    logger.info("Seed user is ready: %s", settings.SEED_USER_EMAIL)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seed()
