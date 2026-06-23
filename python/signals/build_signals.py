from __future__ import print_function
from numpy import cumsum, log, polyfit, sqrt, subtract
from numpy.random import randn
import numpy as np
import pandas as pd
from pathlib import Path


RAW_PATH = Path("data/manual_factset/factset_export.csv")
SIGNALS_PATH = Path("data/signals/signals.csv")
PRICES_PATH = Path("data/processed/prices.csv")


def winsorized_zscore(series: pd.Series, higher_is_better: bool = True,
                      lower: float = 0.05, upper: float = 0.95) -> pd.Series:
    series = pd.to_numeric(series, errors="coerce")
    if not higher_is_better:
        series = -series
    lo, hi = series.quantile(lower), series.quantile(upper)
    clipped = series.clip(lo, hi)
    mu, sigma = clipped.mean(), clipped.std()
    if sigma == 0:
        return pd.Series(50.0, index=series.index)
    z = (clipped - mu) / sigma
    return (z - z.min()) / (z.max() - z.min()) * 100


def hurst(ts):
    """Returns the Hurst Exponent of the time series vector ts."""
    lags = range(2, min(100, len(ts) // 2))
    tau = [sqrt(np.std(subtract(ts[lag:], ts[:-lag]))) for lag in lags]
    poly = polyfit(log(list(lags)), log(tau), 1)
    return poly[0] * 2.0


def score_week(group: pd.DataFrame) -> pd.DataFrame:
    g = group.copy()
    g["momentum_score"] = winsorized_zscore(g["momentum_3m"], higher_is_better=True)
    g["growth_score"] = winsorized_zscore(g["revenue_growth"], higher_is_better=True)
    g["quality_score"] = winsorized_zscore(g["operating_margin"], higher_is_better=True)
    g["valuation_score"] = winsorized_zscore(g["forward_pe"], higher_is_better=False)
    g["estimate_score"] = winsorized_zscore(g["estimate_revision"], higher_is_better=True)

    g["raw_composite"] = (
        0.30 * g["momentum_score"]
        + 0.25 * g["growth_score"]
        + 0.20 * g["quality_score"]
        + 0.15 * g["estimate_score"]
        + 0.10 * g["valuation_score"]
    )

    g["score"] = g["raw_composite"].rank(pct=True) * 100

    return g[["date", "ticker", "score"]]


def build_signals(df: pd.DataFrame, rebalance_lag: int = 1) -> pd.DataFrame:
    """
    Builds bias-controlled signals.

    The score is calculated on signal_date, but it is only allowed
    to be traded on a later trade_date.

    rebalance_lag=1 means:
    use this week's signal for next week's trade.
    """
    df = df.copy()

    df["date"] = pd.to_datetime(df["date"])
    df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()

    scored = (
        df.groupby("date", group_keys=False)
        .apply(score_week)
        .reset_index(drop=True)
    )

    scored["date"] = pd.to_datetime(scored["date"])

    # Get all rebalance dates in sorted order
    rebalance_dates = sorted(scored["date"].unique())

    # Map each signal date to a future trade date
    date_to_trade_date = {}

    for i, signal_date in enumerate(rebalance_dates):
        trade_index = i + rebalance_lag

        if trade_index < len(rebalance_dates):
            date_to_trade_date[signal_date] = rebalance_dates[trade_index]
        else:
            date_to_trade_date[signal_date] = pd.NaT

    scored["signal_date"] = scored["date"]
    scored["trade_date"] = scored["signal_date"].map(date_to_trade_date)

    # Drop the final signal rows that do not have a future trade date
    scored = scored.dropna(subset=["trade_date"])

    scored["signal_date"] = pd.to_datetime(scored["signal_date"]).dt.strftime("%Y-%m-%d")
    scored["trade_date"] = pd.to_datetime(scored["trade_date"]).dt.strftime("%Y-%m-%d")

    signals = scored[["signal_date", "trade_date", "ticker", "score"]].copy()

    return signals.sort_values(
        ["trade_date", "score"],
        ascending=[True, False],
    )


def build_prices(df: pd.DataFrame) -> pd.DataFrame:
    prices = df[["date", "ticker", "close"]].copy()

    prices["date"] = pd.to_datetime(prices["date"]).dt.strftime("%Y-%m-%d")
    prices["ticker"] = prices["ticker"].astype(str).str.upper().str.strip()
    prices["close"] = pd.to_numeric(prices["close"], errors="coerce")

    prices = prices.dropna(subset=["date", "ticker", "close"])

    return prices.sort_values(["date", "ticker"])


def validate_signals(signals: pd.DataFrame) -> None:
    """Run Hurst exponent on each ticker's score series as a sanity check."""
    for ticker, group in signals.groupby("ticker"):
        ts = group.sort_values("date")["score"].dropna().values
        if len(ts) < 30:
            continue
        h = hurst(ts)
        label = "mean-reverting" if h < 0.45 else "trending" if h > 0.55 else "random walk"
        print(f"  {ticker}: H={h:.3f} ({label})")


def main():
    df = pd.read_csv(RAW_PATH)

    required_columns = [
        "date", "ticker", "close",
        "momentum_3m", "revenue_growth", "operating_margin",
        "forward_pe", "estimate_revision",
    ]

    missing = [col for col in required_columns if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns from FactSet export: {missing}")

    SIGNALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PRICES_PATH.parent.mkdir(parents=True, exist_ok=True)

    signals = build_signals(df)
    prices = build_prices(df)

    signals.to_csv(SIGNALS_PATH, index=False)
    prices.to_csv(PRICES_PATH, index=False)

    print(f"Wrote {SIGNALS_PATH}")
    print(f"Wrote {PRICES_PATH}")

    print("\nSignal persistence check (Hurst exponent):")
    validate_signals(signals)


if __name__ == "__main__":
    main()