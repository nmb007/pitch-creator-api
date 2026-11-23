"""Pitch create and follow-up return SSE turns."""

from src.auth import service as auth_service


async def _token(client) -> str:  # noqa: ANN001
    auth_service.ensure_user(
        username="sse@example.com",
        password="correct-password",
        name="SSE Person",
    )
    response = await client.post(
        "/v1/auth/login",
        json={"username": "sse@example.com", "password": "correct-password"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def _parse_sse(body: str) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        event = "message"
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if data_lines:
            events.append((event, data_lines[0]))
    return events


async def test_create_pitch_streams_ready_and_done(client) -> None:  # noqa: ANN001
    token = await _token(client)
    response = await client.post(
        "/v1/pitches",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": "Hello there"},
    )
    assert response.status_code == 201
    assert "text/event-stream" in response.headers.get("content-type", "")
    events = _parse_sse(response.text)
    names = [name for name, _payload in events]
    assert "ready" in names
    assert "message.user" in names
    assert "assistant.message" in names
    assert "done" in names
    assert "matches" not in names


async def test_follow_up_message_streams(client) -> None:  # noqa: ANN001
    token = await _token(client)
    created = await client.post(
        "/v1/pitches",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": "Hello there"},
    )
    assert created.status_code == 201
    ready = next(payload for name, payload in _parse_sse(created.text) if name == "ready")
    import json

    pitch_id = json.loads(ready)["pitch_id"]
    follow = await client.post(
        f"/v1/pitches/{pitch_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": "Still just saying hi again"},
    )
    assert follow.status_code == 200
    names = [name for name, _payload in _parse_sse(follow.text)]
    assert "message.user" in names
    assert "assistant.message" in names
    assert "done" in names

    listed = await client.get(
        f"/v1/pitches/{pitch_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) >= 3
    assert rows[0]["role"] == "user"
    assert any(row["role"] == "assistant" for row in rows)


async def test_get_pitch_has_title_not_status(client) -> None:  # noqa: ANN001
    token = await _token(client)
    created = await client.post(
        "/v1/pitches",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": "Hello there"},
    )
    import json

    pitch_id = json.loads(
        next(payload for name, payload in _parse_sse(created.text) if name == "ready")
    )["pitch_id"]
    pitch = await client.get(
        f"/v1/pitches/{pitch_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert pitch.status_code == 200
    body = pitch.json()
    assert body["title"] == "Hello there"
    assert "status" not in body
    assert "prospect_text" not in body
