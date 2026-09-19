"""Stage 3: pull candidate companies from each winning industry, filter
for tradability, and rank them by the same momentum math.

Candidates come from Yahoo's per-industry leader tables (top performing +
top growth). The output is deliberately a research shortlist, not a
portfolio: final judgment happens after the FactSet deep dive.
"""

import pandas as pd
import yfinance as yf

from . import data, metrics
from .metrics import THREE_MONTHS


def _industry_candidates(industry_key: str, per_table: int) -> pd.DataFrame:
    """Union of the industry's leader tables: index=symbol, columns name/source."""
    industry = yf.Industry(industry_key)
    tables = {
        "top_performing": industry.top_performing_companies,
        "top_growth": industry.top_growth_companies,
    }

    candidates: dict[str, dict] = {}
    for source, table in tables.items():
        if table is None or table.empty:
            continue
        for symbol, row in table.head(per_table).iterrows():
            if symbol in candidates:
                candidates[symbol]["source"] += f"+{source}"
            else:
                candidates[symbol] = {"name": row.get("name", symbol), "source": source}

    frame = pd.DataFrame.from_dict(candidates, orient="index")
    frame.index.name = "ticker"
    return frame


def _market_caps(tickers: list[str]) -> dict[str, float]:
    caps: dict[str, float] = {}
    bundle = yf.Tickers(" ".join(tickers))
    for ticker in tickers:
        try:
            caps[ticker] = float(bundle.tickers[ticker].fast_info["marketCap"])
        except Exception:
            caps[ticker] = float("nan")
    return caps


def scan_companies(selected_industries: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Returns the filtered, scored shortlist across all selected industries."""
    period: str = config["universe"]["history_period"]
    benchmark: str = config["universe"]["benchmark"]
    cfg = config["company_scan"]
    filters = cfg["filters"]

    frames = []
    for industry in selected_industries.itertuples():
        candidates = _industry_candidates(industry.industry_key, cfg["candidates_per_table"])
        if candidates.empty:
            print(f"  warning: no leader tables for {industry.industry}, skipping")
            continue

        tickers = list(candidates.index)
        history = data.download_history(tickers + [benchmark], period)
        benchmark_close = data.close_series(history, benchmark)

        rows = []
        for ticker, candidate in candidates.iterrows():
            close = data.close_series(history, ticker)
            if close.empty:
                continue

            price = close.iloc[-1]
            if price < filters["min_price"]:
                continue

            volume = history["Volume"].get(ticker)
            dollar_volume = (
                (close * volume).dropna().tail(THREE_MONTHS).mean()
                if volume is not None
                else float("nan")
            )
            if not dollar_volume >= filters["min_avg_daily_dollar_volume"]:
                continue

            rows.append(
                {
                    "ticker": ticker,
                    "company": candidate["name"],
                    "sector": industry.sector,
                    "industry": industry.industry,
                    "source": candidate["source"],
                    "price": price,
                    "avg_daily_dollar_volume": dollar_volume,
                    **metrics.momentum_metrics(close, benchmark_close),
                }
            )

        if not rows:
            print(f"  warning: no {industry.industry} candidates survived filters")
            continue

        frame = pd.DataFrame(rows)

        # market cap needs one request per ticker, so only check survivors
        caps = _market_caps(list(frame["ticker"]))
        frame["market_cap"] = frame["ticker"].map(caps)
        frame = frame[
            frame["market_cap"].isna()
            | (frame["market_cap"] >= filters["min_market_cap_usd"])
        ]
        if frame.empty:
            print(f"  warning: no {industry.industry} candidates above market cap floor")
            continue

        frame["score"] = metrics.composite_score(frame, config["score_weights"])
        frame = frame.sort_values("score", ascending=False).head(cfg["top_n_per_industry"])
        frames.append(frame)

    if not frames:
        raise RuntimeError("company scan produced no candidates")

    shortlist = pd.concat(frames, ignore_index=True)
    # a company can lead two industries; keep its strongest row
    shortlist = (
        shortlist.sort_values("score", ascending=False)
        .drop_duplicates(subset="ticker")
        .reset_index(drop=True)
    )
    shortlist = shortlist.rename(columns={"score": "within_industry_score"})
    shortlist["score"] = metrics.composite_score(shortlist, config["score_weights"])
    return shortlist.sort_values(
        ["score", "ticker"], ascending=[False, True], kind="stable"
    ).reset_index(drop=True)
