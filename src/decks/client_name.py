"""One Groq call for a study whose text is new or changed."""

from __future__ import annotations

import logging

from src.config import settings
from src.llm import chat_model

logger = logging.getLogger(__name__)

_EMPTY = {"none", "null", "n/a", "na", "unknown", "unnamed", "not found"}


def extract_client_name(title: str, raw_text: str) -> str | None:
    source = f"{title}\n{raw_text}"
    prompt = (
        "The case study below was delivered by Arbisoft for a client. "
        "Reply with only the client organization name, copied from the text. "
        "The client is the organization the work was done for. "
        "Arbisoft is the vendor, not the client. "
        "If the text does not name that organization, reply NONE.\n\n"
        f"Title:\n{title}\n\n"
        f"Text:\n{raw_text[:6000]}"
    )
    try:
        reply = chat_model().invoke(
            prompt,
            config={
                "metadata": {"model": settings.GROQ_MODEL, "task": "client_name"},
                "tags": [settings.GROQ_MODEL, "client_name"],
            },
        )
    except Exception:
        logger.exception("Client name call failed for %s", title)
        return None
    return _accepted_name(_reply_text(reply), source)


def _reply_text(reply) -> str:  # noqa: ANN001
    content = reply.content
    if isinstance(content, str):
        return content.strip()
    return str(content).strip()


def _accepted_name(reply: str, source: str) -> str | None:
    line = reply.splitlines()[0].strip().strip("\"'`").strip() if reply else ""
    if not line or line.lower().rstrip(".") in _EMPTY:
        return None
    if len(line) > 300 or line.lower() == "arbisoft":
        return None
    if line.lower() not in source.lower():
        return None
    return line
