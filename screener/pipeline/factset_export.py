"""Stage 4 (the end of this pipeline): write the FactSet lookup list.

Produces a dated folder with:
  factset_lookup.csv   the shortlist with the metrics that justified each pick
  tickers.txt          FactSet-style symbols (TICKER-US), one per line,
                       ready to paste into a FactSet identifier lookup
  sector_scores.csv    full stage-1 ranking, for the audit trail
  industry_scores.csv  full stage-2 ranking, for the audit trail
"""

from datetime import date
from pathlib import Path

import pandas as pd

EXPORT_COLUMNS = [
    "factset_symbol",
    "ticker",
    "company",
    "sector",
    "industry",
    "score",
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


def write_exports(
    shortlist: pd.DataFrame,
    sector_scores: pd.DataFrame,
    industry_scores: pd.DataFrame,
    output_root: Path,
) -> Path:
    out_dir = output_root / date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)

    shortlist = shortlist.copy()
    shortlist["factset_symbol"] = shortlist["ticker"].map(to_factset_symbol)
    shortlist[EXPORT_COLUMNS].round(4).to_csv(out_dir / "factset_lookup.csv", index=False)

    (out_dir / "tickers.txt").write_text("\n".join(shortlist["factset_symbol"]) + "\n")

    sector_scores.round(4).to_csv(out_dir / "sector_scores.csv", index=False)
    industry_scores.round(4).to_csv(out_dir / "industry_scores.csv", index=False)

    return out_dir
