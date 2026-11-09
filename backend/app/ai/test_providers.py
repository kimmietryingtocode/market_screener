import pytest

from app.ai import providers


def test_gemini_requires_api_key(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        providers.ensure_configured()


def test_private_backtest_is_blocked_for_gemini(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("AI_GEMINI_DATA_POLICY", "public_only")

    with pytest.raises(RuntimeError, match="Private backtest"):
        providers.ensure_allowed({"kind": "backtest"})


def test_ollama_allows_private_backtest(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("AI_GEMINI_DATA_POLICY", "public_only")
    providers.ensure_allowed({"kind": "backtest"})


def test_embedding_settings_are_provider_specific(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
    monkeypatch.setenv("GEMINI_EMBEDDING_DIMENSIONS", "768")
    assert providers.embedding_model_name() == "gemini-embedding-001"
    assert providers.embedding_dimensions() == 768
