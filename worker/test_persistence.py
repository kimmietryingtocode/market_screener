"""Tests for worker/persistence.py.

The DB-backed tests need a reachable Postgres with migrations 001-003
applied (docker-compose's `postgres` service does this). They read the
connection string from TEST_DATABASE_URL and skip themselves if it isn't
set or the server isn't reachable, so `pytest worker/` stays runnable
without a database.
"""

from __future__ import annotations

import csv
import json
import os
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from worker import persistence
from worker.entrypoint import execute_job

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

try:
    import psycopg
except ImportError:  # pragma: no cover - exercised only without psycopg installed
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


@unittest.skipUnless(DB_AVAILABLE, "TEST_DATABASE_URL not set or unreachable")
class PersistenceRoundTripTests(unittest.TestCase):
    """Exercises persistence.py against a real Postgres instance."""

    def setUp(self) -> None:
        self.connection = psycopg.connect(TEST_DATABASE_URL, autocommit=True)
        self.addCleanup(self.connection.close)
        with self.connection.cursor() as cursor:
            # Fully isolate each test's rows so runs don't interfere.
            cursor.execute(
                "INSERT INTO securities (ticker) VALUES ('TAAPL'), ('TJPM'), ('TSPY') "
                "ON CONFLICT (ticker) DO NOTHING"
            )
            cursor.execute("SELECT id, ticker FROM securities WHERE ticker IN ('TAAPL', 'TJPM', 'TSPY')")
            self.security_ids = {ticker: sid for sid, ticker in cursor.fetchall()}

            cursor.execute(
                """
                INSERT INTO backtests (status, start_date, end_date, benchmark, rebalance_frequency)
                VALUES ('queued', '2025-01-02', '2025-01-06', 'TSPY', 'monthly')
                RETURNING id
                """
            )
            self.backtest_id = cursor.fetchone()[0]
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute("DELETE FROM backtests WHERE id = %s", (self.backtest_id,))
            cursor.execute(
                "DELETE FROM securities WHERE ticker IN ('TAAPL', 'TJPM', 'TSPY')"
            )

    def _insert_price_bars(self) -> None:
        rows = []
        for ticker, prices in {
            "TAAPL": [100.0, 101.0, 102.0],
            "TJPM": [50.0, 50.5, 51.0],
            "TSPY": [400.0, 402.0, 404.0],
        }.items():
            sid = self.security_ids[ticker]
            for offset, price in enumerate(prices):
                rows.append((sid, date(2025, 1, 2 + offset), price))
        with self.connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO price_bars (security_id, trading_date, adjusted_close) VALUES (%s, %s, %s)",
                rows,
            )

    def _insert_targets(self) -> None:
        with self.connection.cursor() as cursor:
            for ticker in ("TAAPL", "TJPM"):
                cursor.execute(
                    """
                    INSERT INTO backtest_targets
                        (backtest_id, security_id, signal_at, execution_date, target_weight, provenance_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        self.backtest_id,
                        self.security_ids[ticker],
                        datetime(2025, 1, 1, tzinfo=timezone.utc),
                        date(2025, 1, 2),
                        0.45,
                        "unit-test",
                    ),
                )

    def test_export_prices_csv_maps_security_id_to_ticker(self) -> None:
        self._insert_price_bars()
        self._insert_targets()
        with TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "prices.csv"
            row_count = persistence.export_prices_csv(self.connection, self.backtest_id, output_path)

            with output_path.open(newline="") as handle:
                rows = list(csv.DictReader(handle))

        # Benchmark (TSPY) plus the two targeted tickers, 3 rows each.
        self.assertEqual(row_count, 9)
        self.assertEqual(list(rows[0].keys()), ["date", "ticker", "adjusted_close"])
        tickers_present = {row["ticker"] for row in rows}
        self.assertEqual(tickers_present, {"TAAPL", "TJPM", "TSPY"})
        # Every value in the ticker column must be a ticker string, never
        # the numeric database id — this is the exact bug the module exists
        # to prevent.
        for row in rows:
            self.assertFalse(row["ticker"].isdigit())

    def test_export_targets_csv_uses_ticker_not_numeric_id(self) -> None:
        self._insert_targets()
        with TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "targets.csv"
            row_count = persistence.export_targets_csv(self.connection, self.backtest_id, output_path)
            with output_path.open(newline="") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(row_count, 2)
        self.assertEqual(
            list(rows[0].keys()),
            ["signal_at", "execution_date", "security_id", "target_weight", "provenance_id"],
        )
        security_id_values = {row["security_id"] for row in rows}
        self.assertEqual(security_id_values, {"TAAPL", "TJPM"})
        numeric_db_ids = {str(self.security_ids["TAAPL"]), str(self.security_ids["TJPM"])}
        self.assertTrue(security_id_values.isdisjoint(numeric_db_ids))

    def test_export_targets_csv_rejects_backtest_with_no_targets(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "no target rows"):
                persistence.export_targets_csv(self.connection, self.backtest_id, Path(tmp) / "targets.csv")

    def test_import_job_result_persists_metrics_and_equity_curve(self) -> None:
        with TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "job"
            job_dir.mkdir()
            (job_dir / "job_result.json").write_text(json.dumps({
                "execution_state": "succeeded",
                "research_outcome": "completed",
                "publishable": True,
                "methodology_version": "worker-backtest-v1",
                "available_at": "2025-01-01T00:00:00Z",
                "input_hashes": {"prices": "abc", "targets": "def"},
                "validation_messages": [],
            }))
            backtest_dir = job_dir / "backtest"
            backtest_dir.mkdir()
            (backtest_dir / "summary.csv").write_text(
                "metric,portfolio,benchmark\n"
                "total_return,0.10,0.18\n"
                "cagr,0.11,0.19\n"
                "annualized_volatility,0.09,0.20\n"
                "sharpe_ratio,1.1,0.9\n"
                "maximum_drawdown,-0.05,-0.15\n"
            )
            (backtest_dir / "equity_curve.csv").write_text(
                "date,portfolio_value,benchmark_value\n"
                "2025-01-02,100000,100000\n"
                "2025-01-03,101000,101500\n"
            )

            persistence.import_job_result(self.connection, self.backtest_id, job_dir)

            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT status, research_outcome, methodology_version FROM backtests WHERE id = %s",
                    (self.backtest_id,),
                )
                status, research_outcome, methodology_version = cursor.fetchone()
                cursor.execute(
                    "SELECT total_return, sharpe_ratio, alpha_vs_benchmark FROM backtest_results WHERE backtest_id = %s",
                    (self.backtest_id,),
                )
                total_return, sharpe_ratio, alpha = cursor.fetchone()
                cursor.execute(
                    "SELECT count(*) FROM equity_curve_points WHERE backtest_id = %s",
                    (self.backtest_id,),
                )
                equity_row_count = cursor.fetchone()[0]

        self.assertEqual(status, "completed")
        self.assertEqual(research_outcome, "completed")
        self.assertEqual(methodology_version, "worker-backtest-v1")
        self.assertAlmostEqual(float(total_return), 0.10)
        self.assertAlmostEqual(float(sharpe_ratio), 1.1)
        self.assertAlmostEqual(float(alpha), 0.11 - 0.19)
        self.assertEqual(equity_row_count, 2)

    def test_engine_nan_metrics_are_stored_as_null_not_nan(self) -> None:
        """Regression: the engine writes `nan` for a Sharpe with zero
        volatility or a CAGR over zero elapsed time. Postgres NUMERIC accepts
        NaN and hands it back, and it then serializes as the bare token NaN,
        which is not valid JSON and which JSON.parse rejects. Undefined must
        be NULL."""
        with TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "job"
            job_dir.mkdir()
            (job_dir / "job_result.json").write_text(json.dumps({
                "execution_state": "succeeded",
                "research_outcome": "completed",
                "publishable": True,
                "methodology_version": "worker-backtest-v1",
                "available_at": "2025-01-01T00:00:00Z",
                "input_hashes": {},
                "validation_messages": [],
            }))
            backtest_dir = job_dir / "backtest"
            backtest_dir.mkdir()
            # Exactly what the engine emits for a flat, zero-volatility book.
            (backtest_dir / "summary.csv").write_text(
                "metric,portfolio,benchmark\n"
                "total_return,0,0\n"
                "cagr,nan,nan\n"
                "annualized_volatility,0,0\n"
                "sharpe_ratio,nan,nan\n"
                "maximum_drawdown,0,0\n"
            )
            (backtest_dir / "equity_curve.csv").write_text(
                "date,portfolio_value,benchmark_value\n2025-01-02,100000,100000\n"
            )

            persistence.import_job_result(self.connection, self.backtest_id, job_dir)

            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT sharpe_ratio, annualized_return, alpha_vs_benchmark "
                    "FROM backtest_results WHERE backtest_id = %s",
                    (self.backtest_id,),
                )
                sharpe, annualized, alpha = cursor.fetchone()

        self.assertIsNone(sharpe, "NaN sharpe must be stored as NULL")
        self.assertIsNone(annualized, "NaN cagr must be stored as NULL")
        # alpha is derived from two NaN cagrs, so it must not be fabricated.
        self.assertIsNone(alpha)

    def test_import_job_result_marks_failed_jobs_without_touching_results(self) -> None:
        with TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "job"
            job_dir.mkdir()
            (job_dir / "job_result.json").write_text(json.dumps({
                "execution_state": "failed",
                "research_outcome": "blocked",
                "publishable": False,
                "methodology_version": "worker-backtest-v1",
                "available_at": "2025-01-01T00:00:00Z",
                "input_hashes": {},
                "validation_messages": [{"code": "JOB_FAILED", "severity": "error", "message": "boom"}],
            }))

            persistence.import_job_result(self.connection, self.backtest_id, job_dir)

            with self.connection.cursor() as cursor:
                cursor.execute("SELECT status FROM backtests WHERE id = %s", (self.backtest_id,))
                status = cursor.fetchone()[0]
                cursor.execute(
                    "SELECT count(*) FROM backtest_results WHERE backtest_id = %s", (self.backtest_id,)
                )
                results_count = cursor.fetchone()[0]

        self.assertEqual(status, "failed")
        self.assertEqual(results_count, 0)

    @unittest.skipUnless(ENGINE_PATH.is_file(), "C++ engine not built: run `make -C backtest`")
    def test_full_round_trip_through_real_engine(self) -> None:
        """DB -> CSV export -> real C++ engine -> DB import, end to end."""
        self._insert_price_bars()
        self._insert_targets()

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prices_path = root / "prices.csv"
            targets_path = root / "targets.csv"
            persistence.export_prices_csv(self.connection, self.backtest_id, prices_path)
            persistence.export_targets_csv(self.connection, self.backtest_id, targets_path)

            job_id = f"test-{self.backtest_id}"
            persistence.mark_running(self.connection, self.backtest_id, job_id)
            result = execute_job(
                job_id,
                "backtest",
                {
                    "prices": str(prices_path),
                    "targets": str(targets_path),
                    "engine": str(ENGINE_PATH),
                    "benchmark": "TSPY",
                    "start_date": "2025-01-02",
                    "end_date": "2025-01-06",
                    "cost_bps": 0,
                    "initial_capital": 100000,
                    "research_outcome": "completed",
                    "available_at": "2025-01-01T00:00:00Z",
                },
                root / "outputs",
            )
            self.assertEqual(result["execution_state"], "succeeded")

            job_dir = root / "outputs" / job_id
            persistence.import_job_result(self.connection, self.backtest_id, job_dir)

            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT status, job_id FROM backtests WHERE id = %s", (self.backtest_id,)
                )
                status, stored_job_id = cursor.fetchone()
                cursor.execute(
                    "SELECT total_return FROM backtest_results WHERE backtest_id = %s",
                    (self.backtest_id,),
                )
                total_return = cursor.fetchone()[0]

        self.assertEqual(status, "completed")
        self.assertEqual(stored_job_id, job_id)
        self.assertIsNotNone(total_return)


class ExportTargetsCsvUnitTests(unittest.TestCase):
    """Pure formatting checks using a stubbed cursor: no database required."""

    class _FakeCursor:
        def __init__(self, rows: list[tuple]) -> None:
            self._rows = rows
            self.queries: list[str] = []

        def execute(self, query: str, params: tuple = ()) -> None:
            self.queries.append(query)

        def fetchall(self) -> list[tuple]:
            return self._rows

        def __enter__(self) -> "ExportTargetsCsvUnitTests._FakeCursor":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    class _FakeConnection:
        def __init__(self, rows: list[tuple]) -> None:
            self._rows = rows

        def cursor(self) -> "ExportTargetsCsvUnitTests._FakeCursor":
            return ExportTargetsCsvUnitTests._FakeCursor(self._rows)

    def test_empty_result_set_raises_instead_of_writing_empty_csv(self) -> None:
        connection = self._FakeConnection([])
        with TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "targets.csv"
            with self.assertRaises(ValueError):
                persistence.export_targets_csv(connection, backtest_id=1, output_path=output_path)
            self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()
