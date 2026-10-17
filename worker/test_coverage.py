"""Tests for worker/coverage.py — the finished-vs-trustworthy distinction.

Each test builds a deliberately different data situation and asserts the
grade, because the whole point of this module is that "the job ran" and
"the answer is trustworthy" are different questions.
"""

from __future__ import annotations

import os
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone

from worker import coverage

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

START = date(2025, 1, 2)
END = date(2025, 1, 31)


@unittest.skipUnless(DB_AVAILABLE, "TEST_DATABASE_URL not set or unreachable")
class CoverageAssessmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = psycopg.connect(TEST_DATABASE_URL, autocommit=True)
        self.addCleanup(self.connection.close)
        suffix = uuid.uuid4().hex[:6].upper()
        self.tickers = {"a": f"C{suffix}A", "b": f"C{suffix}B", "bench": f"C{suffix}X"}
        self.addCleanup(self._cleanup)

        with self.connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (email, password_hash) VALUES (%s, 'x') RETURNING id",
                (f"coverage-{suffix}@example.com",),
            )
            self.user_id = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO portfolios (user_id, name) VALUES (%s, 'Coverage Test') RETURNING id",
                (self.user_id,),
            )
            self.portfolio_id = cursor.fetchone()[0]

            self.security_ids = {}
            for key, ticker in self.tickers.items():
                cursor.execute("INSERT INTO securities (ticker) VALUES (%s) RETURNING id", (ticker,))
                self.security_ids[key] = cursor.fetchone()[0]

            cursor.execute(
                """
                INSERT INTO backtests (user_id, portfolio_id, status, start_date, end_date,
                                       benchmark, rebalance_frequency)
                VALUES (%s, %s, 'queued', %s, %s, %s, 'monthly')
                RETURNING id
                """,
                (self.user_id, self.portfolio_id, START, END, self.tickers["bench"]),
            )
            self.backtest_id = cursor.fetchone()[0]

    def _cleanup(self) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute("DELETE FROM backtests WHERE portfolio_id = %s", (self.portfolio_id,))
            cursor.execute("DELETE FROM securities WHERE ticker = ANY(%s)", (list(self.tickers.values()),))
            cursor.execute("DELETE FROM users WHERE id = %s", (self.user_id,))

    def _add_target(self, key: str, execution_date: date = START) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO backtest_targets
                    (backtest_id, security_id, signal_at, execution_date, target_weight, provenance_id)
                VALUES (%s, %s, %s, %s, 0.5, 'coverage-test')
                """,
                (
                    self.backtest_id,
                    self.security_ids[key],
                    datetime(2025, 1, 1, tzinfo=timezone.utc),
                    execution_date,
                ),
            )

    def _add_prices(self, key: str, first: date, last: date) -> None:
        rows, day = [], first
        while day <= last:
            rows.append((self.security_ids[key], day, 100.0))
            day += timedelta(days=1)
        with self.connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO price_bars (security_id, trading_date, adjusted_close) VALUES (%s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                rows,
            )

    def test_no_targets_is_blocked(self) -> None:
        report = coverage.assess(self.connection, self.backtest_id)

        self.assertEqual(report.research_outcome, "blocked")
        self.assertFalse(report.publishable)
        self.assertEqual(report.messages[0]["code"], "NO_TARGETS")

    def test_target_with_no_price_history_at_all_is_blocked(self) -> None:
        self._add_target("a")
        self._add_prices("bench", START, END)

        report = coverage.assess(self.connection, self.backtest_id)

        self.assertEqual(report.research_outcome, "blocked")
        self.assertFalse(report.publishable)
        self.assertEqual(report.messages[0]["code"], "NO_PRICE_DATA")

    def test_missing_benchmark_prices_is_blocked(self) -> None:
        """Without the benchmark there is nothing to compare against, so the
        run is not merely incomplete — it cannot answer the question."""
        self._add_target("a")
        self._add_prices("a", START, END)

        report = coverage.assess(self.connection, self.backtest_id)

        self.assertEqual(report.research_outcome, "blocked")
        self.assertIn("NO_PRICE_DATA", [m["code"] for m in report.messages])

    def test_history_ending_before_the_window_is_collecting_not_completed(self) -> None:
        """This is the case that silently produced a 'successful' backtest
        before: prices exist, so nothing errors, but they stop early."""
        self._add_target("a")
        self._add_prices("a", START, END - timedelta(days=10))
        self._add_prices("bench", START, END - timedelta(days=10))

        report = coverage.assess(self.connection, self.backtest_id)

        self.assertEqual(report.research_outcome, "collecting")
        self.assertFalse(report.publishable)
        self.assertIn("PRICE_HISTORY_ENDS_EARLY", [m["code"] for m in report.messages])

    def test_history_starting_after_the_window_is_collecting(self) -> None:
        self._add_target("a")
        self._add_prices("a", START + timedelta(days=5), END)
        self._add_prices("bench", START + timedelta(days=5), END)

        report = coverage.assess(self.connection, self.backtest_id)

        self.assertEqual(report.research_outcome, "collecting")
        self.assertIn("PRICE_HISTORY_STARTS_LATE", [m["code"] for m in report.messages])

    def test_missing_price_on_an_execution_date_is_collecting(self) -> None:
        """A gap exactly on a rebalance date changes what the engine buys,
        even though overall coverage looks fine."""
        self._add_target("a", execution_date=START)
        self._add_prices("a", START, END)
        self._add_prices("bench", START, END)
        with self.connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM price_bars WHERE security_id = %s AND trading_date = %s",
                (self.security_ids["a"], START),
            )

        report = coverage.assess(self.connection, self.backtest_id)

        self.assertEqual(report.research_outcome, "collecting")
        self.assertIn("MISSING_EXECUTION_PRICE", [m["code"] for m in report.messages])

    def test_full_coverage_is_completed_and_publishable(self) -> None:
        self._add_target("a")
        self._add_target("b")
        self._add_prices("a", START, END)
        self._add_prices("b", START, END)
        self._add_prices("bench", START, END)

        report = coverage.assess(self.connection, self.backtest_id)

        self.assertEqual(report.research_outcome, "completed")
        self.assertTrue(report.publishable)
        self.assertIn("COVERAGE_COMPLETE", [m["code"] for m in report.messages])

    def test_available_at_comes_from_the_newest_input_not_the_window_end(self) -> None:
        """available_at is a point-in-time claim; it must describe the data
        actually used rather than restating the requested end date."""
        newest = END - timedelta(days=3)
        self._add_target("a")
        self._add_prices("a", START, newest)
        self._add_prices("bench", START, newest)

        report = coverage.assess(self.connection, self.backtest_id)

        self.assertTrue(report.available_at.startswith(newest.isoformat()))


if __name__ == "__main__":
    unittest.main()
