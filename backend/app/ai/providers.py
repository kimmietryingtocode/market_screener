"""Provider selection for report generation and RAG embeddings."""

from __future__ import annotations

import os
from typing import Any


def provider() -> str:
    value = os.getenv("AI_PROVIDER", "gemini").strip().lower()
    if value not in {"gemini", "ollama"}:
        raise ValueError("AI_PROVIDER must be gemini or ollama")
    return value


def data_policy() -> str:
    value = os.getenv("AI_GEMINI_DATA_POLICY", "public_only").strip().lower()
    if value not in {"public_only", "private_allowed"}:
        raise ValueError("AI_GEMINI_DATA_POLICY must be public_only or private_allowed")
    return value


def model_name() -> str:
    # gemini-2.5-flash was the default here, and the API now refuses it for
    # any key created after it was retired:
    #   "This model models/gemini-2.5-flash is no longer available to new
    #    users. Please update your code to use models/gemini-3.8-flash"
    # An existing key keeps working, so this only broke for someone setting
    # the project up fresh — which is everyone who clones it.
    return os.getenv("GEMINI_MODEL", "gemini-3.8-flash") if provider() == "gemini" else os.getenv("AI_MODEL", "qwen3:4b")


def embedding_model_name() -> str:
    return os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001") if provider() == "gemini" else os.getenv("AI_EMBEDDING_MODEL", "nomic-embed-text:v1.5")


def embedding_dimensions() -> int:
    return int(os.getenv("GEMINI_EMBEDDING_DIMENSIONS", "768")) if provider() == "gemini" else 768


def version() -> str:
    return f"{provider()}:{model_name()}"


def ensure_configured() -> None:
    if provider() == "gemini" and not (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")):
        raise RuntimeError("Gemini is selected but GOOGLE_API_KEY is not configured")


def ensure_allowed(context: dict[str, Any]) -> None:
    selected_provider = context.get("provider", provider())
    selected_policy = context.get("data_policy", data_policy())
    if selected_provider == "gemini" and selected_policy == "public_only" and context.get("kind") == "backtest":
        raise RuntimeError("Private backtest analysis is disabled for Gemini; set AI_PROVIDER=ollama")


def embeddings() -> Any:
    ensure_configured()
    if provider() == "gemini":
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        return GoogleGenerativeAIEmbeddings(
            model=embedding_model_name(),
            output_dimensionality=embedding_dimensions(),
        )
    from langchain_ollama import OllamaEmbeddings

    return OllamaEmbeddings(
        model=embedding_model_name(),
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
    )


def chat_model(*, num_predict: int) -> Any:
    ensure_configured()
    if provider() == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=model_name(),
            temperature=0,
            max_output_tokens=num_predict,
            timeout=float(os.getenv("AI_MODEL_TIMEOUT_SECONDS", "180")),
            max_retries=1,
        )
    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=model_name(),
        temperature=0,
        reasoning=False,
        disable_streaming=True,
        num_ctx=int(os.getenv("AI_CONTEXT_TOKENS", "4096")),
        num_predict=num_predict,
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        client_kwargs={"timeout": float(os.getenv("AI_MODEL_TIMEOUT_SECONDS", "180"))},
    )
