"""Auth sync domain services."""

from uuid import UUID

from sqlmodel import Session, select

from src.auth.exceptions import InvalidCredentials
from src.auth.models import User
from src.auth.utils import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from src.database import get_sync_session


def get_user_by_username(session: Session, username: str) -> User | None:
    return session.exec(select(User).where(User.username == username)).first()


def get_user_by_id(session: Session, user_id: UUID) -> User | None:
    return session.get(User, user_id)


def authenticate(username: str, password: str) -> str:
    with get_sync_session() as session:
        user = get_user_by_username(session, username.lower())
        if user is None or not verify_password(password, user.password_hash):
            raise InvalidCredentials()
        return create_access_token(user_id=user.id, email=user.username)


def get_user_from_token(token: str) -> User:
    payload = decode_access_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise InvalidCredentials()
    with get_sync_session() as session:
        user = get_user_by_id(session, UUID(user_id))
        if user is None:
            raise InvalidCredentials()
        session.expunge(user)
        return user


def ensure_user(*, username: str, password: str, name: str) -> User:
    with get_sync_session() as session:
        existing = get_user_by_username(session, username.lower())
        if existing is not None:
            return existing
        user = User(
            username=username.lower(),
            password_hash=hash_password(password),
            name=name,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        session.expunge(user)
        return user
