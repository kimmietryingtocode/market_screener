"""Download daily prices via yfinance and upsert them into price_bars.

This is the real implementation of the "Python ingestion / worker" step at
the top of the architecture doc: market data source -> validate/normalize
-> PostgreSQL. worker/seed_demo_prices.py (loads a static CSV of already-
fetched data) was a stand-in for this while the pipeline didn't exist yet.

Reuses screener/ingest_prices.py's download + normalize functions instead
of re-implementing them (per worker/README.md) — this module only adds the
Postgres upsert and input validation on top.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import time
from typing import Any

import pandas as pd
import psycopg

from screener.ingest_prices import DEFAULT_TICKERS, normalize_price_bars, parse_tickers
from screener.pipeline.data import download_history

logger = logging.getLogger(__name__)


def validate(normalized: pd.DataFrame) -> None:
    if normalized.empty:
        raise ValueError("no price bars were returned to ingest")
    close = normalized["close"]
    if close.isna().any():
        raise ValueError("normalized prices contain blank close values")
    if (~close.map(math.isfinite) | close.le(0)).any():
        raise ValueError("normalized prices contain nonpositive or nonfinite close values")
    if normalized.duplicated(["ticker", "trading_date"]).any():
        raise ValueError("normalized prices contain duplicate ticker/date rows")


def _clean(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return float(value)


def upsert_price_bars(connection: Any, normalized: pd.DataFrame) -> int:
    """Idempotent: rerunning for the same tickers/dates overwrites in place
    rather than duplicating (worker/README.md's "jobs must be idempotent").

    Note on adjusted_close: download_history fetches with auto_adjust=True,
    so yfinance's own "close" is already split/dividend adjusted. This
    module stores that same value in both `close` and `adjusted_close`
    (there is no separate raw-close fetch anywhere in this app) — matching
    the "yahoo_auto_adjust_true" price basis used everywhere else in this
    codebase (see backtest/normalize_price_csv.py).
    """
    tickers = sorted(normalized["ticker"].unique())
    with connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO securities (ticker) VALUES (%s) ON CONFLICT (ticker) DO NOTHING",
            [(ticker,) for ticker in tickers],
        )
        cursor.execute("SELECT id, ticker FROM securities WHERE ticker = ANY(%s)", (tickers,))
        security_ids = {ticker: sid for sid, ticker in cursor.fetchall()}

        rows = [
            (
                security_ids[row.ticker],
                row.trading_date,
                _clean(row.open),
                _clean(row.high),
                _clean(row.low),
                _clean(row.close),
                _clean(row.close),
                None if pd.isna(row.volume) else int(row.volume),
            )
            for row in normalized.itertuples(index=False)
        ]
        cursor.executemany(
            """
            INSERT INTO price_bars (security_id, trading_date, open, high, low, close, adjusted_close, volume)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (security_id, trading_date) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                adjusted_close = EXCLUDED.adjusted_close,
                volume = EXCLUDED.volume
            """,
            rows,
        )
    return len(rows)


def ingest(connection: Any, tickers: list[str], period: str) -> int:
    data = download_history(tickers, period)
    normalized = normalize_price_bars(data)
    validate(normalized)
    return upsert_price_bars(connection, normalized)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", help="comma-separated ticker list; defaults to the starter universe")
    parser.add_argument("--period", default="2y", help="yfinance period, e.g. 1y or 2y")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=None,
        help="seconds between ingestion runs; omit to ingest once and exit",
    )
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")

    logging.basicConfig(level=logging.INFO)
    tickers = parse_tickers(args.tickers) if args.tickers else DEFAULT_TICKERS
    with psycopg.connect(args.database_url, autocommit=True) as connection:
        while True:
            try:
                row_count = ingest(connection, tickers, args.period)
                logger.info("ingested %s price bars for %s tickers", row_count, len(tickers))
            except Exception:  # noqa: BLE001 - a bad fetch must not kill a scheduled loop
                logger.exception("price ingestion run failed")
            if args.interval_seconds is None:
                break
            time.sleep(args.interval_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
