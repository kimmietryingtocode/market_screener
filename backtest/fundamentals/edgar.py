"""SEC facts resolved by fiscal duration and public filing vintage."""

from __future__ import annotations

import json
import logging

import pandas as pd

from .config import CONCEPTS, RAW
from .core import calculate, canonical, finalize, number, put_derived, utc
from .http import JsonClient

LOG = logging.getLogger(__name__)


def filing_records(payload: dict) -> dict[str, dict]:
    """Index either a submissions response or a historical page by accession."""
    table = payload.get("filings", {}).get("recent", payload)
    accessions = table.get("accessionNumber", [])
    return {accession: {key: values[i] for key, values in table.items() if isinstance(values, list) and len(values) > i} for i, accession in enumerate(accessions)}


def availability(fact: dict, filing: dict) -> tuple[pd.Timestamp, str]:
    """Use SEC acceptance time, or next-day midnight for date-only filings."""
    accepted = filing.get("acceptanceDateTime")
    if accepted:
        stamp = pd.Timestamp(accepted)
        if stamp.tzinfo is not None:
            return utc(stamp), "acceptance"
    filed = fact.get("filed") or filing.get("filingDate")
    if not filed:
        return pd.NaT, "unknown"
    stamp = (pd.Timestamp(filed).normalize() + pd.Timedelta(1, unit="D")).tz_localize("America/New_York")
    return stamp.tz_convert("UTC"), "date_only_delayed"


def flatten_facts(payload: dict, filings: dict[str, dict]) -> pd.DataFrame:
    """Preserve SEC fact versions and provenance before any model selection."""
    output = []
    for concept, spec in CONCEPTS.items():
        for rank, tag in enumerate(spec["tags"]):
            units = payload.get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {})
            unit = spec.get("unit", "USD")
            for fact in units.get(unit, []):
                if fact.get("form") not in ("10-K", "10-K/A", "10-Q", "10-Q/A"):
                    continue
                value = number(fact.get("val"))
                if pd.isna(value) or not fact.get("end"):
                    continue
                filing = filings.get(fact.get("accn"), {})
                stamp, precision = availability(fact, filing)
                start = pd.Timestamp(fact["start"]) if fact.get("start") else pd.NaT
                if bool(spec.get("instant")) != pd.isna(start):
                    continue
                output.append({"concept": concept, "tag": tag, "rank": rank, "value": value,
                    "period_start": start, "period_end": pd.Timestamp(fact["end"]),
                    "unit": unit, "available_at": stamp,
                    "published_at": stamp if precision == "acceptance" else pd.NaT,
                    "availability_precision": precision, "accession": fact.get("accn", ""),
                    "filing_date": pd.Timestamp(fact.get("filed")), "form": fact["form"],
                    "fy": fact.get("fy"), "fp": fact.get("fp"),
                    "report_date": pd.Timestamp(filing.get("reportDate"))})
    return pd.DataFrame(output).drop_duplicates() if output else pd.DataFrame()


def select_facts(facts: pd.DataFrame, as_of: str | pd.Timestamp, policy: str = "latest") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Filter availability first, then resolve tags and filed-value versions."""
    cutoff = utc(as_of)
    if policy not in ("latest", "earliest"):
        raise ValueError("restatement policy must be latest or earliest")
    if facts.empty:
        return facts.copy(), facts.copy()
    eligible = facts[facts["available_at"].notna() & (facts["available_at"] <= cutoff) & (facts["period_end"] <= cutoff.tz_localize(None))].copy()
    keys = ["concept", "period_start", "period_end", "unit"]
    ordered = eligible.sort_values(["rank", "filing_date", "available_at", "accession"], ascending=[True, policy == "earliest", policy == "earliest", policy == "earliest"])
    selected = ordered.drop_duplicates(keys, keep="first").copy()
    others = ordered[~ordered.index.isin(selected.index)].copy()
    if not others.empty:
        others["reason"] = "superseded_or_fallback"
    # A changed cumulative fact cannot be differenced against an old vintage silently.
    counts = eligible.groupby(keys, dropna=False)["value"].nunique()
    selected["revised"] = [counts.loc[(r.concept, r.period_start, r.period_end, r.unit)] > 1 if pd.notna(r.period_start) else False for r in selected.itertuples()]
    return selected, others


def _quarter_count(start: pd.Timestamp, end: pd.Timestamp) -> int | None:
    days = (end - start).days + 1
    for count, low, high in ((1, 70, 110), (2, 150, 210), (3, 240, 310), (4, 330, 400)):
        if low <= days <= high:
            return count
    return None


def _periods(facts: pd.DataFrame) -> list[dict]:
    durations = facts[facts["period_start"].notna()]
    anchors: dict[pd.Timestamp, int] = {}
    for f in durations.to_dict("records"):
        count = _quarter_count(f["period_start"], f["period_end"])
        own_report = f["period_end"] == f["report_date"]
        if count == 4:
            anchors[f["period_start"]] = int(f["fy"]) if own_report and pd.notna(f["fy"]) else f["period_end"].year
        elif own_report and f["fp"] == f"Q{count}" and pd.notna(f["fy"]):
            anchors.setdefault(f["period_start"], int(f["fy"]))
    periods = {}
    for start, year in sorted(anchors.items()):
        cumulative = durations[durations["period_start"] >= start]
        ends: dict[int, pd.Timestamp] = {}
        for end in sorted(set(cumulative["period_end"])):
            count = _quarter_count(start, end)
            if count:
                # Ambiguous fiscal boundaries are excluded rather than calendar-guessed.
                if count in ends and ends[count] != end:
                    ends[count] = pd.NaT
                else:
                    ends[count] = end
        for count, end in ends.items():
            if pd.isna(end):
                continue
            if count == 4:
                periods[(end, "FY")] = {"period_start": start, "period_end": end, "fiscal_period": "FY", "fiscal_year": year, "fiscal_start": start}
            q_start = start if count == 1 else ends.get(count - 1, pd.NaT) + pd.Timedelta(1, unit="D")
            if pd.isna(q_start):
                # A reported standalone quarter can establish its own start.
                matches = durations[(durations["period_end"] == end) & ((durations["period_end"] - durations["period_start"]).dt.days.between(69, 109))]
                starts = matches["period_start"].unique()
                q_start = pd.Timestamp(starts[0]) if len(starts) == 1 else pd.NaT
            if pd.notna(q_start):
                periods[(end, f"Q{count}")] = {"period_start": q_start, "period_end": end, "fiscal_period": f"Q{count}", "fiscal_year": year, "fiscal_start": start}
    return sorted(periods.values(), key=lambda p: (p["period_end"], p["fiscal_period"]))


def _attach(row: dict, concept: str, fact: dict) -> None:
    row[concept] = abs(fact["value"]) if concept == "capex" else fact["value"]
    for key in ("tag", "available_at", "published_at", "filing_date", "accession"):
        row[f"{concept}_{key}"] = fact[key]
    row[f"{concept}_derived"] = False
    row[f"{concept}_basis"] = "instant" if CONCEPTS[concept].get("instant") else "period"


def normalize_archive(archive: pd.DataFrame, ticker: str, *, as_of: str | pd.Timestamp, policy: str = "latest") -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build one eligible fiscal snapshot from an already flattened SEC archive."""
    cutoff = utc(as_of)
    archive = archive.copy()
    selected, superseded = select_facts(archive, cutoff, policy)
    for table in (archive, superseded):
        table["ticker"] = ticker
        table["source"] = "edgar"
    if selected.empty:
        return canonical(), superseded, archive
    records = selected.to_dict("records")
    lookup = {(r["concept"], r["period_start"] if pd.notna(r["period_start"]) else None, r["period_end"]): r for r in records}
    output = []
    evidence = archive[archive["available_at"].notna() & (archive["available_at"] <= cutoff) & (archive["period_end"] <= cutoff.tz_localize(None))]
    for period in _periods(evidence):
        row = {**period, "ticker": ticker, "source": "edgar", "currency": "USD", "as_of": cutoff}
        used = []
        for concept, spec in CONCEPTS.items():
            start = None if spec.get("instant") else period["period_start"]
            fact = lookup.get((concept, start, period["period_end"]))
            if fact:
                _attach(row, concept, fact)
                used.append(fact)
            elif not spec.get("instant") and spec.get("additive", True) and period["fiscal_period"] not in ("FY", "Q1"):
                end = period["period_end"]
                left = lookup.get((concept, period["fiscal_start"], period["period_start"] - pd.Timedelta(1, unit="D")))
                right = lookup.get((concept, period["fiscal_start"], end))
                if left and right and left["tag"] == right["tag"]:
                    if (left["revised"] or right["revised"]) and left["accession"] != right["accession"]:
                        LOG.debug("%s %s %s: incompatible revised cumulative vintages", ticker, end, concept)
                        continue
                    inputs = []
                    for f in (right, left):
                        temp = {}
                        _attach(temp, concept, f)
                        inputs.append((temp, concept))
                    value = inputs[0][0][concept] - inputs[1][0][concept]
                    put_derived(row, concept, value, inputs, "period")
                    used.extend((right, left))
        # FY minus three standalone quarters also works when a YTD fact is absent.
        if period["fiscal_period"] == "Q4":
            quarters = [r for r in output if r.get("fiscal_start") == period["fiscal_start"] and r["fiscal_period"] in ("Q1", "Q2", "Q3")]
            for concept, spec in CONCEPTS.items():
                if pd.notna(row.get(concept)) or spec.get("instant") or not spec.get("additive", True):
                    continue
                annual = lookup.get((concept, period["fiscal_start"], period["period_end"]))
                if not annual or annual["revised"] or len(quarters) != 3 or any(pd.isna(q.get(concept)) for q in quarters):
                    continue
                if any(q.get(f"{concept}_available_at") > annual["available_at"] for q in quarters):
                    continue
                temp = {}
                _attach(temp, concept, annual)
                put_derived(row, concept, temp[concept] - sum(q[concept] for q in quarters), [(temp, concept)] + [(q, concept) for q in quarters], "period")
                used.append(annual)
        for _ in range(2):
            for concept, spec in CONCEPTS.items():
                if pd.notna(row.get(concept)) or "formula" not in spec:
                    continue
                a, operator, b = spec["formula"]
                if pd.notna(row.get(a)) and pd.notna(row.get(b)):
                    value = row[a] + row[b] if operator == "+" else row[a] - row[b]
                    put_derived(row, concept, value, [(row, a), (row, b)], "instant" if spec.get("instant") else "period")
        if used:
            row["filing_date"] = max(f["filing_date"] for f in used)
            row["accession"] = json.dumps(sorted({f["accession"] for f in used}))
            row["availability_precision"] = "date_only_delayed" if any(f["availability_precision"] != "acceptance" for f in used) else "acceptance"
        if any(pd.notna(row.get(c)) for c in RAW):
            output.append(finalize(row))
    return calculate(canonical(output)), superseded, archive


def normalize_edgar(payload: dict, filings: dict[str, dict], ticker: str, *, as_of: str | pd.Timestamp, policy: str = "latest") -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build one eligible fiscal snapshot plus superseded and full audit facts."""
    return normalize_archive(flatten_facts(payload, filings), ticker, as_of=as_of, policy=policy)


class EdgarSource:
    """SEC companyfacts provider with acceptance timestamps from submissions."""

    name = "edgar"

    def __init__(self, *, as_of: str | pd.Timestamp, client: JsonClient | None = None, policy: str = "latest", cik_map: dict[str, str] | None = None) -> None:
        """Bind the source to one cutoff and revision policy."""
        self.as_of = utc(as_of)
        self.client = client or JsonClient()
        self.policy = policy
        self.cik_map = cik_map or {}
        self.superseded = pd.DataFrame()
        self.archive = pd.DataFrame()
        self.snapshots = pd.DataFrame()

    def load(self, ticker: str) -> tuple[dict, dict[str, dict]]:
        """Load one issuer's raw SEC facts and filing timestamps."""
        key = ticker.upper().replace(".", "-")
        cik = self.cik_map.get(key)
        if cik is None:
            mapping = self.client.get("https://www.sec.gov/files/company_tickers.json").data
            matches = [v for v in mapping.values() if v["ticker"].upper().replace(".", "-") == key]
            if len(matches) != 1:
                raise ValueError("ticker must identify exactly one SEC issuer")
            cik = str(matches[0]["cik_str"]).zfill(10)
        payload = self.client.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json").data
        submissions = self.client.get(f"https://data.sec.gov/submissions/CIK{cik}.json").data
        filings = filing_records(submissions)
        for page in submissions.get("filings", {}).get("files", []):
            name = page.get("name", "")
            if "/" in name or not name.startswith(f"CIK{cik}") or not name.endswith(".json"):
                raise ValueError("unexpected SEC historical submissions filename")
            filings.update(filing_records(self.client.get(f"https://data.sec.gov/submissions/{name}").data))
        return payload, filings

    def fetch(self, ticker: str) -> pd.DataFrame:
        """Fetch and normalize a ticker; log provider failures and return empty."""
        self.superseded, self.archive = pd.DataFrame(), pd.DataFrame()
        try:
            payload, filings = self.load(ticker)
            frame, self.superseded, self.archive = normalize_edgar(payload, filings, ticker.upper(), as_of=self.as_of, policy=self.policy)
            return frame
        except (ValueError, KeyError, TypeError, RuntimeError, OSError) as exc:
            LOG.warning("edgar %s skipped: %s", ticker, exc)
            return canonical()
