"""Login rejects a wrong password."""

from src.auth import service as auth_service


async def test_login_rejects_unknown_user(client) -> None:  # noqa: ANN001
    response = await client.post(
        "/v1/auth/login",
        json={"username": "missing@example.com", "password": "not-the-password"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials"


async def test_login_rejects_wrong_password(client) -> None:  # noqa: ANN001
    auth_service.ensure_user(
        username="person@example.com",
        password="correct-password",
        name="Person",
    )
    response = await client.post(
        "/v1/auth/login",
        json={"username": "person@example.com", "password": "wrong-password"},
    )
    assert response.status_code == 401
