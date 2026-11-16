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
import math
from pathlib import Path
from typing import Any

from worker import risk

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
                """The engine writes `nan` for metrics it cannot define — a
                Sharpe with zero volatility, a CAGR over zero elapsed time.
                Postgres NUMERIC happily stores NaN and hands it back, and it
                then serializes as the bare token NaN, which is not valid
                JSON and which JSON.parse rejects outright. Undefined is
                NULL, not a number.
                """
                if value in (None, ""):
                    return None
                parsed = float(value)
                return parsed if math.isfinite(parsed) else None

            metrics[record["metric"]] = {
                "portfolio": parse(record["portfolio"]),
                "benchmark": parse(record["benchmark"]),
            }
    return metrics


def _read_equity_curve(path: Path) -> list[dict[str, Any]]:
    """The engine writes six columns; three used to be discarded. daily_return
    feeds the risk measures and drawdown is the underwater series the UI
    could not draw because it was never stored."""
    def number(value: str | None) -> float | None:
        if value in (None, ""):
            return None
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None

    with path.open(newline="") as handle:
        return [
            {
                "trading_date": record["date"],
                "portfolio_value": number(record.get("portfolio_value")),
                "benchmark_value": number(record.get("benchmark_value")),
                "daily_return": number(record.get("daily_return")),
                "drawdown": number(record.get("drawdown")),
            }
            for record in csv.DictReader(handle)
        ]


def import_job_result(
    connection: Any,
    backtest_id: int,
    job_dir: Path,
    *,
    coverage_messages: list[dict] | None = None,
) -> None:
    """Persist a completed worker job's outputs, or record its failure.

    `job_dir` is the directory worker/entrypoint.py wrote via `execute_job`
    (contains job_result.json and, on success, a `backtest/` subdirectory
    with the engine's summary/equity_curve CSVs).

    `publishable` is stored alongside the metrics so a client can tell a
    trusted result from a diagnostic one. It is never inferred from `status`:
    a job can finish cleanly on incomplete data.
    """
    result = json.loads((job_dir / "job_result.json").read_text())
    # Coverage findings explain *why* a result isn't publishable, so they
    # belong next to the engine's own validation output, not just in logs.
    messages = list(coverage_messages or []) + list(result["validation_messages"])

    # One transaction for the whole import. The equity curve is replaced by a
    # DELETE followed by inserts; under autocommit a failure in between would
    # leave the curve permanently gone with the metrics still written.
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE backtests SET
                research_outcome = %s,
                publishable = %s,
                methodology_version = %s,
                available_at = %s,
                input_hashes = %s,
                validation_messages = %s
            WHERE id = %s
            """,
            (
                result["research_outcome"],
                result["publishable"],
                result["methodology_version"],
                result["available_at"],
                json.dumps(result["input_hashes"]),
                json.dumps(messages),
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

        curve = _read_equity_curve(job_dir / "backtest" / "equity_curve.csv")
        # The engine reports a daily_return per row; the benchmark's has to
        # be derived from its value series to compare like with like.
        portfolio_returns = [p["daily_return"] for p in curve if p["daily_return"] is not None]
        benchmark_returns = risk.returns_from_values(
            [p["benchmark_value"] for p in curve if p["benchmark_value"] is not None]
        )
        # Beta pairs observation-by-observation, and the engine's first row
        # is the capital-commitment period, which has no benchmark move to
        # pair with. Align on the shorter series.
        width = min(len(portfolio_returns), len(benchmark_returns))

        cursor.execute(
            """
            INSERT INTO backtest_results (
                backtest_id, total_return, annualized_return, volatility,
                sharpe_ratio, max_drawdown, alpha_vs_benchmark,
                var_95, cvar_95, beta_vs_benchmark,
                total_turnover, transaction_costs, average_cash_weight, rebalance_count
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (backtest_id) DO UPDATE SET
                total_return = EXCLUDED.total_return,
                annualized_return = EXCLUDED.annualized_return,
                volatility = EXCLUDED.volatility,
                sharpe_ratio = EXCLUDED.sharpe_ratio,
                max_drawdown = EXCLUDED.max_drawdown,
                alpha_vs_benchmark = EXCLUDED.alpha_vs_benchmark,
                var_95 = EXCLUDED.var_95,
                cvar_95 = EXCLUDED.cvar_95,
                beta_vs_benchmark = EXCLUDED.beta_vs_benchmark,
                total_turnover = EXCLUDED.total_turnover,
                transaction_costs = EXCLUDED.transaction_costs,
                average_cash_weight = EXCLUDED.average_cash_weight,
                rebalance_count = EXCLUDED.rebalance_count
            """,
            (
                backtest_id,
                metric("total_return", "portfolio"),
                metric("cagr", "portfolio"),
                metric("annualized_volatility", "portfolio"),
                metric("sharpe_ratio", "portfolio"),
                metric("maximum_drawdown", "portfolio"),
                alpha,
                risk.historical_var(portfolio_returns),
                risk.conditional_var(portfolio_returns),
                risk.beta(portfolio_returns[-width:], benchmark_returns[-width:]) if width >= 2 else None,
                metric("total_turnover", "portfolio"),
                metric("transaction_costs", "portfolio"),
                metric("average_cash_weight", "portfolio"),
                int(metric("rebalance_count", "portfolio")) if metric("rebalance_count", "portfolio") is not None else None,
            ),
        )

        cursor.execute("DELETE FROM equity_curve_points WHERE backtest_id = %s", (backtest_id,))
        equity_rows = [
            (
                backtest_id,
                point["trading_date"],
                point["portfolio_value"],
                point["benchmark_value"],
                point["daily_return"],
                point["drawdown"],
            )
            for point in curve
        ]
        cursor.executemany(
            """
            INSERT INTO equity_curve_points (
                backtest_id, trading_date, portfolio_value, benchmark_value, daily_return, drawdown
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            equity_rows,
        )

        cursor.execute(
            "UPDATE backtests SET status = 'completed', completed_at = now() WHERE id = %s",
            (backtest_id,),
        )
