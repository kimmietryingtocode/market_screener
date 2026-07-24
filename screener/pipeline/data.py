"""Thin wrappers around yfinance downloads used by every scan stage."""

import pandas as pd
import yfinance as yf


def download_history(tickers: list[str], period: str) -> pd.DataFrame:
    """Daily auto-adjusted OHLCV for `tickers`; columns are (field, ticker)."""
    data = yf.download(
        tickers,
        period=period,
        interval="1d",
        auto_adjust=True,
        progress=False,
        group_by="column",
        threads=True,
    )
    if data.empty:
        raise RuntimeError(f"no price history returned for {tickers}")
    if not isinstance(data.columns, pd.MultiIndex):
        data.columns = pd.MultiIndex.from_product([data.columns, tickers])
    return data


def close_series(data: pd.DataFrame, ticker: str) -> pd.Series:
    if ticker not in data["Close"].columns:
        return pd.Series(dtype=float)
    return data["Close"][ticker].dropna()
