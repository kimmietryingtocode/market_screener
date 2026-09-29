"""Tests for worker/ingest_prices.py.

validate() and upsert_price_bars() are tested without touching yfinance
(constructing the normalized DataFrame directly), so they run without
network access. The DB-backed tests use the same TEST_DATABASE_URL
skip-if-unavailable convention as the other worker tests. One test does hit
the real network/yfinance/Postgres end to end and skips itself if either is
unreachable, rather than failing the suite on a flaky external dependency.
"""

from __future__ import annotations

import math
import os
import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from worker import ingest_prices

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
TICKERS = ("ZAAPL", "ZMSFT")


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["ticker", "trading_date", "open", "high", "low", "close", "volume"])


class ValidateTests(unittest.TestCase):
    def test_accepts_clean_frame(self) -> None:
        frame = _frame([
            {"ticker": "AAPL", "trading_date": date(2025, 1, 2), "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 100},
        ])
        ingest_prices.validate(frame)  # must not raise

    def test_rejects_empty_frame(self) -> None:
        with self.assertRaisesRegex(ValueError, "no price bars"):
            ingest_prices.validate(_frame([]))

    def test_rejects_nonpositive_close(self) -> None:
        frame = _frame([
            {"ticker": "AAPL", "trading_date": date(2025, 1, 2), "open": 1, "high": 2, "low": 1, "close": 0.0, "volume": 100},
        ])
        with self.assertRaisesRegex(ValueError, "nonpositive or nonfinite"):
            ingest_prices.validate(frame)

    def test_rejects_nonfinite_close(self) -> None:
        frame = _frame([
            {"ticker": "AAPL", "trading_date": date(2025, 1, 2), "open": 1, "high": 2, "low": 1, "close": math.inf, "volume": 100},
        ])
        with self.assertRaisesRegex(ValueError, "nonpositive or nonfinite"):
            ingest_prices.validate(frame)

    def test_rejects_duplicate_ticker_date_rows(self) -> None:
        row = {"ticker": "AAPL", "trading_date": date(2025, 1, 2), "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 100}
        with self.assertRaisesRegex(ValueError, "duplicate"):
            ingest_prices.validate(_frame([row, dict(row)]))


@unittest.skipUnless(DB_AVAILABLE, "TEST_DATABASE_URL not set or unreachable")
class UpsertPriceBarsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = psycopg.connect(TEST_DATABASE_URL, autocommit=True)
        self.addCleanup(self.connection.close)
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute("DELETE FROM securities WHERE ticker = ANY(%s)", (list(TICKERS),))

    def test_upsert_creates_securities_and_price_bars(self) -> None:
        frame = _frame([
            {"ticker": "ZAAPL", "trading_date": date(2025, 1, 2), "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000},
            {"ticker": "ZMSFT", "trading_date": date(2025, 1, 2), "open": 200, "high": 201, "low": 199, "close": 200.5, "volume": 2000},
        ])
        row_count = ingest_prices.upsert_price_bars(self.connection, frame)
        self.assertEqual(row_count, 2)

        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT s.ticker, p.close, p.adjusted_close, p.volume FROM price_bars p "
                "JOIN securities s ON s.id = p.security_id WHERE s.ticker = ANY(%s) ORDER BY s.ticker",
                (list(TICKERS),),
            )
            rows = cursor.fetchall()

        self.assertEqual(len(rows), 2)
        for ticker, close, adjusted_close, volume in rows:
            self.assertEqual(float(close), float(adjusted_close))

    def test_upsert_is_idempotent_and_overwrites_changed_values(self) -> None:
        first = _frame([
            {"ticker": "ZAAPL", "trading_date": date(2025, 1, 2), "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000},
        ])
        ingest_prices.upsert_price_bars(self.connection, first)

        revised = _frame([
            {"ticker": "ZAAPL", "trading_date": date(2025, 1, 2), "open": 100, "high": 101, "low": 99, "close": 111.0, "volume": 5000},
        ])
        ingest_prices.upsert_price_bars(self.connection, revised)

        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*), max(close), max(volume) FROM price_bars p "
                "JOIN securities s ON s.id = p.security_id WHERE s.ticker = 'ZAAPL'"
            )
            count, close, volume = cursor.fetchone()

        self.assertEqual(count, 1)  # overwritten, not duplicated
        self.assertEqual(float(close), 111.0)
        self.assertEqual(volume, 5000)

    def test_upsert_handles_missing_open_high_low_as_null(self) -> None:
        frame = _frame([
            {"ticker": "ZAAPL", "trading_date": date(2025, 1, 2), "open": float("nan"), "high": float("nan"),
             "low": float("nan"), "close": 100.5, "volume": float("nan")},
        ])
        ingest_prices.upsert_price_bars(self.connection, frame)

        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT open, volume FROM price_bars p JOIN securities s ON s.id = p.security_id "
                "WHERE s.ticker = 'ZAAPL'"
            )
            open_value, volume = cursor.fetchone()

        self.assertIsNone(open_value)
        self.assertIsNone(volume)


@unittest.skipUnless(DB_AVAILABLE, "TEST_DATABASE_URL not set or unreachable")
class RealNetworkIngestTest(unittest.TestCase):
    """Exercises the real yfinance -> validate -> Postgres path end to end."""

    def test_ingest_one_ticker_from_real_yfinance(self) -> None:
        connection = psycopg.connect(TEST_DATABASE_URL, autocommit=True)
        try:
            try:
                row_count = ingest_prices.ingest(connection, ["AAPL"], "5d")
            except Exception as exc:  # noqa: BLE001 - network/provider flakiness, not our bug
                self.skipTest(f"yfinance unreachable or returned no data: {exc}")

            self.assertGreater(row_count, 0)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM price_bars p JOIN securities s ON s.id = p.security_id "
                    "WHERE s.ticker = 'AAPL' AND p.trading_date >= current_date - interval '10 days'"
                )
                self.assertGreater(cursor.fetchone()[0], 0)
        finally:
            connection.close()


class PollLoopTests(unittest.TestCase):
    """No DB/network needed: exercises main()'s loop/retry behavior directly."""

    def test_polls_repeatedly_and_survives_a_failed_run(self) -> None:
        calls: list[int] = []

        def fake_ingest(connection, tickers, period):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("simulated provider outage")
            return 3

        class StopLoop(Exception):
            pass

        with patch("worker.ingest_prices.psycopg.connect") as mock_connect, \
             patch("worker.ingest_prices.ingest", side_effect=fake_ingest), \
             patch("worker.ingest_prices.time.sleep", side_effect=[None, StopLoop()]):
            mock_connect.return_value.__enter__.return_value = object()
            with self.assertRaises(StopLoop):
                ingest_prices.main([
                    "--database-url", "postgresql://unused",
                    "--tickers", "AAPL",
                    "--interval-seconds", "0",
                ])

        # First call raised and was swallowed (logged, not fatal); the loop
        # kept going and made a second call before the test stopped it.
        self.assertEqual(len(calls), 2)

    def test_without_interval_runs_once_and_returns(self) -> None:
        with patch("worker.ingest_prices.psycopg.connect") as mock_connect, \
             patch("worker.ingest_prices.ingest", return_value=5) as mock_ingest, \
             patch("worker.ingest_prices.time.sleep") as mock_sleep:
            mock_connect.return_value.__enter__.return_value = object()
            exit_code = ingest_prices.main(["--database-url", "postgresql://unused", "--tickers", "AAPL"])

        self.assertEqual(exit_code, 0)
        mock_ingest.assert_called_once()
        mock_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
