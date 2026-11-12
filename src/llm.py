"""One chat-model factory. Swapping the frontier model is a settings change."""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from src.config import settings


def chat_model() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.GROQ_MODEL,
        api_key=settings.GROQ_API_KEY or "missing",
        base_url=settings.GROQ_BASE_URL,
        temperature=0,
    )
