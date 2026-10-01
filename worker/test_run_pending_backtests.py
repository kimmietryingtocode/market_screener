"""Tests for worker/build_targets.py and worker/run_pending_backtests.py.

Same convention as test_persistence.py: needs TEST_DATABASE_URL pointing at
a Postgres instance with migrations 001-004 applied, and skips itself
otherwise so `pytest worker/` stays runnable without a database.
"""

from __future__ import annotations

import os
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from backtest.calendar import nyse_sessions
from worker import build_targets, ingest_prices, run_pending_backtests

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None


def _db_available() -> bool:
    if not TEST_DATABASE_URL or psycopg is None:
        return False
    try:
        with psycopg.connect(TEST_DATABASE_URL, connect_timeout=2):
            return True
    except Exception:
        return False


DB_AVAILABLE = _db_available()
REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_PATH = REPO_ROOT / "backtest" / "build" / "portfolio_backtest"

START_DATE = date(2025, 1, 2)
END_DATE = date(2025, 2, 28)
TICKERS = ("TAAPL", "TMSFT", "TBENCH")
TEST_ONLY_TICKERS = (*TICKERS, "TCHANGED", "TOTHER")


@unittest.skipUnless(DB_AVAILABLE, "TEST_DATABASE_URL not set or unreachable")
class BuildTargetsAndPollerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = psycopg.connect(TEST_DATABASE_URL, autocommit=True)
        self.addCleanup(self.connection.close)
        self.addCleanup(self._cleanup)

        with self.connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (email, password_hash) VALUES (%s, 'x') RETURNING id",
                (f"poller-test-{id(self)}@example.com",),
            )
            self.user_id = cursor.fetchone()[0]

            cursor.execute(
                "INSERT INTO portfolios (user_id, name) VALUES (%s, 'Poller Test Portfolio') RETURNING id",
                (self.user_id,),
            )
            self.portfolio_id = cursor.fetchone()[0]

            cursor.executemany(
                "INSERT INTO holdings (portfolio_id, ticker, weight) VALUES (%s, %s, %s)",
                [(self.portfolio_id, "TAAPL", 0.6), (self.portfolio_id, "TMSFT", 0.4)],
            )

            cursor.execute(
                "INSERT INTO securities (ticker) VALUES ('TAAPL'), ('TMSFT'), ('TBENCH') "
                "ON CONFLICT (ticker) DO NOTHING"
            )
            cursor.execute("SELECT id, ticker FROM securities WHERE ticker = ANY(%s)", (list(TICKERS),))
            self.security_ids = {ticker: sid for sid, ticker in cursor.fetchall()}

            sessions = nyse_sessions(START_DATE, END_DATE)["date"]
            rows = []
            for offset, ticker in enumerate(TICKERS):
                base_price = 100.0 + offset * 50.0
                for day_index, session in enumerate(sessions):
                    rows.append((
                        self.security_ids[ticker],
                        session.date(),
                        base_price + day_index * 0.1,
                    ))
            cursor.executemany(
                "INSERT INTO price_bars (security_id, trading_date, adjusted_close) VALUES (%s, %s, %s)",
                rows,
            )

    def _cleanup(self) -> None:
        with self.connection.cursor() as cursor:
            # backtest_targets.security_id is ON DELETE RESTRICT, so any
            # backtests/backtest_targets rows created by a test must go
            # before the securities they reference, or the securities
            # delete fails and leaves everything orphaned for the next test.
            cursor.execute("DELETE FROM backtests WHERE portfolio_id = %s", (self.portfolio_id,))
            cursor.execute("DELETE FROM securities WHERE ticker = ANY(%s)", (list(TEST_ONLY_TICKERS),))
            cursor.execute("DELETE FROM users WHERE id = %s", (self.user_id,))

    def _create_backtest(self, *, parameters: dict | None = None, **overrides) -> int:
        import json as _json

        params = {
            "portfolio_id": self.portfolio_id,
            "benchmark": "TBENCH",
            "start_date": START_DATE,
            "end_date": END_DATE,
            "rebalance_frequency": "monthly",
        }
        params.update(overrides)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO backtests
                    (user_id, portfolio_id, status, start_date, end_date, benchmark, rebalance_frequency, parameters)
                VALUES (%s, %s, 'queued', %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    self.user_id,
                    params["portfolio_id"],
                    params["start_date"],
                    params["end_date"],
                    params["benchmark"],
                    params["rebalance_frequency"],
                    _json.dumps(parameters or {}),
                ),
            )
            backtest_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO backtest_holdings (backtest_id, ticker, weight)
                SELECT %s, ticker, weight FROM holdings WHERE portfolio_id = %s
                """,
                (backtest_id, self.portfolio_id),
            )
            return backtest_id

    def test_generate_targets_from_portfolio_expands_holdings_across_signals(self) -> None:
        backtest_id = self._create_backtest()
        row_count = build_targets.generate_targets_from_portfolio(self.connection, backtest_id)

        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT s.ticker, t.target_weight, t.provenance_id FROM backtest_targets t "
                "JOIN securities s ON s.id = t.security_id WHERE t.backtest_id = %s ORDER BY s.ticker",
                (backtest_id,),
            )
            rows = cursor.fetchall()

        # Initial purchase plus one monthly rebalance -> 2 holdings x 2 dates.
        self.assertEqual(row_count, 4)
        self.assertEqual(len(rows), 4)
        tickers = {r[0] for r in rows}
        self.assertEqual(tickers, {"TAAPL", "TMSFT"})
        for _, weight, provenance in rows:
            self.assertIn(float(weight), (0.6, 0.4))
            self.assertEqual(provenance, "saved-backtest-holdings")

    def test_generate_targets_invests_on_first_session(self) -> None:
        backtest_id = self._create_backtest()
        build_targets.generate_targets_from_portfolio(self.connection, backtest_id)

        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT min(execution_date) FROM backtest_targets WHERE backtest_id = %s",
                (backtest_id,),
            )
            first_execution = cursor.fetchone()[0]

        self.assertEqual(first_execution, START_DATE)

    def test_generate_targets_uses_snapshot_after_portfolio_is_edited(self) -> None:
        backtest_id = self._create_backtest()
        with self.connection.cursor() as cursor:
            cursor.execute("DELETE FROM holdings WHERE portfolio_id = %s", (self.portfolio_id,))
            cursor.execute(
                "INSERT INTO holdings (portfolio_id, ticker, weight) VALUES (%s, 'TCHANGED', 1)",
                (self.portfolio_id,),
            )

        build_targets.generate_targets_from_portfolio(self.connection, backtest_id)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT s.ticker FROM backtest_targets t
                JOIN securities s ON s.id = t.security_id
                WHERE t.backtest_id = %s ORDER BY s.ticker
                """,
                (backtest_id,),
            )
            tickers = [ticker for (ticker,) in cursor.fetchall()]
        self.assertEqual(tickers, ["TAAPL", "TMSFT"])

    def test_missing_history_is_fetched_for_selected_ticker_outside_starter_list(self) -> None:
        backtest_id = self._create_backtest()
        with self.connection.cursor() as cursor:
            cursor.execute("DELETE FROM backtest_holdings WHERE backtest_id = %s", (backtest_id,))
            cursor.execute(
                "INSERT INTO backtest_holdings (backtest_id, ticker, weight) VALUES (%s, 'TOTHER', 1)",
                (backtest_id,),
            )

        fetched: list[str] = []

        def fake_ingest_range(connection, tickers, start, end):
            fetched.extend(tickers)
            sessions = nyse_sessions(start, end)["date"]
            frame = pd.DataFrame([
                {
                    "ticker": ticker,
                    "trading_date": session.date(),
                    "open": 100.0,
                    "high": 100.0,
                    "low": 100.0,
                    "close": 100.0,
                    "volume": 1_000,
                }
                for ticker in tickers
                for session in sessions
            ])
            return ingest_prices.upsert_price_bars(connection, frame)

        with patch("worker.ingest_prices.ingest_range", side_effect=fake_ingest_range):
            ingest_prices.ensure_backtest_price_history(self.connection, backtest_id)

        self.assertEqual(fetched, ["TOTHER"])

    def test_incomplete_history_fails_without_shortening_requested_dates(self) -> None:
        backtest_id = self._create_backtest()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM price_bars
                WHERE security_id = %s AND trading_date = %s
                """,
                (self.security_ids["TAAPL"], START_DATE),
            )

        with patch("worker.ingest_prices.ingest_range", return_value=0):
            with self.assertRaisesRegex(
                ingest_prices.PriceCoverageError,
                rf"{START_DATE} through {END_DATE}.*TAAPL: 1 missing session",
            ):
                ingest_prices.ensure_backtest_price_history(self.connection, backtest_id)

    def test_generate_targets_is_idempotent(self) -> None:
        backtest_id = self._create_backtest()
        first = build_targets.generate_targets_from_portfolio(self.connection, backtest_id)
        second = build_targets.generate_targets_from_portfolio(self.connection, backtest_id)
        self.assertEqual(first, second)

        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM backtest_targets WHERE backtest_id = %s", (backtest_id,)
            )
            self.assertEqual(cursor.fetchone()[0], second)

    def test_generate_targets_rejects_unsupported_frequency(self) -> None:
        backtest_id = self._create_backtest(rebalance_frequency="weekly")
        with self.assertRaisesRegex(ValueError, "unsupported rebalance_frequency"):
            build_targets.generate_targets_from_portfolio(self.connection, backtest_id)

    def test_run_once_with_no_queued_backtests_is_a_noop(self) -> None:
        processed = run_pending_backtests.run_once(self.connection)
        self.assertEqual(processed, [])

    def test_run_once_marks_unsupported_frequency_as_failed_without_crashing(self) -> None:
        backtest_id = self._create_backtest(rebalance_frequency="weekly")
        processed = run_pending_backtests.run_once(self.connection)
        self.assertEqual(processed, [backtest_id])

        with self.connection.cursor() as cursor:
            cursor.execute("SELECT status FROM backtests WHERE id = %s", (backtest_id,))
            self.assertEqual(cursor.fetchone()[0], "failed")

    @unittest.skipUnless(ENGINE_PATH.is_file(), "C++ engine not built: run `make -C backtest`")
    def test_run_once_processes_a_queued_backtest_end_to_end(self) -> None:
        backtest_id = self._create_backtest()

        with TemporaryDirectory() as tmp:
            processed = run_pending_backtests.run_once(
                self.connection, engine=ENGINE_PATH, output_root=Path(tmp)
            )
            self.assertEqual(processed, [backtest_id])

            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT status, job_id FROM backtests WHERE id = %s", (backtest_id,)
                )
                status, job_id = cursor.fetchone()
                cursor.execute(
                    "SELECT total_return, sharpe_ratio FROM backtest_results WHERE backtest_id = %s",
                    (backtest_id,),
                )
                metrics_row = cursor.fetchone()
                cursor.execute(
                    "SELECT count(*) FROM equity_curve_points WHERE backtest_id = %s", (backtest_id,)
                )
                equity_row_count = cursor.fetchone()[0]

        self.assertEqual(status, "completed")
        self.assertIsNotNone(job_id)
        self.assertIsNotNone(metrics_row)
        self.assertGreater(equity_row_count, 0)

    @unittest.skipUnless(ENGINE_PATH.is_file(), "C++ engine not built: run `make -C backtest`")
    def test_run_once_passes_cost_bps_from_parameters_to_the_engine(self) -> None:
        """A queued backtest's parameters JSONB must actually reach the engine,
        not just get accepted and silently ignored."""
        free_id = self._create_backtest(parameters={"cost_bps": 0})
        costly_id = self._create_backtest(parameters={"cost_bps": 500})

        with TemporaryDirectory() as tmp:
            run_pending_backtests.run_once(self.connection, engine=ENGINE_PATH, output_root=Path(tmp))

            def total_return(backtest_id: int) -> float:
                with self.connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT total_return FROM backtest_results WHERE backtest_id = %s", (backtest_id,)
                    )
                    return float(cursor.fetchone()[0])

            free_return = total_return(free_id)
            costly_return = total_return(costly_id)

        self.assertGreater(free_return, costly_return)


if __name__ == "__main__":
    unittest.main()
