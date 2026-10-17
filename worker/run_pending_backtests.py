"""Poll `backtests` for queued rows and run each one through the C++ engine.

This is the piece the architecture doc calls "wrap the engine in the Python
worker" plus "connect PostgreSQL": for each queued backtest it builds a
target schedule from the source portfolio, exports prices/targets CSVs,
invokes worker/entrypoint.py's file-based job contract, and imports the
result back into PostgreSQL. One job's failure is recorded on that row and
does not stop the others.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import psycopg

from worker import coverage
from worker.build_targets import generate_targets_from_portfolio
from worker.entrypoint import execute_job
from worker.persistence import export_prices_csv, export_targets_csv, import_job_result, mark_running

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENGINE = REPO_ROOT / "backtest" / "build" / "portfolio_backtest"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data" / "backtest" / "jobs"
# Longer than any legitimate run; a shorter lease would hand a slow job to a
# second worker while the first is still working on it.
CLAIM_TIMEOUT_SECONDS = 1800


def _fail(connection: Any, backtest_id: int, message: str) -> None:
    validation_messages = json.dumps(
        [{"code": "WORKER_FAILED", "severity": "error", "message": message}]
    )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE backtests SET
                status = 'failed',
                research_outcome = 'blocked',
                validation_messages = %s
            WHERE id = %s
            """,
            (validation_messages, backtest_id),
        )


def run_one(connection: Any, backtest_id: int, *, engine: Path, output_root: Path) -> dict[str, Any]:
    """Run a single queued backtest end to end. Raises on failure after
    marking the row failed, so the caller can log/continue."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT benchmark, start_date, end_date, parameters FROM backtests WHERE id = %s",
            (backtest_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError(f"no backtest with id {backtest_id}")
        benchmark, start_date, end_date, parameters = row
        parameters = parameters or {}

    generate_targets_from_portfolio(connection, backtest_id)

    job_id = f"backtest-{backtest_id}-{uuid.uuid4().hex[:8]}"

    # execute_job owns and creates output_root/job_id itself (and refuses to
    # run if it already exists), so the exported CSVs must live somewhere
    # else. This staging directory is only needed until the engine reads it.
    with TemporaryDirectory(prefix=f"{job_id}-inputs-") as staging:
        prices_path = Path(staging) / "prices.csv"
        targets_path = Path(staging) / "targets.csv"

        export_prices_csv(connection, backtest_id, prices_path)
        export_targets_csv(connection, backtest_id, targets_path)

        mark_running(connection, backtest_id, job_id)

        # Grade the inputs before running, not after. A job that exits zero
        # only proves the engine didn't crash; whether the data was actually
        # there is a separate question, and it decides whether this result is
        # publishable or merely diagnostic.
        report = coverage.assess(connection, backtest_id)
        if report.research_outcome == "blocked":
            logger.warning("backtest %s is blocked on missing data: %s", backtest_id, report.messages)

        job_parameters = {
            "prices": str(prices_path),
            "targets": str(targets_path),
            "engine": str(engine),
            "benchmark": benchmark,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "research_outcome": report.research_outcome,
            "available_at": report.available_at or f"{end_date.isoformat()}T00:00:00Z",
        }
        if "cost_bps" in parameters:
            job_parameters["cost_bps"] = parameters["cost_bps"]
        if "initial_capital" in parameters:
            job_parameters["initial_capital"] = parameters["initial_capital"]

        result = execute_job(job_id, "backtest", job_parameters, output_root)

    import_job_result(connection, backtest_id, output_root / job_id, coverage_messages=report.messages)
    return result


def claim_next(connection: Any) -> int | None:
    """Atomically take ownership of one queued backtest, or return None.

    Selecting the queued ids and only marking them running later leaves a
    window — seconds wide, since the schedule is built and the CSVs exported
    in between — where a second worker sees the same row as queued. That is
    not merely duplicated effort: generate_targets_from_portfolio clears and
    rewrites the target schedule, so a second worker deleting those rows
    while the first exports its CSV produces a backtest that holds nothing
    and reports a flat line as a successful result.

    FOR UPDATE SKIP LOCKED hands each row to exactly one worker, and the
    status flips before any target rows are touched.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE backtests SET status = 'running', claimed_at = now()
            WHERE id = (
                SELECT id FROM backtests
                WHERE status = 'queued'
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id
            """
        )
        row = cursor.fetchone()
        return row[0] if row else None


def requeue_abandoned(connection: Any, timeout_seconds: float = CLAIM_TIMEOUT_SECONDS) -> list[int]:
    """Return backtests whose claim has expired to the queue.

    Claiming atomically means a worker that is killed mid-job leaves its row
    'running' with nothing to retry it. The claim is therefore a lease: held
    past the timeout, the row is assumed abandoned. The timeout must exceed
    the longest legitimate run, or a slow job gets picked up twice.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE backtests SET status = 'queued', claimed_at = NULL
            WHERE status = 'running'
              AND claimed_at IS NOT NULL
              AND claimed_at < now() - make_interval(secs => %s)
            RETURNING id
            """,
            (timeout_seconds,),
        )
        requeued = [row[0] for row in cursor.fetchall()]
    if requeued:
        logger.warning("requeued abandoned backtests: %s", requeued)
    return requeued


def run_once(
    connection: Any,
    *,
    engine: Path = DEFAULT_ENGINE,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> list[int]:
    """Drain the queue once. Returns the ids this worker claimed."""
    requeue_abandoned(connection)
    processed: list[int] = []
    while (backtest_id := claim_next(connection)) is not None:
        processed.append(backtest_id)
        try:
            run_one(connection, backtest_id, engine=engine, output_root=output_root)
        except Exception as exc:  # noqa: BLE001 - one bad job must not stop the poller
            logger.exception("backtest %s failed", backtest_id)
            _fail(connection, backtest_id, str(exc))
    return processed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--engine", type=Path, default=DEFAULT_ENGINE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=None,
        help="seconds between polls; omit to process the current queue once and exit",
    )
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")

    logging.basicConfig(level=logging.INFO)
    with psycopg.connect(args.database_url, autocommit=True) as connection:
        while True:
            processed = run_once(connection, engine=args.engine, output_root=args.output_root)
            if processed:
                logger.info("processed backtests: %s", processed)
            if args.poll_interval is None:
                break
            time.sleep(args.poll_interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
