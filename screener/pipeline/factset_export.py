"""Stage 4 (the end of this pipeline): write the FactSet lookup list.

Produces a dated folder with:
  factset_lookup.csv   the shortlist with the metrics that justified each pick
  tickers.txt          FactSet-style symbols (TICKER-US), one per line,
                       ready to paste into a FactSet identifier lookup
  sector_scores.csv    full stage-1 ranking, for the audit trail
  industry_scores.csv  full stage-2 ranking, for the audit trail
"""

from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

EXPORT_COLUMNS = [
    "factset_symbol",
    "ticker",
    "company",
    "sector",
    "industry",
    "score",
    "within_industry_score",
    "return_1m",
    "return_3m",
    "return_6m",
    "rs_3m",
    "volatility_3m",
    "drawdown_3m",
    "price",
    "market_cap",
    "avg_daily_dollar_volume",
    "source",
]


def to_factset_symbol(ticker: str) -> str:
    """AAPL -> AAPL-US, BRK-B -> BRK.B-US (Yahoo uses '-' for share classes)."""
    return f"{ticker.replace('-', '.')}-US"


def select_review_queue(shortlist: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Select the globally ranked review queue subject to concentration caps."""
    cfg = config["company_scan"]["review_queue"]
    sector_counts: dict[str, int] = {}
    industry_counts: dict[str, int] = {}
    selected = []
    for row in shortlist.sort_values(
        ["score", "ticker"], ascending=[False, True], kind="stable"
    ).itertuples():
        if sector_counts.get(row.sector, 0) >= int(cfg["max_per_sector"]):
            continue
        if industry_counts.get(row.industry, 0) >= int(cfg["max_per_industry"]):
            continue
        selected.append(row.Index)
        sector_counts[row.sector] = sector_counts.get(row.sector, 0) + 1
        industry_counts[row.industry] = industry_counts.get(row.industry, 0) + 1
        if len(selected) == int(cfg["top_n"]):
            break

    queue = shortlist.loc[selected].copy().reset_index(drop=True)
    queue.insert(0, "recommendation_rank", queue.index + 1)
    return queue


def write_exports(
    shortlist: pd.DataFrame,
    sector_scores: pd.DataFrame,
    industry_scores: pd.DataFrame,
    output_root: Path,
    config: dict,
) -> Path:
    out_dir = output_root / date.today().isoformat()
    if (out_dir / "factset_lookup.csv").exists():
        out_dir = output_root / f"{date.today().isoformat()}-{datetime.now(UTC):%H%M%S%fZ}"
    out_dir.mkdir(parents=True, exist_ok=True)

    shortlist = shortlist.copy()
    shortlist["factset_symbol"] = shortlist["ticker"].map(to_factset_symbol)
    shortlist[EXPORT_COLUMNS].round(4).to_csv(out_dir / "factset_lookup.csv", index=False)

    (out_dir / "tickers.txt").write_text("\n".join(shortlist["factset_symbol"]) + "\n")

    sector_scores.round(4).to_csv(out_dir / "sector_scores.csv", index=False)
    industry_scores.round(4).to_csv(out_dir / "industry_scores.csv", index=False)

    queue = select_review_queue(shortlist, config)
    review_dir = out_dir / "top10_review"
    review_dir.mkdir()
    queue_columns = ["recommendation_rank", *EXPORT_COLUMNS]
    queue[queue_columns].round(4).to_csv(review_dir / "factset_lookup.csv", index=False)
    (review_dir / "tickers.txt").write_text(
        "\n".join(queue["factset_symbol"]) + "\n"
    )

    return out_dir
