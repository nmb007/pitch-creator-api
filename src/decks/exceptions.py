"""Deck domain exceptions."""

from src.exceptions import AppError


class EmptyDeckError(AppError):
    """The upload had no PowerPoint bytes."""
