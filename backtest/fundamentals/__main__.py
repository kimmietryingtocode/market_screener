"""Run with python -m backtest.fundamentals; exports require decision timestamps."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from .api import collect, write_parquet
from .core import canonical, utc
from .edgar import EdgarSource, flatten_facts, normalize_archive
from .http import JsonClient
from .prices import YFinancePrices
from backtest.workflow import select_fundamental_snapshots


def main(argv: list[str] | None = None) -> int:
    """Build model snapshots and separate audit/reconciliation Parquet files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tickers", nargs="*", default=["AAPL", "MSFT", "KO"])
    parser.add_argument("--as-of", action="append", help="Timezone-aware decision timestamp; repeat for multiple snapshots")
    parser.add_argument("--quarterly", nargs=2, metavar=("START", "END"), help="Add 4:00 p.m. New York calendar-quarter cutoffs in a YYYY-MM-DD range")
    parser.add_argument("--sources", nargs="+", default=["edgar"], help="Only edgar remains supported")
    parser.add_argument("--policy", choices=["latest", "earliest"], default="latest")
    parser.add_argument("--output", type=Path, default=Path("data/fundamentals/output"))
    parser.add_argument("--cache", type=Path, default=Path("data/fundamentals/cache"))
    parser.add_argument("--cik-map", type=Path, help="CSV with ticker,cik columns for historical symbols")
    parser.add_argument("--pit-only", action="store_true", help="Write compact EBITDA/FCF vintages for the PIT backtester")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--prices", choices=["yfinance"])
    parser.add_argument("--compare", nargs="+", metavar="CONCEPT")
    parser.add_argument("--tolerance", type=float, default=0.01)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        if args.sources != ["edgar"]:
            parser.error("vendor fundamentals were retired; use EDGAR plus manual Summary import")
        if args.compare:
            parser.error("automatic provider comparison was retired; use compare_frames(edgar, summary)")
        requested = list(args.as_of or [])
        if args.quarterly:
            start, end = map(pd.Timestamp, args.quarterly)
            if pd.isna(start) or pd.isna(end) or end < start:
                parser.error("--quarterly requires a valid START not later than END")
            requested.extend(
                (stamp + pd.Timedelta(16, unit="h")).tz_localize("America/New_York")
                for stamp in pd.date_range(start.normalize(), end.normalize(), freq="QE")
            )
        if not requested:
            parser.error("at least one --as-of or --quarterly range is required")
        cutoffs = sorted(set(utc(t) for t in requested))
        if any(t > pd.Timestamp.now(tz="UTC") for t in cutoffs):
            parser.error("as_of cannot be in the future")
        client = JsonClient(args.cache, refresh=args.refresh, offline=args.offline)
        cik_map = None
        if args.cik_map:
            mapping = pd.read_csv(args.cik_map, dtype=str)
            if not {"ticker", "cik"}.issubset(mapping.columns):
                parser.error("--cik-map requires ticker and cik columns")
            mapping["ticker"] = mapping["ticker"].str.upper().str.replace(".", "-", regex=False)
            mapping["cik"] = mapping["cik"].str.replace(r"\D", "", regex=True).str.zfill(10)
            if mapping[["ticker", "cik"]].isna().any().any() or mapping["ticker"].duplicated().any():
                parser.error("--cik-map ticker and CIK values must be nonblank and unique")
            cik_map = dict(mapping[["ticker", "cik"]].itertuples(index=False, name=None))
            args.tickers = list(cik_map)
        price_source = YFinancePrices(client) if args.prices else None
        if args.pit_only:
            if args.sources != ["edgar"] or args.prices or args.compare or cik_map is None:
                parser.error("--pit-only requires --sources edgar and --cik-map, without prices or comparison")
            identity = [
                "ticker", "as_of", "period_start", "period_end", "fiscal_year",
                "fiscal_period", "source", "accession", "published_at", "available_at",
                "filing_date", "availability_precision",
            ]
            concepts = [
                "operating_income", "depreciation_and_amortization", "operating_cash_flow",
                "capex", "ebitda", "free_cash_flow", "ebitda_ttm", "free_cash_flow_ttm",
            ]
            provenance = [
                column
                for column in canonical().columns
                if any(column.startswith(f"{concept}_") for concept in concepts)
            ]
            columns = list(dict.fromkeys(identity + concepts + provenance))
            rows = []
            source = EdgarSource(as_of=cutoffs[-1], client=client, policy=args.policy, cik_map=cik_map)
            for ticker in args.tickers:
                try:
                    payload, filings = source.load(ticker)
                    archive = flatten_facts(payload, filings)
                    for cutoff in cutoffs:
                        frame, _, _ = normalize_archive(archive, ticker, as_of=cutoff, policy=args.policy)
                        snapshot = select_fundamental_snapshots(frame, cutoff, [ticker])
                        if not snapshot.empty and snapshot.iloc[0]["fundamental_status"] != "unavailable":
                            rows.append(snapshot.reindex(columns=columns + ["fundamental_status"]))
                except (ValueError, KeyError, TypeError, RuntimeError, OSError) as exc:
                    logging.warning("edgar %s skipped: %s", ticker, exc)
            result = (
                pd.concat(rows, ignore_index=True)
                if rows
                else pd.DataFrame(columns=columns + ["fundamental_status"])
            )
            write_parquet(result, args.output / "fundamentals.parquet")
            logging.info("Wrote %d compact PIT vintages to %s", len(result), args.output)
            return 0 if len(result) else 2
        output: dict[str, list[pd.DataFrame]] = {"fundamentals": [], "superseded": [], "archive": [], "snapshots": []}
        for cutoff in cutoffs:
            source = EdgarSource(as_of=cutoff, client=client, policy=args.policy, cik_map=cik_map)
            frame, audits = collect(args.tickers, source, as_of=cutoff, price_source=price_source)
            output["fundamentals"].append(frame)
            for name, table in audits.items():
                if not table.empty:
                    output[name].append(table)
        for name, frames in output.items():
            records = [row for part in frames for row in part.to_dict("records")]
            frame = canonical(records) if name == "fundamentals" else pd.DataFrame(records)
            if name == "archive" and not frame.empty:
                frame = frame.drop(columns="as_of").drop_duplicates()
            write_parquet(frame, args.output / f"{name}.parquet")
        count = sum(len(f) for f in output["fundamentals"])
        logging.info("Wrote %d eligible model rows to %s", count, args.output)
        return 0 if count else 2
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
