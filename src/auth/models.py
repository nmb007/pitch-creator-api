"""Auth persistence models."""

from sqlmodel import Field

from src.models import TimestampModel, UUIDModel


class User(UUIDModel, TimestampModel, table=True):
    __tablename__ = "users"

    username: str = Field(unique=True, index=True, max_length=320)
    password_hash: str = Field(max_length=255)
    name: str = Field(max_length=200)
    slack_user_id: str | None = Field(default=None, unique=True, max_length=64)
