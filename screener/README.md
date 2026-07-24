# Weekly market screener

Narrows the whole US market down to a short list of companies worth
researching in FactSet. Runs on free Yahoo Finance data and **stops at the
FactSet lookup list** — fundamental scoring and backtesting are separate,
later stages that consume the FactSet export.

## The funnel

```
11 GICS sectors          (SPDR ETF momentum vs SPY)
  -> top 3 sectors
industries per sector    (Yahoo ^YH industry-index momentum vs the sector ETF)
  -> top 3 per sector
companies per industry   (Yahoo leader tables, filtered + momentum-ranked)
  -> top 5 per industry
FactSet lookup list      (~30-45 tickers)
```

Every level is scored with the same math ([pipeline/metrics.py](pipeline/metrics.py)):
1m/3m/6m trailing returns, 3-month relative strength against the level's
benchmark, 3-month annualized volatility and max drawdown, blended into a
0–100 percentile-rank composite. Weights live in [config.yaml](config.yaml).

Company candidates must pass tradability filters before ranking: price ≥ $5,
market cap ≥ $1B, average daily dollar volume ≥ $5M (all configurable).

Note the company score is a *within-industry* percentile — it says "leader of
its industry", not "best stock overall". The list is a research queue, not a
portfolio.

## Run it

```bash
cd screener
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python run_screener.py
```

Takes a couple of minutes (it downloads a year of history at each level).
Output lands in `data/screener/<YYYY-MM-DD>/` at the repo root:

| File | What it is |
|---|---|
| `factset_lookup.csv` | The shortlist with the metrics that justified each pick |
| `tickers.txt` | `TICKER-US` symbols, one per line — paste into FactSet's identifier lookup |
| `sector_scores.csv` | Full sector ranking (audit trail) |
| `industry_scores.csv` | Full industry ranking (audit trail) |

## Next step (out of scope here)

Take `tickers.txt` into FactSet, export fundamentals/estimates for those
names, and feed that export to the scoring + backtesting stage.
