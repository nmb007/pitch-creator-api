"""Pitch domain exceptions."""

from src.exceptions import AppError


class BothInputsError(AppError):
    """A pitch was given a message and a file together."""


class EmptyInputError(AppError):
    """A pitch was given neither a message nor a readable file."""


class EmptySelectionError(AppError):
    """Generate was called with no selected studies."""


class UnknownSelectionError(AppError):
    """One or more study ids are not matches for this pitch."""
