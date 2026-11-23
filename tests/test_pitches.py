"""A pitch accepts one message or one file."""

from uuid import UUID

from src.auth import service as auth_service
from src.pitches import service as pitch_service


async def _token(client, *, username: str = "person@example.com") -> str:  # noqa: ANN001
    auth_service.ensure_user(
        username=username,
        password="correct-password",
        name="Person",
    )
    response = await client.post(
        "/v1/auth/login",
        json={"username": username, "password": "correct-password"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


async def test_message_and_file_rejected(client) -> None:  # noqa: ANN001
    token = await _token(client)
    response = await client.post(
        "/v1/pitches",
        headers={"Authorization": f"Bearer {token}"},
        data={"message": "Please pitch this prospect"},
        files={"file": ("notes.txt", b"Prospect notes", "text/plain")},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Send a message or a file, not both"


async def test_json_message_and_file_rejected(client) -> None:  # noqa: ANN001
    token = await _token(client)
    response = await client.post(
        "/v1/pitches",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": "Please pitch this prospect", "file": "notes.txt"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Send a message or a file, not both"


async def test_list_pitches_for_current_user(client) -> None:  # noqa: ANN001
    token = await _token(client, username="list-pitches@example.com")
    me = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    user_id = UUID(me.json()["id"])

    other = auth_service.ensure_user(
        username="other-list@example.com",
        password="correct-password",
        name="Other",
    )
    pitch_service.create_from_message(user_id=user_id, message="My first pitch chat")
    pitch_service.create_from_message(user_id=user_id, message="My second pitch chat")
    pitch_service.create_from_message(user_id=other.id, message="Someone else's pitch")

    response = await client.get("/v1/pitches", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 2
    assert {row["title"] for row in rows} == {"My first pitch chat", "My second pitch chat"}
    assert rows[0]["updated_at"] >= rows[1]["updated_at"]
