"""The analysis workflow end to end, against a real database, with the
model stubbed.

test_workflow.py only exercises _report_errors on hand-built dicts, so two
defects shipped green:

  - workflow.py called scores_repository.latest_score_date(), which has
    never existed, so every kind="stock" report died with an AttributeError
  - the default model name had been retired by the provider

Neither needs a model to catch. The graph reaches the generate step after
real SQL has run, so stubbing the one call that costs money exercises every
query on the way there and still runs offline in CI.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.ai import workflow
from app.ai.conftest import requires_db  # noqa: F401


class _FakeStructured:
    """Stands in for model.with_structured_output(...)."""

    def __init__(self, payload):
        self._payload = payload

    def invoke(self, prompt, **kwargs):
        self._payload["prompt_seen"] = prompt
        return {"parsed": self._payload["report"], "raw": None, "parsing_error": None}


class _FakeModel:
    def __init__(self, payload):
        self._payload = payload

    def with_structured_output(self, *args, **kwargs):
        return _FakeStructured(self._payload)


@pytest.fixture
def stub_model(monkeypatch):
    """Replace only the provider call. Everything before it is real."""
    payload = {"report": None, "prompt_seen": None}

    def _install(report: dict):
        payload["report"] = report
        monkeypatch.setattr(workflow, "_chat_model", lambda *, num_predict: _FakeModel(payload))
        return payload

    return _install


@pytest.fixture
def scored_security(connection):
    """A security whose score row is the latest scoring run in the database.

    load_facts resolves the newest run across every security, so the date
    has to beat whatever else the database already holds — a fixed date
    passes on an empty database and silently finds nothing on a populated
    one.
    """
    ticker = f"T{uuid.uuid4().hex[:6].upper()}"
    with connection.cursor() as cursor:
        cursor.execute("SELECT coalesce(max(score_date), DATE '2020-01-01') + 1 FROM scores")
        score_date = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO securities (ticker, company_name, sector) VALUES (%s, %s, 'Technology') RETURNING id",
            (ticker, f"{ticker} Corporation"),
        )
        security_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO scores (security_id, score_date, momentum_score, fundamental_score, "
            "composite_score, components, methodology_version) "
            "VALUES (%s, %s, 90.0, 50.0, 70.0, '{}'::jsonb, 'scores-v1')",
            (security_id, score_date),
        )
    yield SimpleNamespace(ticker=ticker, security_id=security_id, score_date=score_date)
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM securities WHERE id = %s", (security_id,))


@requires_db
def test_a_stock_report_runs_every_query_on_the_way_to_the_model(
    connection, make_user, scored_security, stub_model, monkeypatch
):
    """The regression test for latest_score_date.

    load_facts resolves the latest scoring run and reads the score for one
    ticker. Calling a repository function that does not exist raises here,
    before any model is involved, and no stub can hide it.
    """
    monkeypatch.setenv("AI_PROVIDER", "ollama")  # no API key, no network
    ticker = scored_security.ticker
    context = {
        "user_id": make_user(), "kind": "stock", "ticker": ticker,
        "backtest_id": None, "facts": None, "provider": "ollama",
        "data_policy": "private_allowed", "message": None, "history": [],
    }
    stub_model({
        "title": f"{ticker} analysis",
        "summary": f"{ticker} is covered by the current scoring run.",
        "sections": [{"title": "Evidence", "body": f"{ticker} appears in the published scores.", "citation_keys": []}],
        "limitations": [],
    })

    facts, result, citations = workflow.run_analysis(connection, context)

    assert facts["kind"] == "stock"
    assert facts["ticker"] == ticker
    # The score came back from the database, not from the stub.
    assert facts["score"] is not None, "load_facts did not resolve the latest scoring run"
    assert facts["score"]["composite_score"] == 70.0
    assert facts["score"]["methodology_version"] == "scores-v1"
    assert result["title"] == f"{ticker} analysis"
    assert isinstance(citations, list)


@requires_db
def test_facts_resolve_one_methodology_version_not_a_mix(
    connection, make_user, scored_security, stub_model, monkeypatch
):
    """Two versions scored on the same day must not merge.

    latest_run returns a (score_date, methodology_version) pair for exactly
    this reason. Resolving on the date alone puts the same ticker in twice
    with scores produced by different methodologies.
    """
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    ticker = scored_security.ticker
    with connection.cursor() as cursor:
        # Same date, newer methodology, different composite.
        cursor.execute(
            "INSERT INTO scores (security_id, score_date, momentum_score, fundamental_score, "
            "composite_score, components, methodology_version) "
            "VALUES (%s, %s, 10.0, 10.0, 11.0, '{}'::jsonb, 'scores-v2')",
            (scored_security.security_id, scored_security.score_date),
        )

    context = {
        "user_id": make_user(), "kind": "stock", "ticker": ticker,
        "backtest_id": None, "facts": None, "provider": "ollama",
        "data_policy": "private_allowed", "message": None, "history": [],
    }
    stub_model({
        "title": f"{ticker} analysis",
        "summary": f"{ticker} is covered.",
        "sections": [{"title": "Evidence", "body": f"{ticker} is scored.", "citation_keys": []}],
        "limitations": [],
    })

    facts, _, _ = workflow.run_analysis(connection, context)

    score = facts["score"]
    assert score["methodology_version"] == "scores-v2", "must pick one run, the newest"
    assert score["composite_score"] == 11.0


@requires_db
def test_an_ungrounded_report_is_rejected_rather_than_stored(
    connection, make_user, scored_security, stub_model, monkeypatch
):
    """A model that invents a number must fail the job, not persist it.

    This is the guarantee the whole validation layer exists for, and it was
    never exercised against the real graph.
    """
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    ticker = scored_security.ticker
    context = {
        "user_id": make_user(), "kind": "stock", "ticker": ticker,
        "backtest_id": None, "facts": None, "provider": "ollama",
        "data_policy": "private_allowed", "message": None, "history": [],
    }
    stub_model({
        "title": f"{ticker} analysis",
        "summary": f"{ticker} returned 43.7% last quarter.",  # 43.7 is nowhere in the facts
        "sections": [{"title": "Evidence", "body": f"{ticker} looks strong.", "citation_keys": []}],
        "limitations": [],
    })

    with pytest.raises(ValueError) as caught:
        workflow.run_analysis(connection, context)

    assert "43.7" in str(caught.value)
