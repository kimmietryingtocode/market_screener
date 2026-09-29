"""Download per-company fundamentals via yfinance into fundamental_snapshots,
and backfill securities metadata (company name, sector, industry) while we're
there.

IMPORTANT — filing_date is APPROXIMATED here. yfinance exposes the fiscal
period end (`mostRecentQuarter`) but not the date the filing actually became
public, so this module estimates it as period_end + FILING_LAG_DAYS and marks
every row with data_source='yfinance-estimated-filing-date'.

That means these rows are fit for the rankings/screening surface (which asks
"what do we know now?") and are NOT fit for point-in-time backtest gating
(which asks "what was knowable on date X?"). Backtests must keep using
backtest/fundamentals/ (real EDGAR accessions and filing dates) — see the
"Every fundamental record needs filing_date to prevent look-ahead bias" rule
in worker/README.md. Anything reading this table for a backtest should filter
data_source explicitly.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import psycopg
import yfinance as yf

from screener.ingest_prices import DEFAULT_TICKERS, parse_tickers

logger = logging.getLogger(__name__)

DATA_SOURCE = "yfinance-estimated-filing-date"
# SEC 10-Q deadline is 40-45 days after quarter end for most filers; being
# conservative here keeps the estimate on the "not knowable yet" side.
FILING_LAG_DAYS = 45


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def extract_snapshot(info: dict[str, Any]) -> dict[str, Any] | None:
    """Map a yfinance `info` dict onto the fundamental_snapshots columns.

    Returns None when the period can't be determined — a row with no fiscal
    period is worse than no row, since period_end is part of the identity of
    a snapshot.
    """
    epoch = info.get("mostRecentQuarter")
    if not epoch:
        return None
    period_end = datetime.fromtimestamp(int(epoch), tz=timezone.utc).date()

    debt_to_equity = _finite(info.get("debtToEquity"))
    return {
        "period_end": period_end,
        "filing_date": period_end + timedelta(days=FILING_LAG_DAYS),
        "revenue": _finite(info.get("totalRevenue")),
        "revenue_growth": _finite(info.get("revenueGrowth")),
        "earnings_growth": _finite(info.get("earningsGrowth")),
        "operating_margin": _finite(info.get("operatingMargins")),
        "free_cash_flow": _finite(info.get("freeCashflow")),
        # yfinance reports this as a percentage (78.4 == 78.4%); every other
        # ratio in this table is a fraction, so normalize it.
        "debt_to_equity": debt_to_equity / 100.0 if debt_to_equity is not None else None,
    }


def upsert_snapshot(connection: Any, ticker: str, info: dict[str, Any], snapshot: dict[str, Any]) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO securities (ticker) VALUES (%s) ON CONFLICT (ticker) DO NOTHING",
            (ticker,),
        )
        cursor.execute(
            """
            UPDATE securities SET
                company_name = COALESCE(%s, company_name),
                sector = COALESCE(%s, sector),
                industry = COALESCE(%s, industry)
            WHERE ticker = %s
            RETURNING id
            """,
            (info.get("shortName"), info.get("sector"), info.get("industry"), ticker),
        )
        security_id = cursor.fetchone()[0]

        cursor.execute(
            """
            INSERT INTO fundamental_snapshots (
                security_id, period_end, filing_date, revenue, revenue_growth,
                earnings_growth, operating_margin, free_cash_flow, debt_to_equity, data_source
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (security_id, period_end, filing_date, data_source) DO UPDATE SET
                revenue = EXCLUDED.revenue,
                revenue_growth = EXCLUDED.revenue_growth,
                earnings_growth = EXCLUDED.earnings_growth,
                operating_margin = EXCLUDED.operating_margin,
                free_cash_flow = EXCLUDED.free_cash_flow,
                debt_to_equity = EXCLUDED.debt_to_equity
            """,
            (
                security_id,
                snapshot["period_end"],
                snapshot["filing_date"],
                snapshot["revenue"],
                snapshot["revenue_growth"],
                snapshot["earnings_growth"],
                snapshot["operating_margin"],
                snapshot["free_cash_flow"],
                snapshot["debt_to_equity"],
                DATA_SOURCE,
            ),
        )


def ingest(connection: Any, tickers: list[str]) -> int:
    """Ingest one snapshot per ticker. A ticker the provider can't answer for
    is logged and skipped, not fatal — one delisted symbol shouldn't cost us
    the whole universe."""
    ingested = 0
    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).info
        except Exception:
            logger.warning("could not fetch fundamentals for %s", ticker, exc_info=True)
            continue

        snapshot = extract_snapshot(info)
        if snapshot is None:
            logger.warning("skipping %s: no fiscal period reported", ticker)
            continue

        upsert_snapshot(connection, ticker, info, snapshot)
        ingested += 1
    return ingested


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", help="comma-separated ticker list; defaults to the starter universe")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=None,
        help="seconds between runs; omit to ingest once and exit",
    )
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")

    logging.basicConfig(level=logging.INFO)
    tickers = parse_tickers(args.tickers) if args.tickers else DEFAULT_TICKERS
    with psycopg.connect(args.database_url, autocommit=True) as connection:
        while True:
            try:
                count = ingest(connection, tickers)
                logger.info("ingested fundamentals for %s of %s tickers", count, len(tickers))
            except Exception:  # noqa: BLE001 - a bad run must not kill a scheduled loop
                logger.exception("fundamentals ingestion run failed")
            if args.interval_seconds is None:
                break
            time.sleep(args.interval_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
