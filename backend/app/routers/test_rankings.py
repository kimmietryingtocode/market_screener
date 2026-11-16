from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.repositories.scores_repository import RankingRow
from app.routers import rankings
from app.core.security import current_user_id


class FakeDatabase:
    @contextmanager
    def connection(self):
        yield object()


def test_rankings_are_public_and_return_the_frontend_contract(monkeypatch):
    def fake_get_rankings(connection, **filters):
        assert filters == {"limit": 5, "sector": None, "methodology_version": None}
        return [
            RankingRow(
                ticker="AAPL",
                company_name="Apple Inc.",
                sector="Technology",
                score_date=date(2026, 10, 30),
                momentum_score=91.0,
                fundamental_score=74.1,
                composite_score=84.2,
                components={},
                methodology_version="v1",
            )
        ]

    monkeypatch.setattr(rankings.ranking_service, "get_rankings", fake_get_rankings)
    app = FastAPI()
    app.state.db = FakeDatabase()
    app.include_router(rankings.router)

    response = TestClient(app).get("/api/v1/rankings?limit=5")

    assert response.status_code == 200
    assert response.json()[0] == {
        "rank": 1,
        "ticker": "AAPL",
        "company_name": "Apple Inc.",
        "sector": "Technology",
        "score_date": "2026-10-30",
        "momentum_score": 91.0,
        "fundamental_score": 74.1,
        "composite_score": 84.2,
        "components": {},
        "methodology_version": "v1",
    }


def test_factset_upload_is_authenticated_and_returns_validated_rankings(monkeypatch):
    row = RankingRow(
        ticker="AAPL",
        company_name="Apple Inc.",
        sector="Technology",
        score_date=date(2026, 10, 30),
        momentum_score=91.0,
        fundamental_score=74.1,
        composite_score=91.0,
        components={"ranking_stage": "factset_validated"},
        methodology_version="factset-v1",
    )
    monkeypatch.setattr(
        rankings.factset_service,
        "import_summaries",
        lambda source: SimpleNamespace(success=True, imported_count=1, error_count=0, warning_count=0),
    )
    monkeypatch.setattr(rankings.factset_service, "validation_messages", lambda result: [])
    monkeypatch.setattr(rankings.recommendations_repository, "top_ranked", lambda **filters: [row])

    app = FastAPI()
    app.dependency_overrides[current_user_id] = lambda: 7
    app.include_router(rankings.router)

    response = TestClient(app).post(
        "/api/v1/rankings/factset",
        files=[("files", ("summary.csv", b"Financial Statements Summary", "text/csv"))],
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["rankings"][0]["ticker"] == "AAPL"
