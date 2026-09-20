# Weekly market screener

Narrows the whole US market down to a short list of companies worth
researching in FactSet. Runs on free Yahoo Finance data and **stops at the
FactSet lookup list** — fundamental scoring and backtesting are separate,
later stages that consume the FactSet export.

## The funnel

```
11 GICS sectors          (SPDR ETF momentum vs SPY)
  -> top 5 sectors
industries per sector    (Yahoo ^YH industry-index momentum vs the sector ETF)
  -> top 2 per sector
companies per industry   (Yahoo leader tables, filtered + momentum-ranked)
  -> top 5 per industry
global company ranking   (same 40/30/20/10 score across the shortlist)
  -> top 10, max 3 per sector and 2 per industry
FactSet review queue
```

Every level is scored with the same math ([pipeline/metrics.py](pipeline/metrics.py)):
1m/3m/6m trailing returns, 3-month relative strength against the level's
benchmark, 3-month annualized volatility and max drawdown, blended into a
0–100 percentile-rank composite. Weights live in [config.yaml](config.yaml).

Company candidates must pass tradability filters before ranking: price ≥ $5,
market cap ≥ $1B, average daily dollar volume ≥ $5M (all configurable).

The within-industry score chooses each industry's candidates. Those survivors
are rescored together so the final score is comparable across industries. The
top-ten review queue is capped at three names per sector and two per industry.

## Run it

```bash
python3 -m venv screener/venv
screener/venv/bin/pip install -r screener/requirements.txt
screener/venv/bin/python -m backtest sweep
```

Takes a couple of minutes (it downloads a year of history at each level).
Output lands in `data/screener/<YYYY-MM-DD>/` at the repo root:

| File | What it is |
|---|---|
| `factset_lookup.csv` | The shortlist with the metrics that justified each pick |
| `tickers.txt` | `TICKER-US` symbols, one per line — paste into FactSet's identifier lookup |
| `sector_scores.csv` | Full sector ranking (audit trail) |
| `industry_scores.csv` | Full industry ranking (audit trail) |
| `top10_review/factset_lookup.csv` | Diversified top-ten FactSet review queue |
| `top10_review/tickers.txt` | FactSet symbols for the review queue |

## Manual FactSet stage

Take `tickers.txt` into FactSet and complete the manual research review. Every
reviewed company uses a Standardized Summary export. Summary files may be
CSV, XLSX, or XLS and may contain many companies.
A workbook may contain multiple company sheets, and combined CSV reports may
contain multiple company sections. Filenames can be anything.

The importer resolves every section to the Yahoo shortlist in a safe order:
exact FactSet identifier, ticker variants such as `PBF`/`PBF-US`, then a unique
regex match of the FactSet report title against Yahoo's company name. Legal
suffix and share-class wording such as `Inc.`, `Corporation`, and `Class A` is
ignored. Ambiguous or conflicting matches remain hard validation errors.

Place the approved CSVs in one directory, then resume the pipeline:

```bash
screener/venv/bin/python -m backtest import-summary \
  --screen-run data/screener/2026-07-06/top10_review \
  --factset-dir /path/to/factset-summary-files
```

The first successful import records its UTC observation timestamp automatically;
it cannot be backdated. Identical reimports reuse the hash-addressed package and
do not extend its 120-day expiry. Summary history is today's restated view and
is never copied backward into historical decisions.

One Summary CSV or workbook may contain multiple companies. The Summary drives a 35% growth, 35%
operating-quality, and 30% financial-strength diagnostic score. The original
market score remains the ordering. Live eligibility requires the latest
available outlook to have both positive EBITDA and positive free cash flow;
forecast-dependent recoveries are retained but explicitly warned.
Shortlisted names with no Summary are recorded as manual rejections.

Each unique set of inputs produces an immutable, hash-addressed directory
under `<screen-run>/factset/`:

| File | What it is |
|---|---|
| `approved_candidates.csv` | Names passing the diagnostic financial gate; explicit review is still required |
| `factset_actuals.csv` | Summary annual history and estimates, actual/forecast flags, restatements, and availability date |
| `validation_report.csv` | Audit passes, warnings, errors, and manual rejections |
| `manifest.json` | Input hashes, configuration hash, counts, dates, and pipeline version |
| `configuration.yaml` | Exact pipeline configuration used for the import |
| `raw/` | Exact copies of the submitted FactSet CSV/Excel files |

If any supplied file fails, the command exits with status 2 and writes the
audit artifacts, but it does not publish `approved_candidates.csv`. Fix the
reported files and run the command again.

The command also writes a complete `review_template.csv`. Publish
approve/reject/needs-review decisions with `python -m backtest review-summary`;
only that immutable review can feed a forward portfolio. See
[`../backtest/README.md`](../backtest/README.md).
