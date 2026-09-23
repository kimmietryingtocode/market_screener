"""Run a small, reproducible target-schedule test from an uploaded Yahoo CSV."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import subprocess
import tempfile
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from backtest.tests.reference_calculator import simulate, statistics


TICKERS = ("AAPL", "JPM", "XOM")
BENCHMARK = "SPY"
START = date(2025, 1, 2)
SIGNAL_START = date(2024, 12, 31)
END = date(2025, 12, 31)
INITIAL_CAPITAL = 100_000.0
TARGET_WEIGHT = 0.15
MONEY_TOLERANCE = 1e-6
WEIGHT_TOLERANCE = 1e-12


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_prices(source: Path, output: Path) -> dict[str, object]:
    frame = pd.read_csv(source)
    required = {"ticker", "trading_date", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError("price file is missing: " + ", ".join(sorted(missing)))

    frame["ticker"] = frame["ticker"].astype("string").str.strip().str.upper()
    frame["date"] = pd.to_datetime(frame["trading_date"], errors="coerce").dt.date
    frame["adjusted_close"] = pd.to_numeric(frame["close"], errors="coerce")
    if frame[["ticker", "date", "adjusted_close"]].isna().any().any():
        raise ValueError("price file contains blank or malformed ticker/date/close values")
    if (~frame["adjusted_close"].map(math.isfinite) | frame["adjusted_close"].le(0)).any():
        raise ValueError("price file contains nonpositive or nonfinite close values")
    if frame.duplicated(["ticker", "date"]).any():
        raise ValueError("price file contains duplicate ticker/date rows")

    selected = frame[frame["ticker"].isin((*TICKERS, BENCHMARK))].copy()
    if set(selected["ticker"]) != {*TICKERS, BENCHMARK}:
        raise ValueError("price file does not contain all required tickers and SPY")
    date_sets = selected.groupby("ticker")["date"].agg(set)
    if any(value != date_sets[BENCHMARK] for ticker, value in date_sets.items() if ticker != BENCHMARK):
        raise ValueError("required tickers do not share the benchmark date calendar")

    if min(date_sets[BENCHMARK]) > SIGNAL_START or max(date_sets[BENCHMARK]) < END:
        raise ValueError("price history does not cover the requested signal and test dates")
    selected = selected[selected["date"].between(SIGNAL_START, END)]
    selected = selected[["date", "ticker", "adjusted_close"]].sort_values(["date", "ticker"])
    output.parent.mkdir(parents=True, exist_ok=True)
    selected.to_csv(output, index=False, date_format="%Y-%m-%d")
    return {
        "source_file": str(source),
        "source_sha256": sha256(source),
        "source_price_basis": "Yahoo Finance auto_adjust=True; uploaded close treated as adjusted_close",
        "normalized_sha256": sha256(output),
        "rows": int(len(selected)),
        "tickers": [*TICKERS, BENCHMARK],
        "date_start": min(date_sets[BENCHMARK]).isoformat(),
        "date_end": max(date_sets[BENCHMARK]).isoformat(),
    }


def monthly_targets(price_path: Path, output: Path) -> pd.DataFrame:
    prices = pd.read_csv(price_path, parse_dates=["date"])
    benchmark_dates = sorted(prices.loc[prices["ticker"].eq(BENCHMARK), "date"].dt.date)
    signals = (
        pd.Series(benchmark_dates)
        .loc[lambda values: values.between(SIGNAL_START, END)]
        .groupby(lambda index: (benchmark_dates[index].year, benchmark_dates[index].month))
        .max()
        .tolist()
    )
    rows: list[dict[str, object]] = []
    ny = ZoneInfo("America/New_York")
    for signal in signals:
        execution = next((day for day in benchmark_dates if day > signal), None)
        if execution is None or execution > END:
            continue
        signal_at = datetime.combine(signal, time(18, 0), tzinfo=ny).isoformat()
        for ticker in TICKERS:
            rows.append(
                {
                    "signal_at": signal_at,
                    "execution_date": execution.isoformat(),
                    "security_id": ticker,
                    "target_weight": TARGET_WEIGHT,
                    "provenance_id": "uploaded-price-test-2025-monthly",
                }
            )
    targets = pd.DataFrame(rows)
    if targets.empty:
        raise ValueError("no in-window monthly target executions were generated")
    targets.to_csv(output, index=False)
    return targets


def run_engine(repo_root: Path, prices: Path, targets: Path, output: Path, cost_bps: float) -> None:
    subprocess.run(
        [
            str(repo_root / "backtest" / "build" / "portfolio_backtest"),
            "--prices",
            str(prices),
            "--targets",
            str(targets),
            "--output-dir",
            str(output),
            "--benchmark",
            BENCHMARK,
            "--start",
            START.isoformat(),
            "--end",
            END.isoformat(),
            "--cost-bps",
            str(cost_bps),
            "--initial-capital",
            str(INITIAL_CAPITAL),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def compare_run(price_path: Path, target_path: Path, output: Path, cost_bps: float) -> dict[str, object]:
    prices = pd.read_csv(price_path, parse_dates=["date"])
    pivot = prices.pivot(index="date", columns="ticker", values="adjusted_close").sort_index()
    pivot = pivot.loc[pd.Timestamp(START) : pd.Timestamp(END)]
    targets = pd.read_csv(target_path, parse_dates=["execution_date"])
    reference_ledger, reference_holdings, reference_trades = simulate(
        pivot, targets, initial_capital=INITIAL_CAPITAL, cost_bps=cost_bps
    )
    engine_ledger = pd.read_csv(output / "account_ledger.csv", parse_dates=["date"])
    engine_holdings = pd.read_csv(output / "holdings.csv", parse_dates=["date"])

    checks: dict[str, object] = {}

    def max_difference(left: pd.Series, right: pd.Series) -> float:
        return float((left.to_numpy(dtype=float) - right.to_numpy(dtype=float)).__abs__().max())

    ledger_columns = [
        "pre_trade_value",
        "cash",
        "invested_value",
        "traded_notional",
        "transaction_cost",
        "portfolio_value",
    ]
    merged_ledger = engine_ledger.merge(
        reference_ledger, on="date", suffixes=("_engine", "_reference"), validate="one_to_one"
    )
    for column in ledger_columns:
        checks[f"ledger_{column}_max_difference"] = max_difference(
            merged_ledger[f"{column}_engine"], merged_ledger[f"{column}_reference"]
        )
    checks["ledger_money_match"] = all(
        checks[f"ledger_{column}_max_difference"] <= MONEY_TOLERANCE for column in ledger_columns
    )

    engine_weights = engine_holdings[["date", "ticker", "weight"]]
    reference_weights = reference_holdings[["date", "ticker", "weight"]]
    merged_weights = engine_weights.merge(
        reference_weights, on=["date", "ticker"], suffixes=("_engine", "_reference"), validate="one_to_one"
    )
    checks["holding_weight_max_difference"] = max_difference(
        merged_weights["weight_engine"], merged_weights["weight_reference"]
    )
    checks["holding_weights_match"] = checks["holding_weight_max_difference"] <= WEIGHT_TOLERANCE

    summary = pd.read_csv(output / "summary.csv").set_index("metric")
    expected = statistics(reference_ledger, reference_trades, INITIAL_CAPITAL)
    summary_map = {
        "ending_value": "ending_value",
        "total_return": "total_return",
        "cagr": "cagr",
        "annualized_volatility": "annualized_volatility",
        "sharpe_ratio": "sharpe_ratio",
        "maximum_drawdown": "maximum_drawdown",
    }
    summary_differences = {
        metric: abs(float(summary.loc[metric, "portfolio"]) - expected[key])
        for metric, key in summary_map.items()
    }
    checks["summary_differences"] = summary_differences
    checks["summary_matches_reference"] = all(value <= MONEY_TOLERANCE for value in summary_differences.values())

    checks["accounting_max_difference"] = float(
        (engine_ledger["cash"] + engine_ledger["invested_value"] - engine_ledger["portfolio_value"])
        .abs()
        .max()
    )
    checks["accounting_balances"] = checks["accounting_max_difference"] <= MONEY_TOLERANCE
    checks["nonnegative_cash"] = bool(engine_ledger["cash"].ge(-MONEY_TOLERANCE).all())
    checks["transaction_costs"] = float(engine_ledger["transaction_cost"].sum())
    checks["costs_match_request"] = bool(
        abs(checks["transaction_costs"] - expected["transaction_costs"]) <= MONEY_TOLERANCE
    )
    checks["rebalances"] = int(len(pd.read_csv(output / "rebalance_log.csv").groupby("execution_date")))
    checks["passed"] = bool(
        checks["ledger_money_match"]
        and checks["holding_weights_match"]
        and checks["summary_matches_reference"]
        and checks["accounting_balances"]
        and checks["nonnegative_cash"]
        and checks["costs_match_request"]
    )
    return {"cost_bps": cost_bps, "checks": checks, "reference_statistics": expected}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    output = args.output_dir.resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing output directory: {output}")
    output.mkdir(parents=True)
    inputs = output / "inputs"
    runs = output / "runs"
    inputs.mkdir()
    runs.mkdir()
    shutil.copy2(args.input, inputs / "source_price_bars.csv")
    source_info = normalize_prices(args.input, inputs / "prices.csv")
    targets = monthly_targets(inputs / "prices.csv", inputs / "targets.csv")
    subprocess.run(["make", "-C", str(repo_root / "backtest"), "test"], check=True)
    subprocess.run(["make", "-C", str(repo_root / "backtest")], check=True)

    run_reports: dict[str, object] = {}
    for cost_bps, label in ((0.0, "cost-0bps"), (10.0, "cost-10bps")):
        path = runs / label
        run_engine(repo_root, inputs / "prices.csv", inputs / "targets.csv", path, cost_bps)
        run_reports[label] = compare_run(inputs / "prices.csv", inputs / "targets.csv", path, cost_bps)

    # Repeat the costed run in a temporary directory to test determinism without
    # adding another result tree to the deliverable.
    with tempfile.TemporaryDirectory(prefix="uploaded-price-repeat-") as temporary:
        repeat = Path(temporary) / "cost-10bps"
        run_engine(repo_root, inputs / "prices.csv", inputs / "targets.csv", repeat, 10.0)
        files = sorted(path.name for path in (runs / "cost-10bps").iterdir() if path.is_file())
        deterministic = all(
            (runs / "cost-10bps" / name).read_bytes() == (repeat / name).read_bytes() for name in files
        )

    # A cash liquidation followed by re-entry is an explicit engine behavior.
    edge = output / "edge-cases"
    edge.mkdir()
    edge_targets = pd.DataFrame(
        [
            {"signal_at": "2024-12-31T18:00:00-05:00", "execution_date": "2025-01-02", "security_id": ticker, "target_weight": TARGET_WEIGHT, "provenance_id": "liquidation-reentry"}
            for ticker in TICKERS
        ]
        + [{"signal_at": "2025-03-31T18:00:00-04:00", "execution_date": "2025-04-01", "security_id": "", "target_weight": 0.0, "provenance_id": "liquidation-reentry"}]
        + [
            {"signal_at": "2025-04-01T18:00:00-04:00", "execution_date": "2025-04-02", "security_id": ticker, "target_weight": TARGET_WEIGHT, "provenance_id": "liquidation-reentry"}
            for ticker in TICKERS
        ]
    )
    edge_target_path = edge / "liquidation-reentry-targets.csv"
    edge_targets.to_csv(edge_target_path, index=False)
    edge_output = edge / "liquidation-reentry"
    run_engine(repo_root, inputs / "prices.csv", edge_target_path, edge_output, 0.0)
    edge_holdings = pd.read_csv(edge_output / "holdings.csv")
    liquidation_ok = not edge_holdings[edge_holdings["date"].eq("2025-04-01")].any().any()
    reentry_ok = not edge_holdings[edge_holdings["date"].eq("2025-04-02")].empty

    # Missing price for a held security must fail the engine, never forward-fill.
    with tempfile.TemporaryDirectory(prefix="uploaded-price-missing-") as temporary:
        broken = Path(temporary) / "prices.csv"
        prices = pd.read_csv(inputs / "prices.csv")
        prices = prices[~(prices["ticker"].eq("XOM") & prices["date"].eq("2025-01-03"))]
        prices.to_csv(broken, index=False)
        failed = subprocess.run(
            [str(repo_root / "backtest" / "build" / "portfolio_backtest"), "--prices", str(broken), "--targets", str(inputs / "targets.csv"), "--output-dir", str(Path(temporary) / "broken-output"), "--benchmark", BENCHMARK, "--start", START.isoformat(), "--end", END.isoformat()],
            capture_output=True,
            text=True,
        )
        missing_price_rejected = failed.returncode != 0 and "missing" in (failed.stderr + failed.stdout).lower()

    report = {
        "status": "pass",
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "input_contract": {"price_columns": ["date", "ticker", "adjusted_close"], "target_columns": list(targets.columns)},
        "source": source_info,
        "parameters": {"tickers": list(TICKERS), "benchmark": BENCHMARK, "signal_start": SIGNAL_START.isoformat(), "start": START.isoformat(), "end": END.isoformat(), "initial_capital": INITIAL_CAPITAL, "target_weight": TARGET_WEIGHT, "cash_weight": 1 - len(TICKERS) * TARGET_WEIGHT, "signal_time": "18:00 America/New_York", "cost_runs_bps": [0.0, 10.0]},
        "runs": run_reports,
        "checks": {"deterministic_repeat": deterministic, "liquidation_reentry": liquidation_ok and reentry_ok, "missing_held_price_rejected": missing_price_rejected},
    }
    all_passed = all(report["checks"].values()) and all(item["checks"]["passed"] for item in run_reports.values())
    report["status"] = "pass" if all_passed else "fail"
    (output / "test_report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    (output / "test_report.md").write_text(
        "# Uploaded price backtest test\n\n"
        f"Status: **{report['status'].upper()}**\n\n"
        f"Source SHA-256: `{source_info['source_sha256']}`\n\n"
        f"Code commit: `{report['code_commit']}`\n\n"
        f"Runner SHA-256: `{report['runner_sha256']}`\n\n"
        + "\n".join(f"- {name}: {'PASS' if value else 'FAIL'}" for name, value in report["checks"].items())
        + "\n"
    )
    print(output)
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
