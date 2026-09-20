"""Runtime accounting and performance checks for completed simulator outputs."""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import pandas as pd

from backtest.calendar import month_end_signals


CURRENCY_TOLERANCE = 0.000001
WEIGHT_TOLERANCE = 1e-12


def audit_scenario(directory: Path, *, initial_capital: float, start: date, end: date) -> dict:
    """Independently recalculate ledger identities, statistics, and decision timing."""
    ledger = pd.read_csv(directory / "account_ledger.csv", parse_dates=["date"])
    curve = pd.read_csv(directory / "equity_curve.csv", parse_dates=["date"])
    targets = pd.read_csv(directory / "targets.csv", parse_dates=["execution_date"])
    summary = pd.read_csv(directory / "summary.csv").set_index("metric")
    identity_error = (
        ledger["portfolio_value"] - ledger["cash"] - ledger["invested_value"]
    ).abs().max()
    trade_error = (
        ledger["pre_trade_value"] - ledger["transaction_cost"] - ledger["portfolio_value"]
    ).abs().max()
    expected_returns = curve["portfolio_value"].pct_change(fill_method=None)
    expected_returns.iloc[0] = curve["portfolio_value"].iloc[0] / initial_capital - 1
    return_error = (expected_returns - curve["daily_return"]).abs().max()

    values = curve["portfolio_value"]
    returns = expected_returns
    years = (curve["date"].iloc[-1] - curve["date"].iloc[0]).days / 365.25
    peak = pd.concat([pd.Series([initial_capital]), values], ignore_index=True).cummax().iloc[1:]
    calculated = {
        "ending_value": values.iloc[-1],
        "total_return": values.iloc[-1] / initial_capital - 1,
        "cagr": (values.iloc[-1] / initial_capital) ** (1 / years) - 1,
        "annualized_volatility": returns.std(ddof=1) * math.sqrt(252),
        "sharpe_ratio": returns.mean() / returns.std(ddof=1) * math.sqrt(252),
        "maximum_drawdown": values.reset_index(drop=True).div(peak).sub(1).min(),
        "total_turnover": (ledger["traded_notional"] / ledger["pre_trade_value"]).sum(),
        "transaction_costs": ledger["transaction_cost"].sum(),
        "average_cash_weight": (ledger["cash"] / ledger["portfolio_value"]).mean(),
    }
    statistic_errors = {
        metric: abs(float(summary.loc[metric, "portfolio"]) - value)
        for metric, value in calculated.items()
    }
    currency_metrics = {"ending_value", "transaction_costs"}
    statistics_pass = all(
        error < (CURRENCY_TOLERANCE if metric in currency_metrics else 1e-9)
        for metric, error in statistic_errors.items()
    )

    expected_pairs = {
        execution.date(): signal.date() for signal, execution in month_end_signals(start, end)
    }
    timing_pass = all(
        pd.Timestamp(row.signal_at).date() == expected_pairs.get(row.execution_date.date())
        and pd.Timestamp(row.signal_at).hour == 18
        for row in targets.itertuples(index=False)
    )
    return {
        "scenario": directory.name,
        "sessions": len(ledger),
        "max_account_identity_error_usd": identity_error,
        "max_trade_identity_error_usd": trade_error,
        "max_daily_return_error": return_error,
        "minimum_cash_usd": ledger["cash"].min(),
        "maximum_statistic_error": max(statistic_errors.values()),
        "timing_pass": timing_pass,
        "accounting_pass": identity_error < CURRENCY_TOLERANCE
        and trade_error < CURRENCY_TOLERANCE
        and return_error < WEIGHT_TOLERANCE
        and ledger["cash"].min() >= -CURRENCY_TOLERANCE,
        "statistics_pass": statistics_pass,
    }


def write_accuracy_report(
    run_directory: Path,
    scenarios: list[str],
    *,
    initial_capital: float,
    start: date,
    end: date,
) -> pd.DataFrame:
    """Write machine-readable and human-readable release-gate evidence."""
    audits = pd.DataFrame(
        [
            audit_scenario(
                run_directory / scenario,
                initial_capital=initial_capital,
                start=start,
                end=end,
            )
            for scenario in scenarios
        ]
    )
    audits.to_csv(run_directory / "accuracy_report.csv", index=False)
    validation_path = run_directory / "data_validation.csv"
    validation = pd.read_csv(validation_path) if validation_path.is_file() else pd.DataFrame()
    passed = audits[["accounting_pass", "statistics_pass", "timing_pass"]].all().all()
    if not validation.empty:
        passed = passed and not validation["status"].eq("failed").any()
    payload = {
        "status": "passed" if passed else "failed",
        "currency_tolerance": CURRENCY_TOLERANCE,
        "weight_tolerance": WEIGHT_TOLERANCE,
        "independent_reference_test": "backtest/tests/test_accuracy.py",
        "notes": [
            "Entry costs use initial capital as the first return denominator.",
            "Drawdown includes initial capital; volatility and Sharpe use 252 sessions and sample standard deviation.",
            "Average cash includes every evaluation session, including pre-entry and fully-cash sessions.",
            "Yahoo adjusted history passed structural checks; vendor values were not independently verified.",
        ],
        "data_validation": validation.to_dict("records"),
        "scenarios": audits.to_dict("records"),
    }
    (run_directory / "accuracy_report.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )
    lines = [
        "# Backtester Accuracy Report",
        "",
        f"Release gate: **{payload['status'].upper()}**",
        "",
        "The C++ ledger is checked independently for cash + positions = equity, trade-cost reconciliation, daily returns, performance statistics, and official next-session timing.",
        "",
        "```text",
        audits.to_string(index=False),
        "```",
        "",
        "Input-data checks:",
        "",
        "```text",
        validation.to_string(index=False) if not validation.empty else "No validation report supplied.",
        "```",
        "",
        "The independent Python accounting oracle is `backtest/tests/test_accuracy.py`. Data validation means structural checks passed; it is not a claim that Yahoo history was independently verified.",
    ]
    (run_directory / "accuracy_report.md").write_text("\n".join(lines) + "\n")
    return audits
