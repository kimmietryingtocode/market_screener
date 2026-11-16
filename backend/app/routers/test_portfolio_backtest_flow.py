"""Proof of the frontend-facing two-stock API flow."""

from __future__ import annotations

import os
import uuid
from datetime import date
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.conftest import requires_db
from backtest.calendar import nyse_sessions
from worker.run_pending_backtests import run_one


@pytest.fixture(autouse=True)
def no_redis(monkeypatch):
    monkeypatch.setattr("app.core.rate_limit.get_client", lambda: None)


@pytest.fixture
def connection():
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as conn:
        yield conn


@requires_db
def test_two_stock_portfolio_runs_through_public_api(connection, tmp_path):
    email = f"api-flow-{uuid.uuid4().hex}@example.com"
    start_date = "2025-01-02"
    end_date = "2025-02-28"
    tickers = ("AAPL", "MSFT", "SPY")

    sessions = nyse_sessions(
        date.fromisoformat(start_date),
        date.fromisoformat(end_date),
    )["date"]
    with connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO securities (ticker) VALUES (%s) ON CONFLICT (ticker) DO NOTHING",
            [(ticker,) for ticker in tickers],
        )
        cursor.execute("SELECT id, ticker FROM securities WHERE ticker = ANY(%s)", (list(tickers),))
        security_ids = {ticker: security_id for security_id, ticker in cursor.fetchall()}
        cursor.executemany(
            """
            INSERT INTO price_bars (security_id, trading_date, adjusted_close)
            VALUES (%s, %s, %s)
            ON CONFLICT (security_id, trading_date) DO UPDATE
            SET adjusted_close = EXCLUDED.adjusted_close
            """,
            [
                (security_ids[ticker], session.date(), 100 + ticker_index * 50 + day_index)
                for ticker_index, ticker in enumerate(tickers)
                for day_index, session in enumerate(sessions)
            ],
        )

    try:
        with TestClient(app) as client:
            registered = client.post(
                "/api/v1/auth/register",
                json={"email": email, "password": "correct-horse-battery-staple"},
            )
            assert registered.status_code == 201
            headers = {"Authorization": f"Bearer {registered.json()['token']}"}

            portfolio = client.post(
                "/api/v1/portfolios",
                headers=headers,
                json={
                    "name": "Apple and Microsoft",
                    "holdings": [
                        {"ticker": "AAPL", "weight": 0.5},
                        {"ticker": "MSFT", "weight": 0.5},
                    ],
                },
            )
            assert portfolio.status_code == 201
            portfolio_id = portfolio.json()["id"]

            listed = client.get("/api/v1/portfolios", headers=headers)
            assert {holding["ticker"] for holding in listed.json()[0]["holdings"]} == {
                "AAPL",
                "MSFT",
            }

            requested = client.post(
                "/api/v1/backtests",
                headers=headers,
                json={
                    "portfolio_id": portfolio_id,
                    "benchmark": "SPY",
                    "start_date": start_date,
                    "end_date": end_date,
                    "rebalance_frequency": "monthly",
                    "cost_bps": 10,
                    "initial_capital": 100_000,
                },
            )
            assert requested.status_code == 202
            backtest_id = requested.json()["id"]

            run_one(
                connection,
                backtest_id,
                engine=Path("backtest/build/portfolio_backtest").resolve(),
                output_root=tmp_path,
            )

            result = client.get(f"/api/v1/backtests/{backtest_id}", headers=headers)
            assert result.status_code == 200
            assert result.json()["status"] == "completed"
            assert result.json()["metrics"]["total_return"] is not None
            assert len(result.json()["equity_curve"]) == len(sessions)
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
            user = cursor.fetchone()
            if user:
                cursor.execute("DELETE FROM backtests WHERE user_id = %s", (user[0],))
                cursor.execute("DELETE FROM users WHERE id = %s", (user[0],))
            cursor.execute("DELETE FROM securities WHERE ticker = ANY(%s)", (list(tickers),))
