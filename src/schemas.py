"""Shared API schemas."""

from typing import Any

from fastapi import status
from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """Standard HTTP error body returned by FastAPI ``HTTPException``."""

    detail: str = Field(description="Human-readable error message")


def success_response(
    *,
    model: type[BaseModel],
    description: str,
    example: dict[str, Any],
) -> dict:
    """OpenAPI success response with schema model and a sample JSON body."""
    return {
        "model": model,
        "description": description,
        "content": {
            "application/json": {
                "example": example,
            }
        },
    }


def error_response(
    *,
    description: str,
    detail: str | None = None,
) -> dict:
    """OpenAPI response entry with ``ErrorResponse`` schema and a sample body."""
    content: dict = {}
    if detail is not None:
        content["example"] = {"detail": detail}
    return {
        "model": ErrorResponse,
        "description": description,
        "content": {"application/json": content},
    }


def error_responses(**entries: tuple[str, str]) -> dict:
    """Build a ``responses`` map from ``HTTP_<code>=(description, detail)`` kwargs."""
    out: dict = {}
    for name, (description, detail) in entries.items():
        code = getattr(status, name)
        out[code] = error_response(description=description, detail=detail)
    return out


NOT_AUTHENTICATED_RESPONSE: dict = error_responses(
    HTTP_401_UNAUTHORIZED=(
        "Missing or invalid access token",
        "Not authenticated",
    ),
)
