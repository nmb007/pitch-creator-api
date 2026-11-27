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
    # Optional LangSmith tracing extras (empty is fine when tracing is off).
    LANGSMITH_TRACING: str = ""
    LANGSMITH_ENDPOINT: str = ""
    # Agent addressing (do not also set LANGSMITH_PROJECT).
    LANGSMITH_AGENT_ID: str = ""
    LANGSMITH_AGENT_ENVIRONMENT: str = ""
    SEED_USER_EMAIL: str
    SEED_USER_PASSWORD: str
    SEED_USER_NAME: str
    # Comma-separated browser origins. Empty means no cross-origin browser calls.
    CORS_ALLOW_ORIGINS: str
    # Origin used in assistant messages that link to the generated pitch view.
    WEB_BASE_URL: str = "http://localhost:3000"
    EMBEDDING_MODEL: str
    EMBEDDING_DIMENSIONS: int
    # Hybrid chunk retrieval (browse_studies / recommend_pitch_studies).
    RETRIEVAL_VECTOR_K: int = 40
    RETRIEVAL_KEYWORD_K: int = 40
    RETRIEVAL_FUSED_POOL: int = 40
    RETRIEVAL_MAX_CHUNKS_PER_STUDY: int = 3
    RETRIEVAL_STUDY_LIMIT: int = 15
    RETRIEVAL_RRF_K: int = 60
    RERANK_MODEL: str = "BAAI/bge-reranker-v2-m3"
    # Query+chunk token budget BAAI recommends for v2-m3. Query is char-capped below.
    RERANK_MAX_LENGTH: int = 1024
    RERANK_QUERY_CHARS: int = 1500
    # Drop studies whose best chunk score is below this (raw cross-encoder logits).
    RERANK_MIN_SCORE: float = -6.0
    # Structure ingest LLM: groq | openai | local
    STRUCTURE_LLM_PROVIDER: str
    # Empty uses GROQ_MODEL or the local GGUF file. Required when the provider is openai.
    STRUCTURE_LLM_MODEL: str
    # OpenAI-compat thinking control for Ollama/Qwen (e.g. low, none). Empty omits the field.
    STRUCTURE_LLM_REASONING_EFFORT: str
    # Completion cap for structure calls. 0 omits the field.
    STRUCTURE_LLM_MAX_TOKENS: int
    # Retries after the first structure JSON call. Each wait doubles.
    STRUCTURE_LLM_RETRY_LIMIT: int = 4
    # Seconds before the first retry. Later retries wait twice as long.
    STRUCTURE_LLM_RETRY_BASE_SECONDS: float = 1
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
    # Agent addressing conflicts with legacy project destination vars.
    for legacy in ("LANGSMITH_PROJECT", "LANGCHAIN_PROJECT", "LANGCHAIN_SESSION"):
        os.environ.pop(legacy, None)

    if settings.LANGSMITH_API_KEY:
        tracing = (settings.LANGSMITH_TRACING or "true").strip().lower()
        os.environ["LANGSMITH_TRACING"] = tracing if tracing else "true"
        os.environ["LANGSMITH_API_KEY"] = settings.LANGSMITH_API_KEY
        endpoint = settings.LANGSMITH_ENDPOINT.strip()
        if endpoint:
            os.environ["LANGSMITH_ENDPOINT"] = endpoint
        agent_id = settings.LANGSMITH_AGENT_ID.strip()
        if agent_id:
            os.environ["LANGSMITH_AGENT_ID"] = agent_id
        agent_env = settings.LANGSMITH_AGENT_ENVIRONMENT.strip()
        if agent_env:
            os.environ["LANGSMITH_AGENT_ENVIRONMENT"] = agent_env
        return
    os.environ["LANGSMITH_TRACING"] = "false"
