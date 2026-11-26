"""Tools for refining an existing pitch page via chat."""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from langchain_core.tools import tool

from src.exceptions import ConflictError, NotFoundError
from src.pitches.exceptions import EmptyInputError
from src.pitches.generate import revise_body_content, view_url
from src.pitches.service import (
    add_page_study,
    find_pitch_match_studies,
    get_page,
    pitch_owner_id,
    regenerate_page,
    remove_page_study,
    update_page,
)
from src.pitches.tools import current_pitch_id

logger = logging.getLogger(__name__)

SOURCE_CHAT = "chat"


def _pitch_id() -> UUID:
    return UUID(current_pitch_id.get())


def _owner() -> UUID:
    return pitch_owner_id(_pitch_id())


@tool
def find_pitch_studies(query: str) -> str:
    """Find studies already suggested for this pitch by matching the query to titles only.

    Searches all pitch_matches for this pitch (selected and not selected).
    Use before add_study or remove_study. Do not invent study ids.
    If study_count is 2 or more, list the matches and ask the user which one before
    calling add_study or remove_study.
    """
    pitch_id = _pitch_id()
    cleaned = query.strip()
    if not cleaned:
        return json.dumps({"ok": False, "error": "Query is empty", "studies": []})
    studies = find_pitch_match_studies(pitch_id=pitch_id, query=cleaned)
    return json.dumps(
        {
            "ok": True,
            "study_count": len(studies),
            "studies": studies,
            "view_url": view_url(pitch_id),
        }
    )


@tool
def add_study(study_id: str, rewrite: bool) -> str:
    """Add a shortlisted study to the pitch page by setting selected=true.

    study_id must already exist in this pitch's pitch_matches.
    rewrite is required: true or false — never omit it.
    Set rewrite=true when the user explicitly asked to rewrite/update the
    description as part of adding, or when they affirmed a prior ask about
    updating the description (yes, please, yeah, update it, etc.).
    Set rewrite=false when they declined that ask, or when they also gave a
    separate copy instruction (then call update_body after this tool finishes).
    """
    pitch_id = _pitch_id()
    try:
        page = add_page_study(
            pitch_id=pitch_id,
            user_id=_owner(),
            study_id=UUID(study_id),
            rewrite=rewrite,
            source=SOURCE_CHAT,
        )
    except ValueError:
        return json.dumps({"ok": False, "error": "Invalid study_id"})
    except NotFoundError:
        return json.dumps(
            {
                "ok": False,
                "error": "Study is not in this pitch's suggested list, or no page exists",
            }
        )
    except ConflictError:
        return json.dumps({"ok": False, "error": "Study is already selected on this pitch"})
    return json.dumps(
        {
            "ok": True,
            "rewritten": rewrite,
            "title": page["title"],
            "study_count": len(page["studies"]),
            "view_url": view_url(pitch_id),
        }
    )


@tool
def remove_study(study_id: str, rewrite: bool) -> str:
    """Remove a selected study from the pitch page by setting selected=false.

    rewrite is required: true or false — never omit it.
    Set rewrite=true when the user explicitly asked to rewrite/update the
    description as part of removing, or when they affirmed a prior ask about
    updating the description (yes, please, yeah, update it, etc.).
    Set rewrite=false when they declined that ask, or when they also gave a
    separate copy instruction (then call update_body after this tool finishes).
    """
    pitch_id = _pitch_id()
    try:
        page = remove_page_study(
            pitch_id=pitch_id,
            user_id=_owner(),
            study_id=UUID(study_id),
            rewrite=rewrite,
            source=SOURCE_CHAT,
        )
    except ValueError:
        return json.dumps({"ok": False, "error": "Invalid study_id"})
    except NotFoundError:
        return json.dumps(
            {
                "ok": False,
                "error": "Study is not selected on this pitch page, or no page exists",
            }
        )
    return json.dumps(
        {
            "ok": True,
            "rewritten": rewrite,
            "title": page["title"],
            "study_count": len(page["studies"]),
            "view_url": view_url(pitch_id),
        }
    )


@tool
def update_title(title: str) -> str:
    """Update only the pitch page title. Keeps the current body."""
    pitch_id = _pitch_id()
    user_id = _owner()
    try:
        current = get_page(pitch_id, user_id)
        page = update_page(
            pitch_id=pitch_id,
            user_id=user_id,
            title=title,
            body=current["body"],
            source=SOURCE_CHAT,
        )
    except EmptyInputError:
        return json.dumps({"ok": False, "error": "Title is required"})
    except NotFoundError:
        return json.dumps({"ok": False, "error": "Pitch page not found"})
    return json.dumps(
        {
            "ok": True,
            "title": page["title"],
            "view_url": view_url(pitch_id),
        }
    )


@tool
def update_body(instruction: str, section: str = "") -> str:
    """Revise the pitch page body from an instruction.

    Optionally name a section to focus on: Why this matters, How we can help,
    Relevant experience, Why Arbisoft, or Next step.
    Always grounded in the prospect brief and currently selected studies.
    """
    pitch_id = _pitch_id()
    user_id = _owner()
    cleaned = instruction.strip()
    if not cleaned:
        return json.dumps({"ok": False, "error": "Instruction is empty"})
    try:
        current = get_page(pitch_id, user_id)
        new_body = revise_body_content(
            pitch_id=pitch_id,
            current_title=current["title"],
            current_body=current["body"],
            instruction=cleaned,
            section=section.strip() or None,
        )
        page = update_page(
            pitch_id=pitch_id,
            user_id=user_id,
            title=current["title"],
            body=new_body,
            source=SOURCE_CHAT,
        )
    except EmptyInputError:
        return json.dumps({"ok": False, "error": "Title and body are required"})
    except NotFoundError:
        return json.dumps({"ok": False, "error": "Pitch page not found"})
    return json.dumps(
        {
            "ok": True,
            "title": page["title"],
            "view_url": view_url(pitch_id),
        }
    )


@tool("regenerate_page")
def regenerate_page_tool() -> str:
    """Regenerate the full pitch title and body from the current selected studies.

    Use when the user asks to start over or regenerate from the current selection.
    """
    pitch_id = _pitch_id()
    try:
        page = regenerate_page(pitch_id=pitch_id, user_id=_owner(), source=SOURCE_CHAT)
    except NotFoundError:
        return json.dumps({"ok": False, "error": "Pitch page not found"})
    return json.dumps(
        {
            "ok": True,
            "title": page["title"],
            "study_count": len(page["studies"]),
            "view_url": view_url(pitch_id),
        }
    )


def revise_tool_list() -> list[Any]:
    return [
        find_pitch_studies,
        add_study,
        remove_study,
        update_title,
        update_body,
        regenerate_page_tool,
    ]
