"""Generate a redistributable offline fixture for the full experiment."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from backtest.calendar import nyse_sessions


def write_fixture(directory: Path) -> tuple[Path, Path, Path, Path]:
    directory.mkdir(parents=True)
    dates = nyse_sessions(date(2022, 1, 3), date(2023, 4, 30))["date"]
    rates = {"SPY": 1.0003, "XLK": 1.0005, "XLE": 1.0002, "AAA": 1.0008, "BBB": 1.0004}
    rows = [
        {"date": day, "ticker": ticker, "adjusted_close": 100 * rate**index}
        for index, day in enumerate(dates)
        for ticker, rate in rates.items()
    ]
    prices = directory / "prices.csv"
    pd.DataFrame(rows).to_csv(prices, index=False)
    universe = directory / "universe.csv"
    pd.DataFrame(
        [
            {"ticker": "AAA", "company": "Alpha", "sector": "Technology", "industry": "Software", "sector_etf": "XLK"},
            {"ticker": "BBB", "company": "Beta", "sector": "Energy", "industry": "Refining", "sector_etf": "XLE"},
        ]
    ).to_csv(universe, index=False)
    fundamentals = directory / "fundamentals.parquet"
    pd.DataFrame(
        [
            {"ticker": ticker, "period_end": "2022-12-31", "fiscal_period": "FY", "available_at": "2023-01-15T20:00:00Z", "ebitda_ttm": 100.0, "free_cash_flow_ttm": 50.0}
            for ticker in ("AAA", "BBB")
        ]
    ).to_parquet(fundamentals, index=False)
    config = directory / "config.yaml"
    config.write_text(
        """score_weights: {rs_3m: 0.4, return_6m: 0.3, return_1m: 0.2, low_volatility: 0.1}
sector_scan: {top_n: 2}
industry_scan: {top_n_per_sector: 1}
company_scan: {top_n_per_industry: 1}
backtest:
  benchmark: SPY
  trend_days: 200
  maximum_position_weight: 0.4
  maximum_sector_weight: 0.5
  weighting: inverse-vol
  timezone: America/New_York
  signal_time: "18:00"
  transaction_cost_bps: 10
  initial_capital: 100000
"""
    )
    return prices, universe, fundamentals, config
