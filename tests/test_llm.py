"""Exponential backoff when a structure call returns unusable JSON."""

import logging

import pytest
from src.config import settings
from src.llm import complete_json

SCHEMA = {"type": "object"}
REPLY = {"sections": [], "titles": []}


class _Fail(Exception):
    pass


class _Structured:
    def __init__(self, results: list[object]) -> None:
        self.results = results
        self.messages: list[object] = []

    def invoke(self, messages: object) -> object:
        self.messages.append(messages)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _Model:
    def __init__(self, structured: _Structured) -> None:
        self.structured = structured
        self.calls: list[tuple[object, str]] = []

    def with_structured_output(
        self,
        schema: object,
        method: str = "json_schema",
        strict: bool | None = None,
    ) -> _Structured:
        self.calls.append((schema, method, strict))
        return self.structured


@pytest.fixture(autouse=True)
def _retry_clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waits: list[float] = []
    monkeypatch.setattr("src.llm.time.sleep", waits.append)
    monkeypatch.setattr(settings, "STRUCTURE_LLM_RETRY_LIMIT", 2)
    monkeypatch.setattr(settings, "STRUCTURE_LLM_RETRY_BASE_SECONDS", 1.0)
    return waits


def _patch_model(monkeypatch: pytest.MonkeyPatch, results: list[object]) -> _Model:
    structured = _Structured(results)
    model = _Model(structured)
    monkeypatch.setattr("src.llm.get_chat_model", lambda role: model)
    return model


def test_retries_json_validate_failed_once(
    monkeypatch: pytest.MonkeyPatch,
    _retry_clock: list[float],
) -> None:
    model = _patch_model(
        monkeypatch,
        [_Fail("Error code: 400 json_validate_failed"), REPLY],
    )

    payload = complete_json(system="sys", user="user", schema=SCHEMA, role="structure")

    assert payload == REPLY
    assert len(model.structured.messages) == 2
    assert model.structured.messages[0] == model.structured.messages[1]
    assert model.calls == [(SCHEMA, "json_schema", True)]
    assert _retry_clock == [1.0]


def test_second_retry_can_succeed(
    monkeypatch: pytest.MonkeyPatch,
    _retry_clock: list[float],
) -> None:
    model = _patch_model(
        monkeypatch,
        [
            _Fail("json_validate_failed"),
            _Fail("json_validate_failed"),
            REPLY,
        ],
    )

    payload = complete_json(system="sys", user="user", schema=SCHEMA, role="structure")

    assert payload == REPLY
    assert len(model.structured.messages) == 3
    first, second, third = model.structured.messages
    assert first == second == third
    assert _retry_clock == [1.0, 2.0]


def test_backoff_doubles_until_the_retry_limit(
    monkeypatch: pytest.MonkeyPatch,
    _retry_clock: list[float],
) -> None:
    monkeypatch.setattr(settings, "STRUCTURE_LLM_RETRY_LIMIT", 3)
    model = _patch_model(
        monkeypatch,
        [_Fail("json_validate_failed") for _ in range(4)],
    )

    with pytest.raises(_Fail, match="json_validate_failed"):
        complete_json(system="sys", user="user", schema=SCHEMA, role="structure")

    assert len(model.structured.messages) == 4
    assert _retry_clock == [1.0, 2.0, 4.0]


def test_json_validate_failed_after_two_retries_fails_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _patch_model(
        monkeypatch,
        [_Fail("json_validate_failed") for _ in range(3)],
    )

    with pytest.raises(_Fail, match="json_validate_failed"):
        complete_json(system="sys", user="user", schema=SCHEMA, role="structure")

    assert len(model.structured.messages) == 3


def test_retries_non_object_reply_and_logs_it(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    model = _patch_model(monkeypatch, [["not", "an object"], REPLY])

    payload = complete_json(system="sys", user="user", schema=SCHEMA, role="structure")

    assert payload == REPLY
    assert len(model.structured.messages) == 2
    assert "type=list" in caplog.text
    assert "['not', 'an object']" in caplog.text


def test_non_object_reply_after_two_retries_fails_the_call(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    model = _patch_model(monkeypatch, [["bad"], None, "nope"])

    with pytest.raises(ValueError, match="not a JSON object"):
        complete_json(system="sys", user="user", schema=SCHEMA, role="structure")

    assert len(model.structured.messages) == 3
    assert "['bad']" in caplog.text
    assert "None" in caplog.text
    assert "'nope'" in caplog.text


def test_other_errors_are_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _patch_model(monkeypatch, [_Fail("connection reset")])

    with pytest.raises(_Fail, match="connection reset"):
        complete_json(system="sys", user="user", schema=SCHEMA, role="structure")

    assert len(model.structured.messages) == 1
