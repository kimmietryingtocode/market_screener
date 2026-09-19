"""Point-in-time contracts, canonical frames, and shared calculations."""

from __future__ import annotations

import json
import math

import pandas as pd

from .config import METRICS, PRICE_METRICS, RAW

NUMERIC = RAW + METRICS + PRICE_METRICS + ("revenue_ttm", "net_income_ttm", "diluted_eps_ttm", "ebitda_ttm", "free_cash_flow_ttm", "price")
META = ("ticker", "source", "as_of", "period_start", "period_end", "fiscal_year", "fiscal_period", "currency", "published_at", "available_at", "filing_date", "accession", "availability_precision", "is_derived", "price_date", "price_available_at", "price_source")
PROVENANCE = tuple(f"{name}_{suffix}" for name in NUMERIC for suffix in ("tag", "available_at", "published_at", "filing_date", "accession", "derived", "basis"))
COLUMNS = tuple(dict.fromkeys(META + NUMERIC + PROVENANCE))


def utc(value: str | pd.Timestamp) -> pd.Timestamp:
    """Validate a decision/publication timestamp and normalize it to UTC."""
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError("as_of/publication timestamps must include a timezone")
    return stamp.tz_convert("UTC")


def canonical(rows: list[dict] | pd.DataFrame | None = None) -> pd.DataFrame:
    """Return the stable schema, including for empty provider responses."""
    frame = pd.DataFrame(rows).reindex(columns=COLUMNS)
    for column in NUMERIC:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype(float)
    for column in COLUMNS:
        if column in ("as_of", "published_at", "available_at", "price_available_at") or column.endswith(("_available_at", "_published_at")):
            frame[column] = pd.to_datetime(frame[column], utc=True)
        elif column in ("period_start", "period_end", "filing_date", "price_date") or column.endswith("_filing_date"):
            frame[column] = pd.to_datetime(frame[column])
        elif column.endswith("_derived"):
            frame[column] = frame[column].astype("boolean").fillna(False).astype(bool)
        elif column == "fiscal_year":
            frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
        elif column not in NUMERIC:
            frame[column] = frame[column].astype("string")
    return frame


def number(value: object) -> float:
    """Parse a provider number; non-finite or unavailable values become NaN."""
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else math.nan
    except (ValueError, TypeError):
        return math.nan


def ratio(numerator: float, denominator: float) -> float:
    """Divide without fabricating a ratio for a missing or zero denominator."""
    return numerator / denominator if math.isfinite(numerator) and math.isfinite(denominator) and denominator != 0 else math.nan


def put_derived(row: dict, name: str, value: float, inputs: list[tuple[dict, str]], basis: str) -> None:
    """Set a calculation with the availability and provenance of all inputs."""
    if not math.isfinite(value) or not inputs:
        return
    stamps = [r.get(f"{key}_available_at", r.get("available_at")) for r, key in inputs]
    if any(pd.isna(s) for s in stamps):
        return
    row[name] = value
    row[f"{name}_available_at"] = max(utc(s) for s in stamps)
    published = [r.get(f"{key}_published_at", r.get("published_at")) for r, key in inputs]
    row[f"{name}_published_at"] = max((utc(s) for s in published if pd.notna(s)), default=pd.NaT)
    dates = [r.get(f"{key}_filing_date", r.get("filing_date")) for r, key in inputs]
    row[f"{name}_filing_date"] = max((pd.Timestamp(s) for s in dates if pd.notna(s)), default=pd.NaT)
    row[f"{name}_accession"] = json.dumps(sorted({str(r.get(f"{key}_accession", r.get("accession", ""))) for r, key in inputs}))
    row[f"{name}_tag"] = json.dumps([str(r.get(f"{key}_tag", key)) for r, key in inputs])
    row[f"{name}_derived"] = True
    row[f"{name}_basis"] = basis


def finalize(row: dict) -> dict:
    """Conservatively set whole-row availability to the latest feature input."""
    for field in ("available_at", "published_at"):
        values = [row.get(f"{key}_{field}") for key in NUMERIC if pd.notna(row.get(key))]
        row[field] = max((utc(v) for v in values if pd.notna(v)), default=pd.NaT)
    row["is_derived"] = any(row.get(f"{key}_derived") is True for key in NUMERIC)
    dates = [row.get(f"{key}_filing_date") for key in NUMERIC if pd.notna(row.get(key))]
    row["filing_date"] = max((pd.Timestamp(v) for v in dates if pd.notna(v)), default=row.get("filing_date", pd.NaT))
    return row


def trailing_rows(rows: list[dict], row: dict) -> list[dict]:
    """Find four contiguous fiscal quarters ending at this row's period end."""
    quarters = sorted((r for r in rows if r["fiscal_period"] != "FY" and r["period_end"] <= row["period_end"]), key=lambda r: r["period_end"])[-4:]
    if len(quarters) != 4 or quarters[-1]["period_end"] != row["period_end"]:
        return []
    if any(pd.isna(r["period_start"]) for r in quarters):
        return []
    if any(a["period_end"] + pd.Timedelta(1, unit="D") != b["period_start"] for a, b in zip(quarters, quarters[1:])):
        return []
    return quarters


def calculate(frame: pd.DataFrame) -> pd.DataFrame:
    """Calculate period margins and annual/TTM returns from eligible rows only."""
    if frame.empty:
        return canonical()
    output = []
    for _, group in frame.groupby(["ticker", "source", "as_of", "currency"], dropna=False):
        rows = group.sort_values(["period_end", "fiscal_period"]).to_dict("records")
        for row in rows:
            for name, top, bottom in (("gross_margin", "gross_profit", "revenue"), ("operating_margin", "operating_income", "revenue"), ("net_margin", "net_income", "revenue"), ("current_ratio", "current_assets", "current_liabilities"), ("debt_to_equity", "total_debt", "total_equity")):
                basis = "instant" if name in ("current_ratio", "debt_to_equity") else "period"
                if pd.isna(row[name]):
                    put_derived(row, name, ratio(row[top], row[bottom]), [(row, top), (row, bottom)], basis)
            put_derived(row, "ebitda", row["operating_income"] + row["depreciation_and_amortization"], [(row, "operating_income"), (row, "depreciation_and_amortization")], "period")
            put_derived(row, "free_cash_flow", row["operating_cash_flow"] - row["capex"], [(row, "operating_cash_flow"), (row, "capex")], "period")
            annual = next((r for r in rows if r["fiscal_period"] == "FY" and r["period_end"] == row["period_end"]), None)
            trailing = [annual] if annual is not None else trailing_rows(rows, row)
            for concept in ("revenue", "net_income", "diluted_eps", "ebitda", "free_cash_flow"):
                # EPS across split bases cannot safely be summed without corporate-action evidence.
                if concept == "diluted_eps" and annual is None:
                    continue
                if trailing:
                    put_derived(row, f"{concept}_ttm", sum(r[concept] for r in trailing), [(r, concept) for r in trailing], "ttm")
            if trailing:
                beginning = trailing[0]["period_start"] - pd.Timedelta(1, unit="D")
                opening = next((r for r in rows if r["period_end"] == beginning), None)
                if opening:
                    for name, flow, stock in (("roe", "net_income", "total_equity"), ("roa", "net_income", "total_assets"), ("asset_turnover", "revenue", "total_assets")):
                        numerator = sum(r[flow] for r in trailing)
                        denominator = (opening[stock] + row[stock]) / 2
                        inputs = [(r, flow) for r in trailing] + [(opening, stock), (row, stock)]
                        if pd.isna(row[name]):
                            put_derived(row, name, ratio(numerator, denominator), inputs, "annual_average" if row["fiscal_period"] == "FY" else "ttm_average")
            output.append(finalize(row))
    return canonical(output)
