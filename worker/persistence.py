"""PostgreSQL <-> CSV boundary for the backtest worker.

worker/entrypoint.py and the C++ engine only understand file paths; they have
no PostgreSQL client. This module is the only place that translates between
database rows and that file contract, which means it is also the only place
that must get one detail right: `price_bars` and `backtest_targets` key
securities by the numeric `securities.id`, but the CSV contract keys them by
ticker string in both files (prices.csv's `ticker` column and, confusingly,
targets.csv's own column literally named `security_id`). The C++ engine
matches rows by that string, not by the database id. Every function below
resolves the numeric id to a ticker before it reaches a CSV, and back again
when a result is imported.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

PRICES_HEADER = ["date", "ticker", "adjusted_close"]
TARGETS_HEADER = ["signal_at", "execution_date", "security_id", "target_weight", "provenance_id"]


def export_prices_csv(connection: Any, backtest_id: int, output_path: Path) -> int:
    """Write every price bar for the tickers a backtest needs to `output_path`.

    Tickers are the union of the backtest's target schedule and its
    benchmark. Full history is exported (not windowed to start/end_date)
    because signals can reference dates before the simulation start; the
    engine's own --start/--end flags do the windowing.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT benchmark FROM backtests WHERE id = %s", (backtest_id,))
        row = cursor.fetchone()
        if row is None:
            raise ValueError(f"no backtest with id {backtest_id}")
        benchmark = row[0]

        cursor.execute(
            """
            SELECT s.ticker, p.trading_date, p.adjusted_close
            FROM price_bars p
            JOIN securities s ON s.id = p.security_id
            WHERE s.ticker = ANY(
                SELECT DISTINCT ticker FROM (
                    SELECT s2.ticker
                    FROM backtest_targets t
                    JOIN securities s2 ON s2.id = t.security_id
                    WHERE t.backtest_id = %s
                    UNION
                    SELECT %s
                ) needed
            )
            ORDER BY p.trading_date, s.ticker
            """,
            (backtest_id, benchmark),
        )
        rows = cursor.fetchall()

    with output_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(PRICES_HEADER)
        for ticker, trading_date, adjusted_close in rows:
            writer.writerow([trading_date.isoformat(), ticker, adjusted_close])
    return len(rows)


def export_targets_csv(connection: Any, backtest_id: int, output_path: Path) -> int:
    """Write a backtest's target-weight schedule as the engine's target CSV.

    The `security_id` column in the output is the ticker string, not
    `backtest_targets.security_id` (a bigint FK) — see the module docstring.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT t.signal_at, t.execution_date, s.ticker, t.target_weight, t.provenance_id
            FROM backtest_targets t
            JOIN securities s ON s.id = t.security_id
            WHERE t.backtest_id = %s
            ORDER BY t.execution_date, s.ticker
            """,
            (backtest_id,),
        )
        rows = cursor.fetchall()

    if not rows:
        raise ValueError(f"backtest {backtest_id} has no target rows to export")

    with output_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(TARGETS_HEADER)
        for signal_at, execution_date, ticker, target_weight, provenance_id in rows:
            writer.writerow([
                signal_at.isoformat(),
                execution_date.isoformat(),
                ticker,
                target_weight,
                provenance_id,
            ])
    return len(rows)


def mark_running(connection: Any, backtest_id: int, job_id: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE backtests SET status = 'running', job_id = %s WHERE id = %s",
            (job_id, backtest_id),
        )


def _read_summary(summary_path: Path) -> dict[str, dict[str, float | None]]:
    metrics: dict[str, dict[str, float | None]] = {}
    with summary_path.open(newline="") as handle:
        for record in csv.DictReader(handle):
            def parse(value: str) -> float | None:
                return float(value) if value not in (None, "") else None

            metrics[record["metric"]] = {
                "portfolio": parse(record["portfolio"]),
                "benchmark": parse(record["benchmark"]),
            }
    return metrics


def import_job_result(connection: Any, backtest_id: int, job_dir: Path) -> None:
    """Persist a completed worker job's outputs, or record its failure.

    `job_dir` is the directory worker/entrypoint.py wrote via `execute_job`
    (contains job_result.json and, on success, a `backtest/` subdirectory
    with the engine's summary/equity_curve CSVs).
    """
    result = json.loads((job_dir / "job_result.json").read_text())

    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE backtests SET
                research_outcome = %s,
                methodology_version = %s,
                available_at = %s,
                input_hashes = %s,
                validation_messages = %s
            WHERE id = %s
            """,
            (
                result["research_outcome"],
                result["methodology_version"],
                result["available_at"],
                json.dumps(result["input_hashes"]),
                json.dumps(result["validation_messages"]),
                backtest_id,
            ),
        )

        if result["execution_state"] != "succeeded":
            cursor.execute(
                "UPDATE backtests SET status = 'failed' WHERE id = %s",
                (backtest_id,),
            )
            return

        summary = _read_summary(job_dir / "backtest" / "summary.csv")

        def metric(name: str, side: str) -> float | None:
            return summary.get(name, {}).get(side)

        alpha = None
        if metric("cagr", "portfolio") is not None and metric("cagr", "benchmark") is not None:
            alpha = metric("cagr", "portfolio") - metric("cagr", "benchmark")

        cursor.execute(
            """
            INSERT INTO backtest_results (
                backtest_id, total_return, annualized_return, volatility,
                sharpe_ratio, max_drawdown, alpha_vs_benchmark
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (backtest_id) DO UPDATE SET
                total_return = EXCLUDED.total_return,
                annualized_return = EXCLUDED.annualized_return,
                volatility = EXCLUDED.volatility,
                sharpe_ratio = EXCLUDED.sharpe_ratio,
                max_drawdown = EXCLUDED.max_drawdown,
                alpha_vs_benchmark = EXCLUDED.alpha_vs_benchmark
            """,
            (
                backtest_id,
                metric("total_return", "portfolio"),
                metric("cagr", "portfolio"),
                metric("annualized_volatility", "portfolio"),
                metric("sharpe_ratio", "portfolio"),
                metric("maximum_drawdown", "portfolio"),
                alpha,
            ),
        )

        cursor.execute("DELETE FROM equity_curve_points WHERE backtest_id = %s", (backtest_id,))
        equity_curve_path = job_dir / "backtest" / "equity_curve.csv"
        with equity_curve_path.open(newline="") as handle:
            equity_rows = [
                (backtest_id, record["date"], record["portfolio_value"], record["benchmark_value"])
                for record in csv.DictReader(handle)
            ]
        cursor.executemany(
            """
            INSERT INTO equity_curve_points (backtest_id, trading_date, portfolio_value, benchmark_value)
            VALUES (%s, %s, %s, %s)
            """,
            equity_rows,
        )

        cursor.execute(
            "UPDATE backtests SET status = 'completed', completed_at = now() WHERE id = %s",
            (backtest_id,),
        )
