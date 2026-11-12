"""Pitch domain exceptions."""

from src.exceptions import AppError


class BothInputsError(AppError):
    """A pitch was given a message and a file together."""


class EmptyInputError(AppError):
    """A pitch was given neither a message nor a readable file."""
