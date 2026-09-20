from __future__ import annotations

import subprocess
import json
from pathlib import Path

import pandas as pd
import pytest

from backtest.tests.reference_calculator import simulate, statistics
from backtest.tests.synthetic_e2e import write_fixture
from backtest.experiment import run_four_scenarios


ROOT = Path(__file__).resolve().parents[2]


def _fixture(tmp_path: Path) -> tuple[Path, Path, pd.DataFrame, pd.DataFrame]:
    dates = pd.bdate_range("2024-01-02", periods=145)
    wide = pd.DataFrame(
        {
            "SPY": [100 + index * 0.05 for index in range(len(dates))],
            "AAA": [80 + index * 0.30 for index in range(len(dates))],
            "BBB": [140 - index * 0.20 for index in range(len(dates))],
        },
        index=dates,
    )
    prices_path = tmp_path / "prices.csv"
    wide.rename_axis("date").stack().rename("adjusted_close").rename_axis(
        ["date", "ticker"]
    ).reset_index().to_csv(prices_path, index=False)
    schedule = [
        (130, "AAA", 0.60, "full"), (130, "BBB", 0.40, "full"),
        (132, "AAA", 0.60, "unchanged"), (132, "BBB", 0.40, "unchanged"),
        (134, "AAA", 0.30, "partial"), (134, "BBB", 0.60, "partial"),
        (136, "", 0.0, "cash"), (138, "AAA", 0.50, "reentry"),
    ]
    targets = pd.DataFrame(
        [
            {
                "signal_at": f"{dates[index - 1].date()}T18:00:00-04:00",
                "execution_date": dates[index],
                "security_id": ticker,
                "target_weight": weight,
                "provenance_id": provenance,
            }
            for index, ticker, weight, provenance in schedule
        ]
    )
    targets_path = tmp_path / "targets.csv"
    targets.to_csv(targets_path, index=False, date_format="%Y-%m-%d")
    return prices_path, targets_path, wide.iloc[128:143], targets


def test_cpp_ledger_matches_independent_reference(tmp_path: Path):
    prices_path, targets_path, prices, targets = _fixture(tmp_path)
    subprocess.run(["make", "-C", str(ROOT / "backtest")], check=True)
    output = tmp_path / "output"
    subprocess.run(
        [
            str(ROOT / "backtest/build/portfolio_backtest"),
            "--prices", str(prices_path), "--targets", str(targets_path),
            "--output-dir", str(output), "--start", str(prices.index[0].date()),
            "--end", str(prices.index[-1].date()), "--cost-bps", "10",
            "--initial-capital", "100000",
        ],
        check=True,
    )
    expected_ledger, expected_holdings, expected_trades = simulate(
        prices, targets.assign(execution_date=pd.to_datetime(targets["execution_date"])),
        initial_capital=100000, cost_bps=10,
    )
    actual_ledger = pd.read_csv(output / "account_ledger.csv", parse_dates=["date"])
    actual_curve = pd.read_csv(output / "equity_curve.csv", parse_dates=["date"])
    actual_holdings = pd.read_csv(output / "holdings.csv", parse_dates=["date"])

    for column in (
        "pre_trade_value", "cash", "invested_value", "traded_notional",
        "transaction_cost", "portfolio_value",
    ):
        assert (actual_ledger[column] - expected_ledger[column]).abs().max() < 0.000001
    assert (actual_curve["daily_return"] - expected_ledger["daily_return"]).abs().max() < 1e-12
    merged = actual_holdings.merge(expected_holdings, on=["date", "ticker"], suffixes=("_cpp", "_py"))
    assert len(merged) == len(actual_holdings) == len(expected_holdings)
    for column in ("units", "price", "market_value"):
        assert (merged[f"{column}_cpp"] - merged[f"{column}_py"]).abs().max() < 0.000001
    assert (merged["weight_cpp"] - merged["weight_py"]).abs().max() < 1e-12

    assert (actual_ledger["portfolio_value"] - actual_ledger["cash"] - actual_ledger["invested_value"]).abs().max() < 0.000001
    rebalances = actual_ledger[actual_ledger["traded_notional"].gt(0)]
    assert (rebalances["pre_trade_value"] - rebalances["transaction_cost"] - rebalances["portfolio_value"]).abs().max() < 0.000001
    assert actual_ledger["cash"].min() >= -0.000001

    summary = pd.read_csv(output / "summary.csv").set_index("metric")
    expected_stats = statistics(expected_ledger, expected_trades, 100000)
    for metric, value in expected_stats.items():
        tolerance = 0.000001 if metric in {"ending_value", "transaction_costs"} else 1e-9
        assert float(summary.loc[metric, "portfolio"]) == pytest.approx(value, abs=tolerance)
    assert actual_curve.loc[actual_curve["date"].eq(prices.index[2]), "daily_return"].iloc[0] == pytest.approx(
        -actual_ledger.loc[actual_ledger["date"].eq(prices.index[2]), "transaction_cost"].iloc[0] / 100000,
        abs=1e-12,
    )


def test_adjusted_total_return_chain_does_not_add_actions_again():
    fixture = pd.read_csv(Path(__file__).parent / "fixtures/total_return_actions.csv")
    chained = (1 + fixture["adjusted_close"].pct_change(fill_method=None).dropna()).prod() - 1
    assert chained == pytest.approx(fixture["adjusted_close"].iloc[-1] / fixture["adjusted_close"].iloc[0] - 1)
    assert chained == pytest.approx(0.04)


def test_offline_four_scenario_fixture_passes_release_gate(tmp_path: Path):
    prices, universe, fundamentals, config = write_fixture(tmp_path / "fixture")
    output = run_four_scenarios(
        prices,
        universe,
        fundamentals,
        config,
        tmp_path / "runs",
        start=pd.Timestamp("2023-01-01").date(),
        end=pd.Timestamp("2023-04-30").date(),
    )
    report = json.loads((output / "accuracy_report.json").read_text())
    assert report["status"] == "passed"
    assert set(pd.read_csv(output / "comparison.csv")["scenario"]) == {
        "fixed_basket_equal", "sec_equal", "sec_equal_trend", "full_hierarchy"
    }

    future_prices = tmp_path / "fixture" / "prices-with-future.csv"
    future_prices.write_bytes(prices.read_bytes())
    with future_prices.open("a") as handle:
        for ticker in ("SPY", "XLK", "XLE", "AAA", "BBB"):
            handle.write(f"2023-05-01,{ticker},400\n")
    future_fundamentals = tmp_path / "fixture" / "fundamentals-with-future.parquet"
    facts = pd.read_parquet(fundamentals)
    future = facts.iloc[[0]].assign(
        period_end="2023-03-31",
        fiscal_period="Q1",
        available_at="2023-05-01T20:00:00Z",
        ebitda_ttm=-999.0,
        free_cash_flow_ttm=-999.0,
    )
    pd.concat([facts, future], ignore_index=True).to_parquet(future_fundamentals, index=False)
    future_output = run_four_scenarios(
        future_prices,
        universe,
        future_fundamentals,
        config,
        tmp_path / "future-runs",
        start=pd.Timestamp("2023-01-01").date(),
        end=pd.Timestamp("2023-04-30").date(),
    )
    assert json.loads((output / "artifact_hashes.json").read_text()) == json.loads(
        (future_output / "artifact_hashes.json").read_text()
    )
