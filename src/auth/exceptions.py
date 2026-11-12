"""Auth domain exceptions."""

from src.exceptions import AppError


class InvalidCredentials(AppError):
    """Username or password is incorrect."""
