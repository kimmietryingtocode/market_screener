"""Stage 2: inside each winning sector, rank industries by index momentum.

Yahoo publishes an index (^YH...) for every industry, so industries get
the exact same momentum treatment as the sector ETFs — no scraping.
"""

import pandas as pd
import yfinance as yf

from . import data, metrics


def _sector_industries(sector_key: str, min_market_weight: float) -> pd.DataFrame:
    listing = yf.Sector(sector_key).industries
    listing = listing[listing["market weight"] >= min_market_weight]
    return listing.rename(columns={"market weight": "market_weight"})


def scan_industries(selected_sectors: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Score industries within each selected sector; returns all scanned
    industries sorted per sector, with a `selected` flag on each sector's top N."""
    period: str = config["universe"]["history_period"]
    top_n: int = config["industry_scan"]["top_n_per_sector"]
    min_weight: float = config["industry_scan"]["min_market_weight"]

    frames = []
    for sector in selected_sectors.itertuples():
        listing = _sector_industries(sector.sector_key, min_weight)
        if listing.empty:
            print(f"  warning: no industries found for {sector.sector}, skipping")
            continue

        history = data.download_history(list(listing["symbol"]) + [sector.sector_etf], period)
        sector_close = data.close_series(history, sector.sector_etf)

        rows = []
        for industry_key, industry in listing.iterrows():
            close = data.close_series(history, industry["symbol"])
            if close.empty:
                print(f"  warning: no history for industry index {industry['name']}, skipping")
                continue
            rows.append(
                {
                    "sector": sector.sector,
                    "industry_key": industry_key,
                    "industry": industry["name"],
                    "market_weight": industry["market_weight"],
                    # relative strength here is vs the industry's own sector ETF,
                    # so it measures leadership within the sector
                    **metrics.momentum_metrics(close, sector_close),
                }
            )

        frame = pd.DataFrame(rows)
        frame["score"] = metrics.composite_score(frame, config["score_weights"])
        frame = frame.sort_values("score", ascending=False).reset_index(drop=True)
        frame["selected"] = frame.index < top_n
        frames.append(frame)

    if not frames:
        raise RuntimeError("industry scan produced no results")
    return pd.concat(frames, ignore_index=True)
