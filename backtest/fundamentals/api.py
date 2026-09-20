"""EDGAR model exports and explicit Summary reconciliation."""

from __future__ import annotations

from pathlib import Path
import math

import pandas as pd

from .core import NUMERIC, canonical, utc
from .edgar import EdgarSource
from .http import JsonClient
from .prices import PriceSource, enrich_prices
FRAME_COMPARE_COLUMNS = ("ticker", "as_of", "period_start", "period_end", "fiscal_period", "currency", "unit", "concept", "basis", "source", "value", "edgar_value", "spread_pct", "flagged", "status")


def validate_snapshot(frame: pd.DataFrame, cutoff: pd.Timestamp) -> None:
    """Reject provider output that could expose a future or undated feature."""
    if frame.empty:
        return
    if frame.duplicated(["ticker", "period_end", "fiscal_period"]).any():
        raise ValueError("provider returned duplicate fiscal rows")
    if frame["as_of"].ne(cutoff).any() or frame["available_at"].isna().any() or frame["available_at"].gt(cutoff).any():
        raise ValueError("provider returned an unavailable snapshot")
    if frame["period_end"].isna().any() or frame["period_end"].gt(cutoff.tz_localize(None)).any():
        raise ValueError("provider returned an unknown or future financial period")
    for concept in NUMERIC:
        present = frame[concept].notna()
        times = frame.loc[present, f"{concept}_available_at"]
        if times.isna().any() or times.gt(cutoff).any():
            raise ValueError(f"{concept} lacks eligible availability")
        published = frame.loc[present, f"{concept}_published_at"]
        if published.gt(times).any() or times.gt(frame.loc[present, "available_at"]).any():
            raise ValueError(f"{concept} availability contradicts its publication/row timestamp")


def fetch_fundamentals(tickers: list[str], *, as_of: str | pd.Timestamp, client: JsonClient | None = None, policy: str = "latest", price_source: PriceSource | None = None, cik_map: dict[str, str] | None = None) -> pd.DataFrame:
    """Fetch eligible EDGAR rows at a required decision cutoff."""
    provider = EdgarSource(as_of=as_of, client=client, policy=policy, cik_map=cik_map)
    result, _ = collect(tickers, provider, as_of=as_of, price_source=price_source)
    return result


def collect(tickers: list[str], provider: EdgarSource, *, as_of: str | pd.Timestamp, price_source: PriceSource | None = None) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Collect EDGAR rows and separate non-model audit tables."""
    cutoff = utc(as_of)
    frames = []
    audits: dict[str, list[pd.DataFrame]] = {"superseded": [], "archive": [], "snapshots": []}
    for ticker in dict.fromkeys(t.upper().strip() for t in tickers):
        frame = canonical(provider.fetch(ticker))
        validate_snapshot(frame, cutoff)
        if price_source and not frame.empty:
            frame = enrich_prices(frame, price_source)
            validate_snapshot(frame, cutoff)
        if not frame.empty:
            frames.append(frame)
        for name in audits:
            audit = getattr(provider, name, pd.DataFrame())
            if not audit.empty:
                audits[name].append(audit.assign(as_of=cutoff))
    result = canonical([row for frame in frames for row in frame.to_dict("records")])
    audit_frames = {
        name: pd.DataFrame([row for part in parts for row in part.to_dict("records")])
        for name, parts in audits.items()
    }
    return result, audit_frames


def compare_frames(
    edgar: pd.DataFrame,
    summary: pd.DataFrame,
    *,
    as_of: str | pd.Timestamp,
    tolerance: float = 0.01,
) -> pd.DataFrame:
    """Compare eligible EDGAR actuals to manual Summary actuals.

    Differences can reflect non-GAAP adjustments, calendar versus fiscal-year
    alignment, restatement vintage, or whether diluted shares are period-end
    or weighted average. Only matching identity, fiscal duration, currency,
    unit, and basis rows are comparable.
    """
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("tolerance must be finite and nonnegative")
    cutoff = utc(as_of)
    frames = []
    for name, frame in (("edgar", edgar), ("summary", summary)):
        if frame.empty:
            continue
        current = frame.copy()
        if "available_at" not in current:
            continue
        available = pd.to_datetime(current["available_at"], utc=True, errors="coerce")
        current = current[available.notna() & available.le(cutoff)]
        if name == "summary":
            if "is_actual" not in current:
                continue
            current = current[current["is_actual"].eq(True)]
        if current.empty:
            continue
        current["source"] = name
        current["as_of"] = cutoff
        frames.append(current)
    if not frames:
        return pd.DataFrame(columns=FRAME_COMPARE_COLUMNS)
    concepts = [column for column in NUMERIC if any(column in frame for frame in frames)]
    rows = []
    for frame in frames:
        for record in frame.to_dict("records"):
            for concept in concepts:
                rows.append(
                    {
                        "ticker": record.get("ticker"),
                        "as_of": cutoff,
                        "period_start": record.get("period_start"),
                        "period_end": record.get("period_end"),
                        "fiscal_period": record.get("fiscal_period"),
                        "currency": record.get("currency", "UNKNOWN"),
                        "unit": record.get(f"{concept}_unit", record.get("unit")),
                        "concept": concept,
                        "basis": record.get(f"{concept}_basis", record.get("basis")),
                        "source": record["source"],
                        "value": record.get(concept),
                    }
                )
    result = pd.DataFrame(rows)
    keys = ["ticker", "as_of", "period_start", "period_end", "fiscal_period", "currency", "unit", "concept", "basis"]
    reference = result[result["source"] == "edgar"][keys + ["value"]].rename(columns={"value": "edgar_value"})
    result = result.merge(reference, on=keys, how="left")
    result["spread_pct"] = float("nan")
    result["flagged"] = pd.Series(pd.NA, index=result.index, dtype="boolean")
    result["status"] = "unmatched"
    comparable = result["value"].notna() & result["edgar_value"].notna() & result["edgar_value"].ne(0)
    result.loc[result["value"].isna(), "status"] = "missing_value"
    result.loc[comparable, "spread_pct"] = 100 * (result.loc[comparable, "value"] - result.loc[comparable, "edgar_value"]) / result.loc[comparable, "edgar_value"].abs()
    result.loc[comparable, "flagged"] = [
        abs(value) > tolerance * 100
        and not math.isclose(abs(value), tolerance * 100, rel_tol=1e-12, abs_tol=1e-12)
        for value in result.loc[comparable, "spread_pct"]
    ]
    result.loc[comparable, "status"] = "comparable"
    return result.reindex(columns=FRAME_COMPARE_COLUMNS)


def write_parquet(frame: pd.DataFrame, path: str | Path) -> None:
    """Atomically publish a DataFrame without replacing a good file on failure."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp.parquet")
    try:
        frame.to_parquet(temporary, engine="pyarrow", index=False)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
