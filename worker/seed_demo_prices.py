"""One-off: load the normalized demo price CSV into securities/price_bars.

Not part of the production ingestion path (that's worker/ingest_prices.py,
still planned). This exists to seed the local dev database with real price
history so the create-backtest -> run worker -> fetch results loop can be
exercised end to end without a real ingestion pipeline yet.
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

import psycopg


def seed(connection, csv_path: Path) -> int:
    with csv_path.open(newline="") as handle:
        rows = [(r["ticker"], r["date"], r["adjusted_close"]) for r in csv.DictReader(handle)]

    tickers = sorted({ticker for ticker, _, _ in rows})
    with connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO securities (ticker) VALUES (%s) ON CONFLICT (ticker) DO NOTHING",
            [(t,) for t in tickers],
        )
        cursor.execute("SELECT id, ticker FROM securities WHERE ticker = ANY(%s)", (tickers,))
        security_ids = {ticker: sid for sid, ticker in cursor.fetchall()}

        cursor.executemany(
            """
            INSERT INTO price_bars (security_id, trading_date, adjusted_close)
            VALUES (%s, %s, %s)
            ON CONFLICT (security_id, trading_date) DO UPDATE SET adjusted_close = EXCLUDED.adjusted_close
            """,
            [(security_ids[ticker], trading_date, adjusted_close) for ticker, trading_date, adjusted_close in rows],
        )
    return len(rows)


if __name__ == "__main__":
    database_url = os.environ.get("DATABASE_URL") or sys.argv[1]
    csv_path = Path(sys.argv[2] if len(sys.argv) > 2 else "data/backtest/test-2025/prices.csv")
    with psycopg.connect(database_url, autocommit=True) as connection:
        count = seed(connection, csv_path)
    print(f"seeded {count} price rows from {csv_path}")
