"""Drop a reason sentence that states a fact the source quote does not contain."""

from __future__ import annotations

import re

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?%?")
_PROPER = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b")


def ground_reason(reason: str, source: str) -> str:
    kept: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", reason.strip()):
        text = sentence.strip()
        if text and _supported(text, source):
            kept.append(text)
    return " ".join(kept)


def _supported(sentence: str, source: str) -> bool:
    for number in _NUMBER.findall(sentence):
        if number not in source:
            return False
    return all(name in source for name in _PROPER.findall(sentence))
