"""Tests for worker/build_targets.py and worker/run_pending_backtests.py.

Same convention as test_persistence.py: needs TEST_DATABASE_URL pointing at
a Postgres instance with migrations 001-004 applied, and skips itself
otherwise so `pytest worker/` stays runnable without a database.
"""

from __future__ import annotations

import os
import unittest
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from backtest.calendar import nyse_sessions
from worker import build_targets, run_pending_backtests

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
            cursor.execute("DELETE FROM securities WHERE ticker = ANY(%s)", (list(TICKERS),))
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
            return cursor.fetchone()[0]

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

        # One monthly signal (Jan 31 2025) falls in this window -> 2 holdings x 1 signal.
        self.assertEqual(row_count, 2)
        self.assertEqual(len(rows), 2)
        tickers = {r[0] for r in rows}
        self.assertEqual(tickers, {"TAAPL", "TMSFT"})
        for _, weight, provenance in rows:
            self.assertIn(float(weight), (0.6, 0.4))
            self.assertEqual(provenance, "portfolio-holdings")

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

    def test_a_queued_backtest_is_claimed_by_only_one_worker(self) -> None:
        """Regression: two workers could both see the same queued row, and
        the second one rewriting the target schedule while the first exported
        its CSV would silently produce a backtest holding nothing."""
        backtest_id = self._create_backtest()
        other_worker = psycopg.connect(TEST_DATABASE_URL, autocommit=True)
        self.addCleanup(other_worker.close)

        first = run_pending_backtests.claim_next(self.connection)
        second = run_pending_backtests.claim_next(other_worker)

        self.assertEqual(first, backtest_id)
        self.assertIsNone(second, "a second worker must not claim the same row")

    def test_claiming_flips_status_before_any_targets_are_touched(self) -> None:
        """The claim has to be visible immediately, otherwise the window it
        closes is still open."""
        backtest_id = self._create_backtest()

        run_pending_backtests.claim_next(self.connection)

        with self.connection.cursor() as cursor:
            cursor.execute("SELECT status FROM backtests WHERE id = %s", (backtest_id,))
            self.assertEqual(cursor.fetchone()[0], "running")
            cursor.execute(
                "SELECT count(*) FROM backtest_targets WHERE backtest_id = %s", (backtest_id,)
            )
            self.assertEqual(cursor.fetchone()[0], 0)

    def test_claim_returns_none_when_the_queue_is_empty(self) -> None:
        self.assertIsNone(run_pending_backtests.claim_next(self.connection))

    def test_an_abandoned_claim_is_returned_to_the_queue(self) -> None:
        """A worker killed mid-job leaves its row 'running'. Without a lease
        nothing ever retries it, so claiming atomically would trade a double
        -run for a permanently stuck job."""
        backtest_id = self._create_backtest()
        run_pending_backtests.claim_next(self.connection)
        # Backdate the claim past its lease.
        with self.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE backtests SET claimed_at = now() - interval '2 hours' WHERE id = %s",
                (backtest_id,),
            )

        requeued = run_pending_backtests.requeue_abandoned(self.connection)

        self.assertIn(backtest_id, requeued)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT status, claimed_at FROM backtests WHERE id = %s", (backtest_id,))
            status, claimed_at = cursor.fetchone()
        self.assertEqual(status, "queued")
        self.assertIsNone(claimed_at)
        # And it is claimable again.
        self.assertEqual(run_pending_backtests.claim_next(self.connection), backtest_id)

    def test_a_fresh_claim_is_left_alone_by_the_reaper(self) -> None:
        """The lease must not steal a job that is still legitimately running."""
        backtest_id = self._create_backtest()
        run_pending_backtests.claim_next(self.connection)

        requeued = run_pending_backtests.requeue_abandoned(self.connection)

        self.assertNotIn(backtest_id, requeued)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT status FROM backtests WHERE id = %s", (backtest_id,))
            self.assertEqual(cursor.fetchone()[0], "running")

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
    def test_complete_data_is_marked_completed_and_publishable(self) -> None:
        backtest_id = self._create_backtest()

        with TemporaryDirectory() as tmp:
            run_pending_backtests.run_once(self.connection, engine=ENGINE_PATH, output_root=Path(tmp))

            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT status, research_outcome, publishable FROM backtests WHERE id = %s",
                    (backtest_id,),
                )
                status, outcome, publishable = cursor.fetchone()

        self.assertEqual(status, "completed")
        self.assertEqual(outcome, "completed")
        self.assertTrue(publishable)

    @unittest.skipUnless(ENGINE_PATH.is_file(), "C++ engine not built: run `make -C backtest`")
    def test_incomplete_price_history_finishes_but_is_not_publishable(self) -> None:
        """The case that used to look identical to a good result: the engine
        runs fine, so status is 'completed', but the data did not cover the
        window. research_outcome must say 'collecting' and publishable must
        be false, with a message explaining why."""
        backtest_id = self._create_backtest()
        # Drop the tail of every price series so coverage stops short of END.
        with self.connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM price_bars WHERE trading_date > %s AND security_id IN "
                "(SELECT id FROM securities WHERE ticker = ANY(%s))",
                (END_DATE - timedelta(days=10), list(TICKERS)),
            )

        with TemporaryDirectory() as tmp:
            run_pending_backtests.run_once(self.connection, engine=ENGINE_PATH, output_root=Path(tmp))

            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT status, research_outcome, publishable, validation_messages "
                    "FROM backtests WHERE id = %s",
                    (backtest_id,),
                )
                status, outcome, publishable, messages = cursor.fetchone()

        self.assertEqual(outcome, "collecting")
        self.assertFalse(publishable, "incomplete data must never be publishable")
        codes = [m["code"] for m in messages]
        self.assertIn("PRICE_HISTORY_ENDS_EARLY", codes)
        # The engine itself still succeeded, which is exactly why the two
        # axes have to be reported separately.
        self.assertIn(status, ("completed", "failed"))

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
