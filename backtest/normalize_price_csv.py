"""Normalize a Yahoo Finance CSV into the C++ backtest price contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import pandas as pd


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize(
    source: Path,
    output: Path,
    *,
    price_column: str | None = None,
    price_basis: str = "declared_by_user",
) -> dict[str, object]:
    frame = pd.read_csv(source)
    columns = {str(column).strip().lower(): column for column in frame.columns}
    ticker_column = columns.get("ticker")
    date_column = columns.get("date") or columns.get("trading_date")
    selected_price = price_column or ("adjusted_close" if "adjusted_close" in columns else "close")
    value_column = columns.get(selected_price.lower())
    missing = [name for name, value in (("ticker", ticker_column), ("date/trading_date", date_column), (selected_price, value_column)) if value is None]
    if missing:
        raise ValueError("price file is missing: " + ", ".join(missing))

    normalized = pd.DataFrame(
        {
            "date": pd.to_datetime(frame[date_column], errors="coerce", utc=True).dt.date,
            "ticker": frame[ticker_column].astype("string").str.strip().str.upper(),
            "adjusted_close": pd.to_numeric(frame[value_column], errors="coerce"),
        }
    )
    if normalized.isna().any().any():
        raise ValueError("price file contains blank or malformed ticker/date/price values")
    if (~normalized["adjusted_close"].map(math.isfinite) | normalized["adjusted_close"].le(0)).any():
        raise ValueError("price file contains nonpositive or nonfinite prices")
    if normalized.duplicated(["date", "ticker"]).any():
        raise ValueError("price file contains duplicate ticker/date rows")

    normalized = normalized.sort_values(["date", "ticker"]).reset_index(drop=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    normalized.to_csv(output, index=False, date_format="%Y-%m-%d")
    manifest = {
        "source_file": str(source.resolve()),
        "source_sha256": sha256(source),
        "normalized_file": str(output.resolve()),
        "normalized_sha256": sha256(output),
        "source_price_column": selected_price,
        "source_price_basis": price_basis,
        "rows": len(normalized),
        "tickers": sorted(normalized["ticker"].unique().tolist()),
        "date_start": normalized["date"].min().isoformat(),
        "date_end": normalized["date"].max().isoformat(),
    }
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--price-column", choices=["close", "adjusted_close"])
    parser.add_argument("--price-basis", default="declared_by_user")
    args = parser.parse_args()
    if not args.input.is_file():
        raise SystemExit(f"input file does not exist: {args.input}")
    print(json.dumps(normalize(args.input, args.output, price_column=args.price_column, price_basis=args.price_basis), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
