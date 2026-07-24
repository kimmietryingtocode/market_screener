"""Stage 1: rank the 11 GICS sectors by SPDR ETF momentum vs the benchmark."""

import pandas as pd

from . import data, metrics


def scan_sectors(config: dict) -> pd.DataFrame:
    """Score every sector ETF; returns all sectors sorted by score with a
    `selected` flag on the top N."""
    sectors: dict[str, dict] = config["sectors"]
    benchmark: str = config["universe"]["benchmark"]
    period: str = config["universe"]["history_period"]

    history = data.download_history(list(sectors) + [benchmark], period)
    benchmark_close = data.close_series(history, benchmark)

    rows = []
    for etf, info in sectors.items():
        close = data.close_series(history, etf)
        if close.empty:
            print(f"  warning: no price history for {etf}, skipping")
            continue
        rows.append(
            {
                "sector_etf": etf,
                "sector_key": info["key"],
                "sector": info["name"],
                **metrics.momentum_metrics(close, benchmark_close),
            }
        )

    frame = pd.DataFrame(rows)
    frame["score"] = metrics.composite_score(frame, config["score_weights"])
    frame = frame.sort_values("score", ascending=False).reset_index(drop=True)
    frame["selected"] = frame.index < config["sector_scan"]["top_n"]
    return frame
