"""Global application settings."""

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENVIRONMENT: str = "local"
    DATABASE_URL: str = "postgresql+psycopg://postgres:postgres@localhost:5433/pitch"
    CELERY_BROKER_URL: str = "redis://localhost:6380/0"
    STORAGE_DIR: str = "storage"
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    LANGSMITH_API_KEY: str = ""
    LANGSMITH_PROJECT: str = "pitch-creator"
    SEED_USER_EMAIL: str = ""
    SEED_USER_PASSWORD: str = ""
    SEED_USER_NAME: str = "Demo User"
    # Comma-separated browser origins. Empty means no cross-origin browser calls.
    CORS_ALLOW_ORIGINS: str = "http://localhost:3000"
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    EMBEDDING_DIMENSIONS: int = 384


settings = Settings()


def cors_allow_origins() -> list[str]:
    return [origin.strip() for origin in settings.CORS_ALLOW_ORIGINS.split(",") if origin.strip()]


def checkpoint_conninfo() -> str:
    conn = settings.DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)
    if "sslmode=" not in conn:
        conn += ("&" if "?" in conn else "?") + "sslmode=disable"
    return conn


def configure_tracing() -> None:
    """Turn LangSmith on only when a key is present. An empty key must not stop startup."""
    if settings.LANGSMITH_API_KEY:
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_API_KEY"] = settings.LANGSMITH_API_KEY
        os.environ["LANGSMITH_PROJECT"] = settings.LANGSMITH_PROJECT
        return
    os.environ["LANGSMITH_TRACING"] = "false"
