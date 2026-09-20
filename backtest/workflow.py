"""Point-in-time-safe free-data backtest workflow.

Historical mode is explicitly diagnostic: it uses today's cached Yahoo history
for a fixed research list and never imports current FactSet approvals.
Forward mode uses only observed caches and Summary imports visible by `as_of`.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from screener.pipeline.metrics import composite_score, momentum_metrics
from backtest.calendar import CALENDAR_SOURCE, CALENDAR_VERSION, month_end_signals, nyse_sessions


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "screener" / "config.yaml"
DEFAULT_CACHE = REPO_ROOT / "data" / "backtest" / "2026-09-02-current-universe"
DEFAULT_SCREENER_ROOT = REPO_ROOT / "data" / "screener"
CPP_BINARY = REPO_ROOT / "backtest" / "build" / "portfolio_backtest"


@dataclass(frozen=True)
class Paths:
    run_dir: Path
    inputs_dir: Path
    results_dir: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_config(path: Path) -> dict:
    config = yaml.safe_load(path.read_text())
    config.setdefault("backtest", {})
    return config


def _run_paths(output_root: Path, label: str) -> Paths:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = output_root / f"{stamp}-{label}"
    return Paths(run_dir=run_dir, inputs_dir=run_dir / "inputs", results_dir=run_dir / "results")


def _copy_research_inputs(cache_dir: Path, paths: Paths, tickers: list[str]) -> tuple[Path, Path]:
    prices = pd.read_csv(cache_dir / "prices.csv")
    universe = pd.read_csv(cache_dir / "universe.csv")
    subset = universe[universe["ticker"].isin(tickers)].copy()
    if sorted(subset["ticker"]) != sorted(tickers):
        missing = sorted(set(tickers) - set(subset["ticker"]))
        raise ValueError("research universe missing tickers: " + ", ".join(missing))

    needed = set(subset["ticker"]) | set(subset["sector_etf"]) | {"SPY"}
    price_subset = prices[prices["ticker"].isin(needed)].copy()
    counts = price_subset.groupby("ticker")["date"].count()
    missing_prices = sorted(needed - set(counts.index))
    if missing_prices:
        raise ValueError("price cache missing tickers: " + ", ".join(missing_prices))

    paths.inputs_dir.mkdir(parents=True, exist_ok=True)
    universe_path = paths.inputs_dir / "universe.csv"
    prices_path = paths.inputs_dir / "prices.csv"
    subset.sort_values("ticker").to_csv(universe_path, index=False)
    price_subset.sort_values(["date", "ticker"]).to_csv(prices_path, index=False)

    security_master = subset.copy()
    security_master.insert(0, "security_id", security_master["ticker"])
    security_master["effective_from"] = "2020-01-01"
    security_master["effective_to"] = ""
    security_master["evidence"] = "verified from cached Yahoo/FactSet shortlist on 2026-09-02"
    security_master.to_csv(paths.inputs_dir / "security_master.csv", index=False)
    return prices_path, universe_path


def _pivot_prices(prices_path: Path) -> pd.DataFrame:
    prices = pd.read_csv(prices_path, parse_dates=["date"])
    pivot = prices.pivot(index="date", columns="ticker", values="adjusted_close").sort_index()
    return pivot


def _valid_series(
    pivot: pd.DataFrame,
    ticker: str,
    signal_date: pd.Timestamp,
    required_observations: int = 127,
) -> pd.Series | None:
    if ticker not in pivot:
        return None
    series = pivot.loc[:signal_date, ticker]
    recent = series.tail(required_observations)
    if len(recent) < required_observations or recent.isna().any() or recent.index[-1] != signal_date:
        return None
    return series.dropna()


def _above_trend(series: pd.Series, trend_days: int) -> bool:
    if trend_days <= 0:
        return True
    recent = series.tail(trend_days)
    return len(recent) == trend_days and series.iloc[-1] >= recent.mean()


def _basket_index(pivot: pd.DataFrame, tickers: list[str]) -> pd.Series:
    prices = pivot[tickers]
    returns = prices.pct_change(fill_method=None)
    daily = returns.mean(axis=1).where(returns.notna().all(axis=1))
    first = prices.notna().all(axis=1).idxmax() if prices.notna().all(axis=1).any() else None
    if first is None:
        return pd.Series(dtype=float)
    result = pd.Series(float("nan"), index=prices.index)
    result.loc[first] = 100.0
    start = prices.index.get_loc(first)
    for index in range(start + 1, len(result)):
        if pd.isna(result.iloc[index - 1]) or pd.isna(daily.iloc[index]):
            continue
        result.iloc[index] = result.iloc[index - 1] * (1 + daily.iloc[index])
    return result.dropna()


def _score(rows: list[dict], weights: dict[str, float], key: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    frame["score"] = composite_score(frame, weights)
    return frame.sort_values(["score", key], ascending=[False, True], kind="stable")


def validate_prices(
    prices_path: Path,
    universe_path: Path,
    *,
    start: date,
    end: date,
    benchmark: str = "SPY",
    discontinuity_threshold: float = 0.50,
) -> pd.DataFrame:
    """Validate price structure and official-session coverage; return diagnostics."""
    prices = pd.read_csv(prices_path, parse_dates=["date"])
    universe = pd.read_csv(universe_path)
    required = {"date", "ticker", "adjusted_close"}
    if not required.issubset(prices.columns):
        raise ValueError(f"price file missing columns: {sorted(required - set(prices.columns))}")
    if prices.duplicated(["date", "ticker"]).any():
        raise ValueError("price file contains duplicate date/ticker rows")
    values = pd.to_numeric(prices["adjusted_close"], errors="coerce")
    if values.isna().any() or (~values.map(math.isfinite)).any() or values.le(0).any():
        raise ValueError("price file contains nonpositive or non-finite adjusted prices")
    if universe["ticker"].duplicated().any():
        raise ValueError("universe contains duplicate security identities")

    sessions = set(nyse_sessions(start, end)["date"])
    benchmark_dates = set(
        prices.loc[
            prices["ticker"].eq(benchmark)
            & prices["date"].dt.date.between(start, end),
            "date",
        ]
    )
    missing = sorted(sessions - benchmark_dates)
    extra = sorted(benchmark_dates - sessions)
    if missing or extra:
        raise ValueError(
            f"{benchmark} session mismatch: {len(missing)} missing, {len(extra)} non-session rows"
        )

    ordered = prices.sort_values(["ticker", "date"]).copy()
    ordered["return"] = ordered.groupby("ticker")["adjusted_close"].pct_change(fill_method=None)
    suspicious = ordered[ordered["return"].abs().gt(discontinuity_threshold)]
    suspicious_detail = "; ".join(
        f"{row['ticker']} {row['date'].date().isoformat()} {row['return']:+.2%}"
        for _, row in suspicious.head(20).iterrows()
    )
    if len(suspicious) > 20:
        suspicious_detail += f"; plus {len(suspicious) - 20} more"
    return pd.DataFrame(
        [
            {"check": "positive_finite_unique_prices", "status": "passed", "detail": len(prices)},
            {"check": "official_session_alignment", "status": "passed", "detail": len(sessions)},
            {"check": "instrument_identity_uniqueness", "status": "passed", "detail": len(universe)},
            {
                "check": "suspicious_adjusted_discontinuities",
                "status": "warning" if len(suspicious) else "passed",
                "detail": suspicious_detail or "none",
            },
            {
                "check": "vendor_independent_verification",
                "status": "not_claimed",
                "detail": "Yahoo history passed structural checks only",
            },
        ]
    )


def select_fundamental_snapshots(
    fundamentals: pd.DataFrame, cutoff: pd.Timestamp, tickers: list[str]
) -> pd.DataFrame:
    """Select latest eligible period, then its latest eligible revision, per ticker."""
    frame = fundamentals.copy()
    frame["available_at"] = pd.to_datetime(frame["available_at"], utc=True, errors="coerce")
    frame["period_end"] = pd.to_datetime(frame["period_end"], errors="coerce")
    eligible = frame[frame["available_at"].notna() & frame["available_at"].le(cutoff)]
    if "as_of" in eligible:
        snapshot_at = pd.to_datetime(eligible["as_of"], utc=True, errors="coerce")
        eligible = eligible[snapshot_at.notna() & snapshot_at.le(cutoff)]
    rows = []
    period_rank = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4, "FY": 5}
    for ticker in tickers:
        company = eligible[eligible["ticker"].eq(ticker)].copy()
        if company.empty:
            rows.append({"ticker": ticker, "fundamental_status": "unavailable"})
            continue
        latest_period = company["period_end"].max()
        company = company[company["period_end"].eq(latest_period)].copy()
        company["_period_rank"] = company.get(
            "fiscal_period", pd.Series(index=company.index, dtype=str)
        ).map(period_rank).fillna(0)
        highest_period_type = company["_period_rank"].max()
        company = company[company["_period_rank"].eq(highest_period_type)]
        row = company.sort_values("available_at", kind="stable").iloc[-1].to_dict()
        bases = [row.get("ebitda_ttm_basis"), row.get("free_cash_flow_ttm_basis")]
        if any(value not in (None, "", "ttm", "annual") and not pd.isna(value) for value in bases):
            status = "unsupported"
        elif "ebitda_ttm" not in row or "free_cash_flow_ttm" not in row:
            status = "missing"
        elif pd.isna(row["ebitda_ttm"]) or pd.isna(row["free_cash_flow_ttm"]):
            status = "incomplete"
        elif row["ebitda_ttm"] <= 0 or row["free_cash_flow_ttm"] <= 0:
            status = "nonpositive"
        else:
            status = "positive"
        row["fundamental_status"] = status
        rows.append(row)
    return pd.DataFrame(rows)


def _approval_state(screener_root: Path, as_of: datetime) -> tuple[set[str], bool]:
    approved: dict[str, tuple[datetime, bool]] = {}
    found = False
    for manifest_path in screener_root.glob("**/factset/*/reviews/*/manifest.json"):
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("status") != "success":
            continue
        reviewed = datetime.fromisoformat(str(manifest["reviewed_at"]))
        if reviewed.tzinfo is None:
            reviewed = reviewed.replace(tzinfo=UTC)
        if reviewed > as_of:
            continue
        found = True
        expires = datetime.fromisoformat(str(manifest["expires_at"])[:10]).replace(
            tzinfo=as_of.tzinfo or UTC
        )
        frame = pd.read_csv(manifest_path.parent / "decisions.csv")
        for row in frame.itertuples(index=False):
            symbol = str(row.factset_symbol)
            is_approved = row.decision == "approve"
            if expires < as_of:
                is_approved = False
            current = approved.get(symbol)
            if current is None or reviewed > current[0]:
                approved[symbol] = (reviewed, is_approved)
    return (
        {symbol.removesuffix("-US") for symbol, (_, ok) in approved.items() if ok},
        found,
    )


def _approval_at(screener_root: Path, as_of: datetime) -> set[str] | None:
    approved, found = _approval_state(screener_root, as_of)
    return approved if found else None


def review_summary(package_dir: Path, decisions_path: Path) -> Path:
    """Publish a complete immutable human review for one Summary import package."""
    package_dir = package_dir.resolve()
    source_manifest = json.loads((package_dir / "manifest.json").read_text())
    if source_manifest.get("status") != "success":
        raise ValueError("cannot review a failed Summary import")
    queue_path = Path(source_manifest["shortlist"]["path"])
    if _sha256(queue_path) != source_manifest["shortlist"]["sha256"]:
        raise ValueError("review queue hash does not match the imported package")
    queue = pd.read_csv(queue_path)
    decisions = pd.read_csv(decisions_path, dtype=str).fillna("")
    required = {"factset_symbol", "decision"}
    if not required.issubset(decisions.columns):
        raise ValueError("decisions require factset_symbol and decision columns")
    if "reason" not in decisions:
        decisions["reason"] = ""
    decisions["factset_symbol"] = decisions["factset_symbol"].str.strip().str.upper()
    decisions["decision"] = decisions["decision"].str.strip().str.lower()
    expected = set(queue["factset_symbol"].astype(str))
    supplied = set(decisions["factset_symbol"])
    if decisions["factset_symbol"].duplicated().any() or supplied != expected:
        raise ValueError("review must contain each queue symbol exactly once")
    allowed = {"approve", "reject", "needs_review"}
    if not set(decisions["decision"]).issubset(allowed):
        raise ValueError("decision must be approve, reject, or needs_review")

    validation = pd.read_csv(package_dir / "validation_report.csv").fillna("")
    structural = set(
        validation.loc[validation["severity"].eq("error"), "identifier"].astype(str)
    )
    invalid_approvals = set(
        decisions.loc[decisions["decision"].eq("approve"), "factset_symbol"]
    ) & structural
    if invalid_approvals:
        raise ValueError("structural validation failures cannot be overridden: " + ", ".join(sorted(invalid_approvals)))
    eligible = set(
        pd.read_csv(package_dir / "approved_candidates.csv")["factset_symbol"].astype(str)
    )
    overrides = decisions[decisions["decision"].eq("approve") & ~decisions["factset_symbol"].isin(eligible)]
    if overrides["reason"].str.strip().eq("").any():
        raise ValueError("financial-gate approvals require a nonblank reason")

    reviewed_at = datetime.now(UTC)
    payload = decisions.sort_values("factset_symbol").to_csv(index=False).encode()
    review_id = hashlib.sha256(
        source_manifest["import_id"].encode() + reviewed_at.isoformat().encode() + payload
    ).hexdigest()[:16]
    output = package_dir / "reviews" / f"{reviewed_at.strftime('%Y%m%dT%H%M%SZ')}-{review_id}"
    output.mkdir(parents=True, exist_ok=False)
    decisions.sort_values("factset_symbol").to_csv(output / "decisions.csv", index=False)
    manifest = {
        "schema_version": 1,
        "status": "success",
        "review_id": review_id,
        "reviewed_at": reviewed_at.isoformat(),
        "expires_at": source_manifest["expires_at"],
        "source_import_id": source_manifest["import_id"],
        "queue_sha256": source_manifest["shortlist"]["sha256"],
        "decisions_sha256": _sha256(output / "decisions.csv"),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return output


def build_targets(
    prices_path: Path,
    universe_path: Path,
    config: dict,
    *,
    start: date,
    end: date,
    approval_tickers: set[str] | None,
    label: str,
    fundamentals: pd.DataFrame | None = None,
    approval_resolver: Callable[[datetime], set[str] | None] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Create one target-weight table plus coverage and eligibility diagnostics."""
    bt = config["backtest"]
    weights = {str(k): float(v) for k, v in config["score_weights"].items()}
    top_sectors = int(config["sector_scan"]["top_n"])
    top_industries = int(config["industry_scan"]["top_n_per_sector"])
    top_companies = int(config["company_scan"]["top_n_per_industry"])
    trend_days = int(bt.get("trend_days", 200))
    max_position = float(bt.get("maximum_position_weight", 0.15))
    max_sector = float(bt.get("maximum_sector_weight", 0.40))
    tz = ZoneInfo(str(bt.get("timezone", "America/New_York")))
    signal_hour, signal_minute = [int(part) for part in str(bt.get("signal_time", "18:00")).split(":")]

    universe = pd.read_csv(universe_path).sort_values("ticker").reset_index(drop=True)
    pivot = _pivot_prices(prices_path)
    if fundamentals is not None:
        fundamentals = fundamentals.copy()
        fundamentals["available_at"] = pd.to_datetime(
            fundamentals["available_at"], utc=True
        )
    benchmark = str(bt.get("benchmark", "SPY"))
    targets: list[dict] = []
    coverage: list[dict] = []
    eligibility: list[dict] = []

    for signal_date, execution_date in month_end_signals(start, end):
        signal_at = datetime.combine(
            signal_date.date(),
            time(signal_hour, signal_minute),
            tzinfo=tz,
        ).isoformat()
        provenance = hashlib.sha256(f"{label}|{signal_at}".encode()).hexdigest()[:16]
        period_approvals = approval_resolver(datetime.fromisoformat(signal_at)) if approval_resolver else approval_tickers
        if approval_resolver and period_approvals is None:
            coverage.append(dict(signal_at=signal_at, status="collecting", reason="no_eligible_explicit_summary_review"))
            continue
        fundamental_status: dict[str, str] = {}
        if fundamentals is not None:
            cutoff = pd.Timestamp(signal_at).tz_convert("UTC")
            latest = select_fundamental_snapshots(
                fundamentals, cutoff, universe["ticker"].astype(str).tolist()
            )
            fundamental_status = dict(zip(latest["ticker"], latest["fundamental_status"]))
            period_approvals = set(
                latest.loc[latest["fundamental_status"].eq("positive"), "ticker"]
            )
        bench = _valid_series(pivot, benchmark, signal_date)
        if bench is None:
            coverage.append(dict(signal_at=signal_at, status="blocked", reason="missing_benchmark"))
            continue

        sector_rows = []
        for sector, group in universe.groupby("sector"):
            etf = str(group["sector_etf"].iloc[0])
            series = _valid_series(pivot, etf, signal_date)
            if series is None:
                continue
            sector_rows.append({"sector": sector, **momentum_metrics(series, bench)})
        sectors = _score(sector_rows, weights, "sector").head(top_sectors)

        selected_targets: list[dict] = []
        for sector_row in sectors.itertuples(index=False):
            sector_universe = universe[universe["sector"] == sector_row.sector]
            sector_etf = str(sector_universe["sector_etf"].iloc[0])
            sector_series = _valid_series(pivot, sector_etf, signal_date)
            if sector_series is None:
                continue

            industry_rows = []
            for industry, group in sector_universe.groupby("industry"):
                basket = _basket_index(pivot.loc[:signal_date], list(group["ticker"]))
                if basket.empty or basket.index[-1] != signal_date:
                    continue
                industry_rows.append({"industry": industry, **momentum_metrics(basket, sector_series)})
            industries = _score(industry_rows, weights, "industry").head(top_industries)
            if industries.empty:
                continue

            sector_budget = min(1.0 / max(len(sectors), 1), max_sector)
            industry_budget = sector_budget / len(industries)
            for industry_row in industries.itertuples(index=False):
                industry_universe = sector_universe[sector_universe["industry"] == industry_row.industry]
                company_rows = []
                for security in industry_universe.itertuples(index=False):
                    series = _valid_series(
                        pivot, security.ticker, signal_date, max(127, trend_days)
                    )
                    if series is None:
                        eligibility.append(dict(signal_at=signal_at, security_id=security.ticker, status="blocked", reason="missing_signal_price"))
                        continue
                    metrics = momentum_metrics(series, bench)
                    company_rows.append(
                        {
                            "security_id": security.ticker,
                            "sector": security.sector,
                            "industry": security.industry,
                            "company": security.company,
                            **metrics,
                            "passes_trend": _above_trend(series, trend_days),
                            "approved": period_approvals is None or security.ticker in period_approvals,
                        }
                    )
                ranked = _score(company_rows, weights, "security_id").head(top_companies)
                eligible = ranked[(ranked["passes_trend"]) & (ranked["approved"])].copy()
                for row in ranked.itertuples(index=False):
                    reason = "eligible"
                    if not row.passes_trend:
                        reason = "trend_filter"
                    elif not row.approved:
                        reason = (
                            f"sec_{fundamental_status.get(row.security_id, 'unavailable')}"
                            if fundamentals is not None
                            else "manual_summary_unapproved"
                        )
                    eligibility.append(dict(signal_at=signal_at, security_id=row.security_id, status="eligible" if reason == "eligible" else "excluded", reason=reason))
                if eligible.empty:
                    continue
                if str(bt.get("weighting", "inverse-vol")) == "inverse-vol":
                    inv = 1 / eligible["volatility_3m"].clip(lower=1e-9)
                    shares = inv / inv.sum()
                else:
                    shares = pd.Series(1 / len(eligible), index=eligible.index)
                for (_, row), share in zip(eligible.iterrows(), shares):
                    selected_targets.append(
                        dict(
                            signal_at=signal_at,
                            execution_date=execution_date.date(),
                            security_id=row["security_id"],
                            target_weight=min(float(industry_budget * share), max_position),
                            provenance_id=provenance,
                        )
                    )

        if not selected_targets:
            selected_targets.append(
                dict(signal_at=signal_at, execution_date=execution_date.date(), security_id="", target_weight=0.0, provenance_id=provenance)
            )
        targets.extend(selected_targets)
        coverage.append(
            dict(
                signal_at=signal_at,
                status="ok" if any(row["security_id"] for row in selected_targets) else "cash",
                reason="selected" if any(row["security_id"] for row in selected_targets) else "no_eligible_targets",
            )
        )

    return pd.DataFrame(targets), pd.DataFrame(coverage), pd.DataFrame(eligibility)


def build_equal_weight_targets(
    prices_path: Path,
    universe_path: Path,
    config: dict,
    *,
    start: date,
    end: date,
    label: str,
    approval_tickers: set[str] | None = None,
    fundamentals: pd.DataFrame | None = None,
    trend_days: int = 0,
    approval_resolver: Callable[[datetime], set[str] | None] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build capped equal-weight targets while preserving excluded weight as cash."""
    pivot = _pivot_prices(prices_path)
    tickers = sorted(pd.read_csv(universe_path)["ticker"].astype(str).unique())
    bt = config["backtest"]
    cap = float(bt.get("maximum_position_weight", 0.15))
    tz = ZoneInfo(str(bt.get("timezone", "America/New_York")))
    hour, minute = map(int, str(bt.get("signal_time", "18:00")).split(":"))
    rows: list[dict] = []
    diagnostics: list[dict] = []
    for signal, execution in month_end_signals(start, end):
        signal_at = datetime.combine(signal.date(), time(hour, minute), tzinfo=tz).isoformat()
        period_approvals = approval_resolver(datetime.fromisoformat(signal_at)) if approval_resolver else approval_tickers
        if approval_resolver and period_approvals is None:
            diagnostics.extend(
                {
                    "signal_at": signal_at,
                    "security_id": ticker,
                    "fundamental_status": "unavailable",
                    "status": "collecting",
                    "reason": "no_eligible_explicit_summary_review",
                }
                for ticker in tickers
            )
            continue
        status = {ticker: "positive" for ticker in tickers}
        if fundamentals is not None:
            snapshots = select_fundamental_snapshots(
                fundamentals, pd.Timestamp(signal_at).tz_convert("UTC"), tickers
            )
            status = dict(zip(snapshots["ticker"], snapshots["fundamental_status"]))
            base = [ticker for ticker in tickers if status[ticker] == "positive"]
        elif period_approvals is not None:
            base = [ticker for ticker in tickers if ticker in period_approvals]
            status = {
                ticker: "positive" if ticker in period_approvals else "unapproved"
                for ticker in tickers
            }
        else:
            base = tickers
        weight = min(1 / len(base), cap) if base else 0.0
        selected = []
        for ticker in tickers:
            if ticker not in base:
                diagnostics.append(
                    {
                        "signal_at": signal_at,
                        "security_id": ticker,
                        "fundamental_status": status[ticker],
                        "status": "excluded",
                        "reason": (
                            f"sec_{status[ticker]}" if fundamentals is not None else "manual_summary_unapproved"
                        ),
                    }
                )
                continue
            series = _valid_series(pivot, ticker, signal, max(1, trend_days))
            reason = status[ticker]
            if series is None:
                reason = "missing_price_history"
            elif trend_days and not _above_trend(series, trend_days):
                reason = "trend_filter"
            else:
                selected.append(ticker)
                reason = "eligible"
            diagnostics.append(
                {
                    "signal_at": signal_at,
                    "security_id": ticker,
                    "fundamental_status": status[ticker],
                    "status": "eligible" if reason == "eligible" else "excluded",
                    "reason": reason,
                }
            )
        provenance = hashlib.sha256(f"{label}|{signal_at}".encode()).hexdigest()[:16]
        rows.extend(
            {
                "signal_at": signal_at,
                "execution_date": execution.date(),
                "security_id": ticker,
                "target_weight": weight,
                "provenance_id": provenance,
            }
            for ticker in selected
        )
        if not selected:
            rows.append(
                {
                    "signal_at": signal_at,
                    "execution_date": execution.date(),
                    "security_id": "",
                    "target_weight": 0.0,
                    "provenance_id": provenance,
                }
            )
    return pd.DataFrame(rows), pd.DataFrame(diagnostics)


def _equal_coverage(diagnostics: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for signal_at, group in diagnostics.groupby("signal_at"):
        missing = group["reason"].eq("missing_price_history").any()
        eligible = group["status"].eq("eligible").any()
        rows.append(
            {
                "signal_at": signal_at,
                "status": "blocked" if missing else "ok" if eligible else "cash",
                "reason": "missing_price_history" if missing else "selected" if eligible else "no_eligible_targets",
            }
        )
    return pd.DataFrame(rows)


def _run_cpp(prices_path: Path, targets_path: Path, output_dir: Path, config: dict, start: date, end: date) -> None:
    subprocess.run(["make", "-C", str(REPO_ROOT / "backtest")], check=True)
    bt = config["backtest"]
    subprocess.run(
        [
            str(CPP_BINARY),
            "--prices",
            str(prices_path),
            "--targets",
            str(targets_path),
            "--output-dir",
            str(output_dir),
            "--benchmark",
            str(bt.get("benchmark", "SPY")),
            "--start",
            start.isoformat(),
            "--end",
            end.isoformat(),
            "--cost-bps",
            str(bt.get("transaction_cost_bps", 10)),
            "--initial-capital",
            str(bt.get("initial_capital", 100000)),
        ],
        check=True,
    )


def _write_manifest(paths: Paths, *, mode: str, label: str, config_path: Path, prices_path: Path, universe_path: Path, targets_path: Path) -> None:
    manifest = {
        "schema_version": 2,
        "mode": mode,
        "label": label,
        "created_at": datetime.now(UTC).isoformat(),
        "accounting_basis": "synthetic total-return units; adjusted-close returns already include splits/dividends",
        "availability_note": "historical_diagnostic uses current cached price history; forward uses observed caches/imports only",
        "calendar": {"version": CALENDAR_VERSION, "source": CALENDAR_SOURCE},
        "files": {
            "config": {"path": str(config_path), "sha256": _sha256(config_path)},
            "prices": {"path": str(prices_path), "sha256": _sha256(prices_path)},
            "universe": {"path": str(universe_path), "sha256": _sha256(universe_path)},
            "targets": {"path": str(targets_path), "sha256": _sha256(targets_path)},
            "workflow_code": {"path": str(Path(__file__)), "sha256": _sha256(Path(__file__))},
            "simulator_code": {
                "path": str(REPO_ROOT / "backtest" / "src" / "backtest.cpp"),
                "sha256": _sha256(REPO_ROOT / "backtest" / "src" / "backtest.cpp"),
            },
        },
    }
    (paths.run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def _write_status(
    paths: Paths,
    status: str,
    *,
    reasons: list[str] | None = None,
    as_of: datetime | None = None,
    supported_through: date | None = None,
) -> None:
    payload = {
        "status": status,
        "reasons": reasons or [],
        "supported_through": supported_through.isoformat() if supported_through else None,
    }
    if as_of:
        payload["as_of"] = as_of.isoformat()
    (paths.run_dir / "status.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )


def historical(args: argparse.Namespace) -> int:
    config_path = args.config.resolve()
    config = _load_config(config_path)
    bt = config["backtest"]
    tickers = [str(ticker) for ticker in bt.get("research_tickers", [])]
    start = date.fromisoformat(str(args.start or bt["start"]))
    end = date.fromisoformat(str(args.end or bt["end"]))
    paths = _run_paths(args.output_root, "historical-diagnostic")
    prices_path, universe_path = _copy_research_inputs(args.cache_dir, paths, tickers)
    validate_prices(
        prices_path, universe_path, start=start, end=end, benchmark=str(bt.get("benchmark", "SPY"))
    ).to_csv(paths.run_dir / "data_validation.csv", index=False)
    nyse_sessions(start, end).to_csv(paths.inputs_dir / "sessions.csv", index=False)
    if args.strategy == "primary":
        targets, eligibility = build_equal_weight_targets(
            prices_path, universe_path, config, start=start, end=end,
            approval_tickers=None, label="historical_diagnostic_primary",
        )
        coverage = _equal_coverage(eligibility)
    else:
        targets, coverage, eligibility = build_targets(
            prices_path,
            universe_path,
            config,
            start=start,
            end=end,
            approval_tickers=None,
            label="historical_diagnostic_full_control",
        )
    targets_path = paths.inputs_dir / "targets.csv"
    targets.to_csv(targets_path, index=False, quoting=csv.QUOTE_MINIMAL)
    coverage.to_csv(paths.run_dir / "coverage.csv", index=False)
    eligibility.to_csv(paths.run_dir / "eligibility.csv", index=False)
    if not coverage.empty and coverage["status"].eq("blocked").any():
        _write_status(
            paths,
            "blocked",
            reasons=sorted(set(coverage.loc[coverage["status"].eq("blocked"), "reason"])),
        )
        _write_manifest(paths, mode="historical", label="historical_diagnostic", config_path=config_path, prices_path=prices_path, universe_path=universe_path, targets_path=targets_path)
        print(paths.run_dir)
        return 2
    _run_cpp(prices_path, targets_path, paths.results_dir, config, start, end)
    _write_status(paths, "completed", supported_through=end)
    _write_manifest(paths, mode="historical", label="historical_diagnostic", config_path=config_path, prices_path=prices_path, universe_path=universe_path, targets_path=targets_path)
    print(paths.run_dir)
    return 0


def forward(args: argparse.Namespace) -> int:
    config_path = args.config.resolve()
    config = _load_config(config_path)
    bt = config["backtest"]
    as_of = datetime.fromisoformat(args.as_of)
    if as_of.tzinfo is None:
        raise ValueError("--as-of must be timezone-aware")
    if as_of.astimezone(UTC) > datetime.now(UTC):
        raise ValueError("--as-of cannot be in the future")
    cache_manifest = args.cache_dir / "data_manifest.json"
    observed = None
    if cache_manifest.is_file():
        observed = datetime.fromisoformat(json.loads(cache_manifest.read_text())["created_at"])
        if observed > as_of.astimezone(UTC):
            raise ValueError("price cache was first observed after --as-of")
    tickers = (
        pd.read_csv(args.cache_dir / "universe.csv", usecols=["ticker"])["ticker"]
        .dropna()
        .astype(str)
        .drop_duplicates()
        .tolist()
    )
    observed_date = observed.astimezone(ZoneInfo("America/New_York")).date() if observed else as_of.date()
    start = date.fromisoformat(str(args.start or observed_date.isoformat()))
    end = date.fromisoformat(str(args.end or as_of.date().isoformat()))
    paths = _run_paths(args.output_root, "forward")
    prices_path, universe_path = _copy_research_inputs(args.cache_dir, paths, tickers)
    approvals, has_review = _approval_state(args.screener_root, as_of)
    if not has_review:
        nyse_sessions(start, end).to_csv(paths.inputs_dir / "sessions.csv", index=False)
        targets_path = paths.inputs_dir / "targets.csv"
        pd.DataFrame(columns=["signal_at", "execution_date", "security_id", "target_weight", "provenance_id"]).to_csv(targets_path, index=False)
        _write_status(
            paths,
            "collecting",
            as_of=as_of,
            reasons=["no_eligible_explicit_summary_review"],
        )
        _write_manifest(paths, mode="forward", label="forward_observed", config_path=config_path, prices_path=prices_path, universe_path=universe_path, targets_path=targets_path)
        print(paths.run_dir)
        return 0
    validate_prices(
        prices_path, universe_path, start=start, end=end, benchmark=str(bt.get("benchmark", "SPY"))
    ).to_csv(paths.run_dir / "data_validation.csv", index=False)
    nyse_sessions(start, end).to_csv(paths.inputs_dir / "sessions.csv", index=False)
    if args.strategy == "primary":
        targets, eligibility = build_equal_weight_targets(
            prices_path, universe_path, config, start=start, end=end,
            approval_tickers=approvals, label="forward_observed_primary",
            approval_resolver=lambda signal_at: _approval_at(args.screener_root, signal_at),
        )
        coverage = _equal_coverage(eligibility)
    else:
        targets, coverage, eligibility = build_targets(
            prices_path,
            universe_path,
            config,
            start=start,
            end=end,
            approval_tickers=approvals,
            label="forward_observed_full_control",
            approval_resolver=lambda signal_at: _approval_at(args.screener_root, signal_at),
        )
    targets_path = paths.inputs_dir / "targets.csv"
    targets.to_csv(targets_path, index=False)
    coverage.to_csv(paths.run_dir / "coverage.csv", index=False)
    eligibility.to_csv(paths.run_dir / "eligibility.csv", index=False)
    if not coverage.empty and coverage["status"].eq("blocked").any():
        _write_status(
            paths,
            "blocked",
            as_of=as_of,
            reasons=sorted(set(coverage.loc[coverage["status"].eq("blocked"), "reason"])),
        )
        _write_manifest(paths, mode="forward", label="forward_observed", config_path=config_path, prices_path=prices_path, universe_path=universe_path, targets_path=targets_path)
        print(paths.run_dir)
        return 2
    if targets.empty:
        _write_status(
            paths,
            "collecting",
            as_of=as_of,
            reasons=["no_supported_monthly_decision"],
        )
        _write_manifest(paths, mode="forward", label="forward_observed", config_path=config_path, prices_path=prices_path, universe_path=universe_path, targets_path=targets_path)
        print(paths.run_dir)
        return 0
    execution_day = pd.to_datetime(targets["execution_date"]).max().date()
    session = nyse_sessions(execution_day, execution_day)
    close_hour, close_minute = map(int, session.iloc[0]["close_time"].split(":"))
    execution_close = datetime.combine(
        execution_day, time(close_hour, close_minute), ZoneInfo("America/New_York")
    )
    if execution_close > as_of.astimezone(ZoneInfo("America/New_York")):
        _write_status(
            paths,
            "pending",
            as_of=as_of,
            reasons=["execution_close_not_observed"],
        )
    else:
        _run_cpp(prices_path, targets_path, paths.results_dir, config, start, end)
        _write_status(paths, "completed", as_of=as_of, supported_through=end)
    _write_manifest(paths, mode="forward", label="forward_observed", config_path=config_path, prices_path=prices_path, universe_path=universe_path, targets_path=targets_path)
    print(paths.run_dir)
    return 0


def import_summary(args: argparse.Namespace) -> int:
    from screener.pipeline.factset_pipeline import run_factset_pipeline

    result = run_factset_pipeline(args.screen_run, args.factset_dir, args.config)
    if result.success:
        manifest = json.loads((result.output_directory / "manifest.json").read_text())
        queue = pd.read_csv(manifest["shortlist"]["path"], usecols=["factset_symbol"])
        queue["decision"] = "needs_review"
        queue["reason"] = ""
        queue.to_csv(result.output_directory / "review_template.csv", index=False)
    print(result.output_directory)
    return 0 if result.success else 2


def sweep(args: argparse.Namespace) -> int:
    from screener.run_screener import run

    print(run(args.config))
    return 0


def report_run(run_dir: Path) -> Path:
    """Write a compact machine-readable report for a completed simulator run."""
    results = run_dir / "results" if (run_dir / "results").is_dir() else run_dir
    summary_path = results / "summary.csv"
    curve_path = results / "equity_curve.csv"
    if not summary_path.is_file() or not curve_path.is_file():
        raise ValueError("run has no completed simulator outputs")
    summary = pd.read_csv(summary_path).set_index("metric")
    curve = pd.read_csv(curve_path)
    payload = {
        "status": "completed",
        "supported_through": str(curve["date"].iloc[-1]),
        "portfolio": {
            metric: float(summary.loc[metric, "portfolio"])
            for metric in (
                "ending_value", "total_return", "cagr", "annualized_volatility",
                "sharpe_ratio", "maximum_drawdown", "total_turnover", "transaction_costs",
            )
        },
        "benchmark_total_return": float(summary.loc["total_return", "benchmark"]),
        "average_cash_weight": float(curve["cash_weight"].mean()),
    }
    output = run_dir / "report.json"
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return output


def report(args: argparse.Namespace) -> int:
    print(report_run(args.run_dir))
    return 0


def review(args: argparse.Namespace) -> int:
    output = review_summary(args.package, args.decisions)
    print(output)
    return 0


def experiment(args: argparse.Namespace) -> int:
    from backtest.experiment import run_four_scenarios

    output = run_four_scenarios(
        args.prices, args.universe, args.fundamentals, args.config,
        args.output_root, start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end), previous_run=args.previous_run,
    )
    print(output)
    print(pd.read_csv(output / "comparison.csv").to_string(index=False))
    return 0


def verify(args: argparse.Namespace) -> int:
    """Run offline release gates and, unless requested otherwise, the September experiment."""
    subprocess.run(["make", "-C", str(REPO_ROOT / "backtest"), "test"], check=True)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT), environment.get("PYTHONPATH", "")]
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "backtest/tests",
            "backtest/fundamentals/tests",
            "screener/tests",
        ],
        cwd=REPO_ROOT,
        env=environment,
        check=True,
    )
    if args.tests_only:
        print("offline release tests passed")
        return 0
    return experiment(args)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m backtest")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="command", required=True)

    hist = sub.add_parser("historical", help="run the eight-company 2023-2025 diagnostic")
    hist.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    hist.add_argument("--output-root", type=Path, default=REPO_ROOT / "data" / "backtest" / "free-summary")
    hist.add_argument("--start")
    hist.add_argument("--end")
    hist.add_argument("--strategy", choices=["primary", "full"], default="primary")
    hist.set_defaults(func=historical)

    scan = sub.add_parser("sweep", help="run the diversified Yahoo research sweep")
    scan.set_defaults(func=sweep)

    fwd = sub.add_parser("forward", help="replay observed forward decisions")
    fwd.add_argument("--as-of", required=True, help="timezone-aware timestamp")
    fwd.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    fwd.add_argument("--screener-root", type=Path, default=DEFAULT_SCREENER_ROOT)
    fwd.add_argument("--output-root", type=Path, default=REPO_ROOT / "data" / "backtest" / "free-summary")
    fwd.add_argument("--start")
    fwd.add_argument("--end")
    fwd.add_argument("--strategy", choices=["primary", "full"], default="primary")
    fwd.set_defaults(func=forward)

    imp = sub.add_parser("import-summary", help="archive FactSet Summary files for forward use")
    imp.add_argument("--screen-run", type=Path, default=DEFAULT_SCREENER_ROOT / "2026-09-02")
    imp.add_argument("--factset-dir", type=Path, required=True)
    imp.set_defaults(func=import_summary)

    rev = sub.add_parser("review-summary", help="publish approve/reject decisions for one import")
    rev.add_argument("--package", type=Path, required=True)
    rev.add_argument("--decisions", type=Path, required=True)
    rev.set_defaults(func=review)

    rep = sub.add_parser("report", help="write a compact report for a completed run")
    rep.add_argument("--run-dir", type=Path, required=True)
    rep.set_defaults(func=report)

    exp = sub.add_parser("experiment", help="run the offline four-scenario September diagnostic")
    exp.add_argument("--prices", type=Path, default=REPO_ROOT / "data/backtest/2026-09-18-corrected-top10-observed/prices.csv")
    exp.add_argument("--universe", type=Path, default=REPO_ROOT / "data/backtest/2026-09-18-corrected-top10-observed/universe.csv")
    exp.add_argument("--fundamentals", type=Path, default=REPO_ROOT / "data/fundamentals/sep17-18-test/pit-v2/fundamentals.parquet")
    exp.add_argument("--output-root", type=Path, default=REPO_ROOT / "data/backtest/control")
    exp.add_argument("--previous-run", type=Path, default=REPO_ROOT / "data/backtest/control/20260918T183936Z-sep17-18-weakpoint")
    exp.add_argument("--start", default="2023-01-01")
    exp.add_argument("--end", default="2025-12-31")
    exp.set_defaults(func=experiment)

    gate = sub.add_parser("verify", help="run all offline accuracy gates and the September experiment")
    gate.add_argument("--prices", type=Path, default=REPO_ROOT / "data/backtest/2026-09-18-corrected-top10-observed/prices.csv")
    gate.add_argument("--universe", type=Path, default=REPO_ROOT / "data/backtest/2026-09-18-corrected-top10-observed/universe.csv")
    gate.add_argument("--fundamentals", type=Path, default=REPO_ROOT / "data/fundamentals/sep17-18-test/pit-v2/fundamentals.parquet")
    gate.add_argument("--output-root", type=Path, default=REPO_ROOT / "data/backtest/control")
    gate.add_argument("--previous-run", type=Path, default=REPO_ROOT / "data/backtest/control/20260918T183936Z-sep17-18-weakpoint")
    gate.add_argument("--start", default="2023-01-01")
    gate.add_argument("--end", default="2025-12-31")
    gate.add_argument("--tests-only", action="store_true")
    gate.set_defaults(func=verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}")
        return 1
