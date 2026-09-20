"""Download and normalize daily market data for the initial stock universe.

Usage:
    python ingest_prices.py --tickers AAPL,MSFT,NVDA --period 2y

The output is intentionally a local CSV snapshot for the first milestone.
A later database migration can load the same normalized rows into price_bars.
"""

import argparse
from pathlib import Path

import pandas as pd

from pipeline.data import download_history

DEFAULT_TICKERS = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "TSLA",
    "JPM",
    "XOM",
    "SPY",
]


def parse_tickers(raw_tickers: str | None) -> list[str]:
    if not raw_tickers:
        return DEFAULT_TICKERS

    tickers = list(dict.fromkeys(
        ticker.strip().upper()
        for ticker in raw_tickers.split(",")
        if ticker.strip()
    ))
    if not tickers:
        raise ValueError("at least one ticker is required")
    return tickers


def normalize_price_bars(data: pd.DataFrame) -> pd.DataFrame:
    """Convert yfinance's wide OHLCV frame into one row per ticker/date."""
    rows: list[pd.DataFrame] = []
    for ticker in data["Close"].columns:
        frame = pd.DataFrame(
            {
                "ticker": ticker,
                "trading_date": data.index,
                "open": data["Open"][ticker].to_numpy(),
                "high": data["High"][ticker].to_numpy(),
                "low": data["Low"][ticker].to_numpy(),
                "close": data["Close"][ticker].to_numpy(),
                "volume": data["Volume"][ticker].to_numpy(),
            }
        )
        rows.append(frame.dropna(subset=["close"]))

    if not rows:
        raise RuntimeError("no valid price bars were returned")

    normalized = pd.concat(rows, ignore_index=True)
    normalized["trading_date"] = pd.to_datetime(
        normalized["trading_date"], utc=True
    ).dt.date
    normalized["ticker"] = normalized["ticker"].str.upper()
    normalized = normalized.drop_duplicates(
        subset=["ticker", "trading_date"]
    ).sort_values(["ticker", "trading_date"])
    return normalized.reset_index(drop=True)


def ingest_prices(tickers: list[str], period: str, output: Path) -> pd.DataFrame:
    data = download_history(tickers, period)
    normalized = normalize_price_bars(data)
    output.parent.mkdir(parents=True, exist_ok=True)
    normalized.to_csv(output, index=False)
    return normalized


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", help="comma-separated ticker list")
    parser.add_argument("--period", default="2y", help="yfinance period, e.g. 1y or 2y")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "price_bars.csv",
    )
    args = parser.parse_args()

    tickers = parse_tickers(args.tickers)
    bars = ingest_prices(tickers, args.period, args.output)
    print(f"Downloaded {len(bars):,} price bars for {bars['ticker'].nunique()} tickers")
    print(f"Date range: {bars['trading_date'].min()} to {bars['trading_date'].max()}")
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
