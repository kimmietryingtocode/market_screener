"""Reproducible four-scenario diagnostic for the September research queue."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import yaml

from backtest.calendar import CALENDAR_SOURCE, CALENDAR_VERSION, nyse_sessions
from backtest.accuracy import write_accuracy_report
from backtest.workflow import (
    REPO_ROOT,
    _run_cpp,
    build_equal_weight_targets,
    build_targets,
    validate_prices,
)


DEFAULT_PRICES = REPO_ROOT / "data/backtest/2026-09-18-corrected-top10-observed/prices.csv"
DEFAULT_UNIVERSE = REPO_ROOT / "data/backtest/2026-09-18-corrected-top10-observed/universe.csv"
DEFAULT_FUNDAMENTALS = REPO_ROOT / "data/fundamentals/sep17-18-test/pit-v2/fundamentals.parquet"
DEFAULT_CONFIG = REPO_ROOT / "screener/config.yaml"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _portable_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return path.name


def _scenario_summary(name: str, directory: Path) -> dict:
    summary = pd.read_csv(directory / "summary.csv").set_index("metric")
    curve = pd.read_csv(directory / "equity_curve.csv")
    targets = pd.read_csv(directory / "targets.csv")
    return {
        "scenario": name,
        "total_return_pct": 100 * summary.loc["total_return", "portfolio"],
        "cagr_pct": 100 * summary.loc["cagr", "portfolio"],
        "benchmark_return_pct": 100 * summary.loc["total_return", "benchmark"],
        "maximum_drawdown_pct": 100 * summary.loc["maximum_drawdown", "portfolio"],
        "sharpe": summary.loc["sharpe_ratio", "portfolio"],
        "average_cash_pct": 100 * curve["cash_weight"].mean(),
        "average_exposure_pct": 100 * (1 - curve["cash_weight"]).mean(),
        "maximum_target_weight_pct": 100 * targets["target_weight"].max(),
        "turnover_x": summary.loc["total_turnover", "portfolio"],
        "cost_usd": summary.loc["transaction_costs", "portfolio"],
        "rebalances": int(summary.loc["rebalance_count", "portfolio"]),
        "initial_waiting_sessions": int(summary.loc["initial_waiting_sessions", "portfolio"]),
    }


def run_four_scenarios(
    prices_path: Path,
    universe_path: Path,
    fundamentals_path: Path,
    config_path: Path,
    output_root: Path,
    *,
    start: date = date(2023, 1, 1),
    end: date = date(2025, 12, 31),
    previous_run: Path | None = None,
) -> Path:
    """Run the corrected, current-selection-biased September diagnostic offline."""
    for path in (prices_path, universe_path, fundamentals_path, config_path):
        if not path.is_file():
            raise ValueError(f"missing experiment input: {path}")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = output_root / f"{stamp}-sep17-18-corrected"
    output.mkdir(parents=True, exist_ok=False)
    config = yaml.safe_load(config_path.read_text())
    fundamentals = pd.read_parquet(fundamentals_path)
    validation = validate_prices(
        prices_path,
        universe_path,
        start=start,
        end=end,
        benchmark=str(config["backtest"].get("benchmark", "SPY")),
    )
    validation.to_csv(output / "data_validation.csv", index=False)
    nyse_sessions(start, end).to_csv(output / "sessions.csv", index=False)

    schedules: dict[str, pd.DataFrame] = {}
    diagnostics: dict[str, pd.DataFrame] = {}
    schedules["fixed_basket_equal"], diagnostics["fixed_basket_equal"] = build_equal_weight_targets(
        prices_path, universe_path, config, start=start, end=end, label="fixed_basket_equal"
    )
    schedules["sec_equal"], diagnostics["sec_equal"] = build_equal_weight_targets(
        prices_path, universe_path, config, start=start, end=end,
        label="sec_equal", fundamentals=fundamentals,
    )
    schedules["sec_equal_trend"], diagnostics["sec_equal_trend"] = build_equal_weight_targets(
        prices_path, universe_path, config, start=start, end=end,
        label="sec_equal_trend", fundamentals=fundamentals,
        trend_days=int(config["backtest"].get("trend_days", 200)),
    )
    full_config = deepcopy(config)
    schedules["full_hierarchy"], coverage, diagnostics["full_hierarchy"] = build_targets(
        prices_path, universe_path, full_config, start=start, end=end,
        approval_tickers=None, label="full_hierarchy", fundamentals=fundamentals,
    )
    blocked = {
        name: sorted(set(frame.loc[frame["reason"].isin(["missing_price_history", "missing_signal_price"]), "security_id"]))
        for name, frame in diagnostics.items()
        if "reason" in frame and frame["reason"].isin(["missing_price_history", "missing_signal_price"]).any()
    }
    if blocked:
        raise ValueError(f"experiment blocked by incomplete signal prices: {blocked}")

    summaries = []
    for name, targets in schedules.items():
        directory = output / name
        directory.mkdir()
        targets.to_csv(directory / "targets.csv", index=False)
        diagnostics[name].to_csv(directory / "eligibility.csv", index=False)
        if name == "full_hierarchy":
            coverage.to_csv(directory / "coverage.csv", index=False)
        _run_cpp(prices_path, directory / "targets.csv", directory, config, start, end)
        summaries.append(_scenario_summary(name, directory))
    corrected = pd.DataFrame(summaries)
    corrected.to_csv(output / "comparison.csv", index=False)
    pd.concat(
        [frame.assign(scenario=name) for name, frame in diagnostics.items()],
        ignore_index=True,
    ).groupby(["scenario", "status", "reason"], dropna=False).size().rename("rows").reset_index().to_csv(
        output / "coverage_report.csv", index=False
    )
    write_accuracy_report(
        output,
        list(schedules),
        initial_capital=float(config["backtest"].get("initial_capital", 100000)),
        start=start,
        end=end,
    )

    if previous_run and (previous_run / "comparison.csv").is_file():
        old = pd.read_csv(previous_run / "comparison.csv").rename(columns={"scenario": "old_scenario"})
        aliases = {
            "yahoo_equal": "fixed_basket_equal",
            "sec_equal": "sec_equal",
            "sec_equal_trend": "sec_equal_trend",
            "full_strategy": "full_hierarchy",
        }
        old["scenario"] = old["old_scenario"].map(aliases)
        comparison = old.merge(
            corrected, on="scenario", how="outer", suffixes=("_old", "_corrected")
        )
        comparison["explanation"] = comparison["scenario"].map(
            {
                "fixed_basket_equal": "Accounting convention clarified; holdings unchanged.",
                "sec_equal": "Corrected SEC selection chooses the latest eligible period before its revision.",
                "sec_equal_trend": "Corrected SEC selection plus the unchanged 200-session trend filter.",
                "full_hierarchy": "Accounting convention clarified; hierarchy and eligibility unchanged.",
            }
        )
        comparison.to_csv(output / "old_vs_corrected.csv", index=False)

    artifact_hashes = {}
    evidence = (
        "targets.csv", "account_ledger.csv", "equity_curve.csv", "holdings.csv",
        "trades.csv", "summary.csv", "eligibility.csv",
    )
    for name in evidence:
        for path in sorted(output.glob(f"*/{name}")):
            artifact_hashes[str(path.relative_to(output))] = _digest(path)
    (output / "artifact_hashes.json").write_text(
        json.dumps(artifact_hashes, indent=2, sort_keys=True) + "\n"
    )
    manifest = {
        "schema_version": 2,
        "created_at": datetime.now(UTC).isoformat(),
        "label": "historical_diagnostic_current_selection_biased",
        "window": [start.isoformat(), end.isoformat()],
        "calendar": {"version": CALENDAR_VERSION, "source": CALENDAR_SOURCE},
        "execution": "signal at 18:00 America/New_York on month-end session; next session close",
        "accounting": "synthetic total-return units; no separate dividend or split credits",
        "limitations": [
            "The September 2026 shortlist is applied to 2023-2025 and has current-selection bias.",
            "Yahoo history was archived in September 2026 and is not a historical vendor vintage.",
            "SEC facts are selected by filing availability; FactSet forecasts do not select historical holdings.",
            "Passing structural checks does not independently verify Yahoo's adjusted history.",
        ],
        "inputs": {
            name: {"path": _portable_path(path), "sha256": _digest(path)}
            for name, path in {
                "prices": prices_path, "universe": universe_path,
                "fundamentals": fundamentals_path, "config": config_path,
            }.items()
        },
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return output
