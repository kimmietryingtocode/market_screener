"""Create a configurable monthly target schedule for engine smoke tests."""

from __future__ import annotations

import argparse
import math
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from backtest.calendar import month_end_signals


CONTRACT = ["signal_at", "execution_date", "security_id", "target_weight", "provenance_id"]


def make_targets(
    prices_path: Path,
    output: Path,
    *,
    symbols: list[str],
    benchmark: str,
    signal_start: date,
    end: date,
    target_weight: float,
    signal_time: str = "18:00",
    timezone: str = "America/New_York",
    provenance_id: str = "configurable-test-schedule",
) -> pd.DataFrame:
    prices = pd.read_csv(prices_path, usecols=["date", "ticker", "adjusted_close"])
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce").dt.date
    prices["ticker"] = prices["ticker"].astype("string").str.strip().str.upper()
    prices["adjusted_close"] = pd.to_numeric(prices["adjusted_close"], errors="coerce")
    if prices.isna().any().any():
        raise ValueError("normalized prices contain blank or malformed values")
    if (~prices["adjusted_close"].map(math.isfinite) | prices["adjusted_close"].le(0)).any():
        raise ValueError("normalized prices contain nonpositive or nonfinite values")
    required = {*symbols, benchmark}
    available = set(prices["ticker"])
    if missing := sorted(required - available):
        raise ValueError("normalized prices are missing: " + ", ".join(missing))
    if prices.duplicated(["date", "ticker"]).any():
        raise ValueError("normalized prices contain duplicate date/ticker rows")
    if target_weight <= 0 or len(symbols) * target_weight > 1:
        raise ValueError("target weights must be positive and fit within 100%")
    if not symbols or len(set(symbols)) != len(symbols):
        raise ValueError("symbols must be a non-empty list without duplicates")

    dates = {ticker: set(prices.loc[prices["ticker"].eq(ticker), "date"]) for ticker in required}
    pairs = month_end_signals(signal_start, end)
    zone = ZoneInfo(timezone)
    hour, minute = map(int, signal_time.split(":"))
    rows: list[dict[str, object]] = []
    for signal, execution in pairs:
        signal_date, execution_date = signal.date(), execution.date()
        if any(signal_date not in dates[ticker] or execution_date not in dates[ticker] for ticker in required):
            raise ValueError(f"missing required prices around {signal_date} -> {execution_date}")
        signal_at = datetime.combine(signal_date, time(hour, minute), tzinfo=zone).isoformat()
        rows.extend(
            {
                "signal_at": signal_at,
                "execution_date": execution_date,
                "security_id": ticker,
                "target_weight": target_weight,
                "provenance_id": provenance_id,
            }
            for ticker in symbols
        )

    result = pd.DataFrame(rows, columns=CONTRACT)
    if result.empty:
        raise ValueError("no target executions were generated")
    output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output, index=False, date_format="%Y-%m-%d")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", type=Path, required=True)
    parser.add_argument("--symbols", required=True, help="comma-separated target securities")
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--signal-start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--target-weight", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--signal-time", default="18:00")
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--provenance-id", default="configurable-test-schedule")
    args = parser.parse_args()
    symbols = [value.strip().upper() for value in args.symbols.split(",") if value.strip()]
    targets = make_targets(
        args.prices,
        args.output,
        symbols=symbols,
        benchmark=args.benchmark.strip().upper(),
        signal_start=date.fromisoformat(args.signal_start),
        end=date.fromisoformat(args.end),
        target_weight=args.target_weight,
        signal_time=args.signal_time,
        timezone=args.timezone,
        provenance_id=args.provenance_id,
    )
    print(f"wrote {len(targets)} targets to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
