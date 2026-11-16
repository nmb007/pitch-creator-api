"""Global application settings. Values come from the environment / .env."""

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENVIRONMENT: str
    DATABASE_URL: str
    CELERY_BROKER_URL: str
    STORAGE_DIR: str
    GROQ_API_KEY: str
    GROQ_MODEL: str
    GROQ_BASE_URL: str
    OPENAI_API_KEY: str
    OPENAI_BASE_URL: str
    LANGSMITH_API_KEY: str
    LANGSMITH_PROJECT: str
    SEED_USER_EMAIL: str
    SEED_USER_PASSWORD: str
    SEED_USER_NAME: str
    # Comma-separated browser origins. Empty means no cross-origin browser calls.
    CORS_ALLOW_ORIGINS: str
    EMBEDDING_MODEL: str
    EMBEDDING_DIMENSIONS: int
    # Structure ingest LLM: groq | openai | local
    STRUCTURE_LLM_PROVIDER: str
    # Empty uses GROQ_MODEL or the local GGUF file. Required when the provider is openai.
    STRUCTURE_LLM_MODEL: str
    # OpenAI-compat thinking control for Ollama/Qwen (e.g. low, none). Empty omits the field.
    STRUCTURE_LLM_REASONING_EFFORT: str
    # Completion cap for structure calls. 0 omits the field.
    STRUCTURE_LLM_MAX_TOKENS: int
    STRUCTURE_MODEL_REPO: str
    STRUCTURE_MODEL_FILE: str
    STRUCTURE_MODEL_THREADS: int
    STRUCTURE_BATCH_SLIDES: int


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
