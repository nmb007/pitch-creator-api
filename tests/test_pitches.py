"""A pitch accepts one message or one file."""

from src.auth import service as auth_service


async def _token(client) -> str:  # noqa: ANN001
    auth_service.ensure_user(
        username="person@example.com",
        password="correct-password",
        name="Person",
    )
    response = await client.post(
        "/v1/auth/login",
        json={"username": "person@example.com", "password": "correct-password"},
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
