"""Reusable chat LLM access. Switch provider with settings; Groq uses LangChain."""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from src.config import settings

logger = logging.getLogger(__name__)

Provider = Literal["groq", "openai", "local"]
Role = Literal["default", "pitch", "structure"]


def chat_model() -> BaseChatModel:
    """Pitch / general chat model (Groq OpenAI-compatible via LangChain)."""
    return get_chat_model("pitch")


def get_chat_model(role: Role = "default") -> BaseChatModel:
    """Return a chat model for ``role``. Provider comes from settings."""
    provider = provider_for(role)
    if provider in {"groq", "openai"}:
        return _openai_compatible_model(role, provider)
    if provider == "local":
        raise RuntimeError(
            "Local GGUF chat is only used through complete_json(provider=local)."
        )
    raise ValueError(f"Unknown LLM provider {provider!r}")


def provider_for(role: Role = "default") -> Provider:
    if role == "structure":
        return _as_provider(settings.STRUCTURE_LLM_PROVIDER)
    # Pitch and default stay on Groq unless a broader switch is added later.
    return "groq"


def model_name_for(role: Role = "default") -> str:
    provider = provider_for(role)
    if role == "structure" and settings.STRUCTURE_LLM_MODEL.strip():
        return settings.STRUCTURE_LLM_MODEL.strip()
    if provider == "openai":
        return settings.OPENAI_MODEL
    if provider == "local":
        return settings.STRUCTURE_MODEL_FILE
    return settings.GROQ_MODEL


def complete_json(
    *,
    system: str,
    user: str,
    schema: dict[str, Any],
    role: Role = "structure",
) -> dict[str, Any]:
    """Run a JSON completion. Shape should match ``schema``."""
    provider = provider_for(role)
    effort = (
        settings.STRUCTURE_LLM_REASONING_EFFORT.strip()
        if role == "structure"
        else ""
    )
    max_tokens = settings.STRUCTURE_LLM_MAX_TOKENS if role == "structure" else 0
    logger.info(
        "LLM complete_json role=%s provider=%s model=%s%s%s",
        role,
        provider,
        model_name_for(role),
        f" reasoning_effort={effort}" if effort else "",
        f" max_tokens={max_tokens}" if max_tokens else "",
    )
    if provider == "local":
        return _complete_json_local(system=system, user=user, schema=schema)
    return _complete_json_langchain(
        system=system,
        user=user,
        schema=schema,
        role=role,
    )


def _as_provider(value: str) -> Provider:
    name = (value or "groq").strip().lower()
    if name not in {"groq", "openai", "local"}:
        raise ValueError(
            f"LLM provider must be groq, openai, or local; got {value!r}"
        )
    return name  # type: ignore[return-value]


def _openai_compatible_model(role: Role, provider: Provider) -> ChatOpenAI:
    if provider == "openai":
        api_key = settings.OPENAI_API_KEY
        base_url = settings.OPENAI_BASE_URL
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required for provider=openai")
    else:
        api_key = settings.GROQ_API_KEY
        base_url = settings.GROQ_BASE_URL
        if not api_key:
            raise RuntimeError("GROQ_API_KEY is required for provider=groq")
    kwargs: dict[str, Any] = {
        "model": model_name_for(role),
        "api_key": api_key,
        "base_url": base_url,
        "temperature": 0,
    }
    # Ollama OpenAI-compat: reasoning_effort selects Qwen thinking level.
    if role == "structure":
        effort = settings.STRUCTURE_LLM_REASONING_EFFORT.strip()
        if effort:
            kwargs["reasoning_effort"] = effort
        if settings.STRUCTURE_LLM_MAX_TOKENS > 0:
            kwargs["max_tokens"] = settings.STRUCTURE_LLM_MAX_TOKENS
    return ChatOpenAI(**kwargs)


def _complete_json_langchain(
    *,
    system: str,
    user: str,
    schema: dict[str, Any],
    role: Role,
) -> dict[str, Any]:
    model = get_chat_model(role)
    # json_mode works on Groq and other OpenAI-compatible APIs.
    structured = model.with_structured_output(schema, method="json_mode")
    reply = structured.invoke(
        [SystemMessage(content=system), HumanMessage(content=user)]
    )
    if isinstance(reply, dict):
        return reply
    if hasattr(reply, "model_dump"):
        return reply.model_dump()
    raise ValueError("Structured LLM reply was not a JSON object")


def _complete_json_local(
    *,
    system: str,
    user: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    response = _local_llama().create_chat_completion(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={
            "type": "json_object",
            "schema": schema,
        },
        temperature=0,
    )
    content = response["choices"][0]["message"]["content"]
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Local structure model returned empty content")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("Local structure model returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Local structure model JSON must be an object")
    return payload


@lru_cache(maxsize=1)
def _local_llama():  # noqa: ANN202
    from llama_cpp import Llama

    logger.info(
        "Local LLM: loading %s from %s (first use may download)",
        settings.STRUCTURE_MODEL_FILE,
        settings.STRUCTURE_MODEL_REPO,
    )
    try:
        _ensure_gguf_shards(settings.STRUCTURE_MODEL_REPO, settings.STRUCTURE_MODEL_FILE)
        model = Llama.from_pretrained(
            repo_id=settings.STRUCTURE_MODEL_REPO,
            filename=settings.STRUCTURE_MODEL_FILE,
            chat_format="chatml",
            n_ctx=8192,
            n_threads=settings.STRUCTURE_MODEL_THREADS,
            verbose=False,
        )
    except Exception:
        logger.exception("Failed to load local model %s", settings.STRUCTURE_MODEL_FILE)
        raise RuntimeError(
            f"Could not load {settings.STRUCTURE_MODEL_FILE}. Import failed."
        ) from None
    logger.info("Local LLM: loaded")
    return model


def _ensure_gguf_shards(repo_id: str, filename: str) -> None:
    """Download every part of a split GGUF. from_pretrained only fetches the named file."""
    from huggingface_hub import hf_hub_download

    hf_hub_download(repo_id=repo_id, filename=filename)
    match = re.search(r"-(\d+)-of-(\d+)\.gguf$", filename, re.IGNORECASE)
    if match is None:
        return
    total = int(match.group(2))
    for part in range(1, total + 1):
        part_name = re.sub(
            r"-\d+-of-\d+\.gguf$",
            f"-{part:05d}-of-{total:05d}.gguf",
            filename,
            count=1,
            flags=re.IGNORECASE,
        )
        logger.info("Local LLM: ensuring GGUF shard %s", part_name)
        hf_hub_download(repo_id=repo_id, filename=part_name)
