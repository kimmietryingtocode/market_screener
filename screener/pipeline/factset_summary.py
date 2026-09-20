"""Parse FactSet Standardized Summary exports by labels and year headers."""

from __future__ import annotations

import csv
import re
from dataclasses import asdict, dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from .factset_common import FactSetParseError, normalize_identifier, normalize_label, parse_financial_number


@dataclass(frozen=True)
class FactSetActual:
    source_file: str
    identifier: str
    available_at: date
    fiscal_year: int
    period_label: str
    restated: bool
    reporting_currency: str
    sales: float | None
    gross_income: float | None
    ebit: float | None
    ebitda: float | None
    net_income: float | None
    cash_and_short_term_investments: float | None
    total_assets: float | None
    total_debt: float | None
    net_debt: float | None
    total_liabilities: float | None
    total_shareholders_equity: float | None
    net_operating_cash_flow: float | None
    capital_expenditures: float | None
    net_investing_cash_flow: float | None
    net_financing_cash_flow: float | None
    free_cash_flow: float | None
    stock_option_compensation: float | None
    operating_lease_commitments: float | None
    long_term_debt_maturities: float | None
    auditor_fees: float | None
    auditor_opinion: str | None
    pension_funded_status: float | None
    pension_expense: float | None
    health_care_funded_status: float | None
    health_care_expense: float | None
    is_actual: bool = True

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["available_at"] = self.available_at.isoformat()
        return record


@dataclass(frozen=True)
class FactSetSummarySection:
    source_file: str
    source_label: str
    rows: tuple[tuple[str, ...], ...]
    identifier_hint: str | None
    reported_company_name: str | None


_TITLE = re.compile(r"^\s*(.*?)\s*\(([A-Za-z0-9.\-/]+)\)\s*$")
_SUMMARY_TITLE = re.compile(r"^\s*financial\s+statements\s+summary\s*-\s*(.*?)\s*$", re.I)
_YEAR = re.compile(r"\b(?:[A-Z]{3}\s*)?[\'’]?(\d{2}|\d{4})\b", re.I)
_FY_OFFSET = re.compile(r"^\s*([+-]?\d+)\s*FY\s*$", re.I)


def _clean_cell(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _read_workbook(path: Path) -> list[tuple[str, list[list[str]]]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                return [(path.stem, [[_clean_cell(cell) for cell in row] for row in csv.reader(handle)])]
        except (OSError, UnicodeError, csv.Error) as exc:
            raise FactSetParseError("MALFORMED_SUMMARY", None, f"failed to read {path.name}: {exc}") from exc
    if suffix not in {".xlsx", ".xls"}:
        raise FactSetParseError("UNSUPPORTED_FACTSET_FILE", None, f"unsupported FactSet file: {path.name}")
    try:
        workbook = pd.read_excel(path, sheet_name=None, header=None, dtype=object)
    except (OSError, ValueError, ImportError) as exc:
        raise FactSetParseError("MALFORMED_SUMMARY", None, f"failed to read {path.name}: {exc}") from exc
    return [
        (str(sheet), [[_clean_cell(cell) for cell in row] for row in frame.values.tolist()])
        for sheet, frame in workbook.items()
        if not frame.empty
    ]


def _title_identity(row: list[str]) -> tuple[str | None, str | None] | None:
    """Return identity only for a genuine Summary title row.

    FactSet's current CSV export uses ``Financial Statements Summary - Name``
    and puts the identifier in a later row.  Older exports use
    ``Name (TICKER)`` beside annual headers.  Requiring the generic title to be
    followed only by year labels prevents rows such as ``Non-Op. Income
    (Expense)`` from being mistaken for company boundaries.
    """
    nonempty = [(index, cell) for index, cell in enumerate(row) if cell]
    if not nonempty:
        return None
    first_index, first = nonempty[0]
    summary_match = _SUMMARY_TITLE.fullmatch(first)
    if summary_match:
        company = summary_match.group(1).strip()
        ticker_match = _TITLE.fullmatch(company)
        if ticker_match:
            return normalize_identifier(ticker_match.group(2)), ticker_match.group(1).strip()
        return None, company or None

    match = _TITLE.fullmatch(first)
    if not match or "discounted cash flow" in first.lower():
        return None
    following = [cell for index, cell in nonempty if index > first_index]
    if following and not all(_YEAR.search(cell) for cell in following):
        return None
    name = match.group(1).strip()
    if not name or name.lower() in {"restated", "standardized"}:
        return None
    return normalize_identifier(match.group(2)), name


def _identity(rows: list[list[str]]) -> tuple[str | None, str | None]:
    title_identifier: str | None = None
    company: str | None = None
    for row in rows[:20]:
        identity = _title_identity(row)
        if identity:
            title_identifier, company = identity
            break

    # Current CSV exports provide an explicit Identifier row after the title.
    for row in rows:
        for index, cell in enumerate(row):
            if normalize_label(cell) == "identifier":
                for value in row[index + 1 :]:
                    if value:
                        return normalize_identifier(value), company
    return title_identifier, company


def load_summary_sections(path: Path) -> tuple[FactSetSummarySection, ...]:
    sections: list[FactSetSummarySection] = []
    workbook = _read_workbook(path)
    for sheet, rows in workbook:
        if not rows:
            continue
        anchors: list[int] = []
        for row_index, row in enumerate(rows):
            if _title_identity(row):
                anchors.append(row_index)
        if not anchors:
            # Some stripped-down exports omit company titles but retain one
            # Identifier row per company.
            identifier_rows = [
                row_index
                for row_index, row in enumerate(rows)
                if any(normalize_label(cell) == "identifier" for cell in row if cell)
            ]
            if len(identifier_rows) > 1:
                anchors = identifier_rows
        segments = (
            [(anchor, anchors[index + 1] if index + 1 < len(anchors) else len(rows)) for index, anchor in enumerate(anchors)]
            if anchors
            else [(0, len(rows))]
        )
        for segment_number, (begin, end) in enumerate(segments, start=1):
            segment = rows[begin:end]
            identifier, company = _identity(segment)
            # Ignore non-summary workbook tabs instead of misclassifying them.
            labels = {normalize_label(cell) for row in segment for cell in row if cell}
            if "sales" not in labels or "ebitda" not in labels or "free cash flow" not in labels:
                continue
            if len(workbook) == 1 and len(segments) == 1:
                label = path.name
            elif len(segments) == 1:
                label = f"{path.name}#sheet:{sheet}"
            else:
                label = f"{path.name}#sheet:{sheet}:section-{segment_number}"
            sections.append(FactSetSummarySection(path.name, label, tuple(tuple(row) for row in segment), identifier, company))
    if not sections:
        raise FactSetParseError("NOT_SUMMARY_REPORT", None, f"{path.name} contains no recognizable Standardized Summary tab")
    return tuple(sections)


_ALIASES = {
    "sales": ("sales", "revenue"),
    "gross_income": ("gross income", "gross profit"),
    "ebit": ("ebit",),
    "ebitda": ("ebitda",),
    "net_income": ("net income",),
    "cash_and_short_term_investments": (
        "cash & short-term investments",
        "cash and short-term investments",
        "cash & equivalents",
        "cash and equivalents",
    ),
    "total_assets": ("total assets",),
    "total_debt": ("total debt",),
    "net_debt": ("net debt",),
    "total_liabilities": ("total liabilities",),
    "total_shareholders_equity": (
        "total shareholders' equity",
        "total shareholders equity",
        "stockholder's equity",
        "stockholders' equity",
        "shareholders' equity",
    ),
    "net_operating_cash_flow": ("net operating cash flow", "operating cash flow"),
    "capital_expenditures": ("capital expenditures", "capital expenditure"),
    "net_investing_cash_flow": ("net investing cash flow",),
    "net_financing_cash_flow": ("net financing cash flow",),
    "free_cash_flow": ("free cash flow",),
    "stock_option_compensation": ("stock option comp exp (net of tax)", "stock option compensation"),
    "operating_lease_commitments": ("operating lease commitments",),
    "long_term_debt_maturities": ("long term debt maturities", "long-term debt maturities"),
    "auditor_fees": ("fees", "auditor fees"),
    "auditor_opinion": ("opinion", "auditor opinion"),
    "pension_funded_status": ("pension funded status",),
    "pension_expense": ("pension expense",),
    "health_care_funded_status": ("health care funded status", "healthcare funded status"),
    "health_care_expense": ("health care expense", "healthcare expense"),
}


def _year_columns(rows: list[list[str]]) -> dict[int, tuple[int, str, bool, bool]]:
    # Preferred path for current FactSet Summary exports.  The -3FY..3FY
    # header precisely separates annual columns from LTM and quarterly data.
    for offset_row_index, offset_row in enumerate(rows):
        offsets = {
            index: int(match.group(1))
            for index, cell in enumerate(offset_row)
            if (match := _FY_OFFSET.fullmatch(cell))
        }
        if len(offsets) < 3:
            continue
        for period_row in rows[offset_row_index + 1 : offset_row_index + 5]:
            candidate: dict[int, tuple[int, str, bool, bool]] = {}
            for index, offset in offsets.items():
                cell = period_row[index] if index < len(period_row) else ""
                match = _YEAR.search(cell.upper()) if cell else None
                if not match:
                    continue
                raw = int(match.group(1))
                year = raw if raw >= 1900 else (1900 + raw if raw >= 70 else 2000 + raw)
                candidate[year] = (
                    index,
                    cell.replace("\n", " ").strip(),
                    "restate" in cell.lower(),
                    offset <= 0,
                )
            if len(candidate) >= 3:
                return candidate

    best: dict[int, tuple[int, str, bool, bool]] = {}
    for row in rows:
        candidate: dict[int, tuple[int, str, bool, bool]] = {}
        for index, cell in enumerate(row):
            match = _YEAR.search(cell.upper()) if cell else None
            if not match:
                continue
            raw = int(match.group(1))
            year = raw if raw >= 1900 else (1900 + raw if raw >= 70 else 2000 + raw)
            candidate[year] = (
                index,
                cell.replace("\n", " ").strip(),
                "restate" in cell.lower(),
                True,
            )
        if len(candidate) > len(best):
            best = candidate
    if len(best) < 3:
        raise FactSetParseError("MISSING_SUMMARY_YEARS", "fiscal_year", "Summary report requires at least three annual columns")
    return best


def _find_row(rows: list[list[str]], aliases: tuple[str, ...]) -> list[str] | None:
    normalized = set(aliases)
    candidates: list[list[str]] = []
    for row in rows:
        if any(normalize_label(cell) in normalized for cell in row if cell):
            candidates.append(row)
    if not candidates:
        return None
    return max(candidates, key=lambda row: sum(bool(cell) for cell in row))


def parse_summary_section(section: FactSetSummarySection, available_at: date) -> tuple[FactSetActual, ...]:
    rows = [list(row) for row in section.rows]
    if not section.identifier_hint:
        raise FactSetParseError("MISSING_IDENTIFIER", "identifier", "Summary title or Identifier row does not contain a ticker")
    years = _year_columns(rows)
    numeric: dict[str, list[str] | None] = {
        field: _find_row(rows, aliases) for field, aliases in _ALIASES.items()
    }
    for required in ("sales", "ebitda", "total_debt", "cash_and_short_term_investments", "free_cash_flow"):
        if numeric[required] is None:
            raise FactSetParseError("MISSING_SUMMARY_FIELD", required, f"Summary report is missing {required}")
    currency = "UNKNOWN"
    text = " ".join(cell for row in rows for cell in row if cell)
    currency_match = re.search(r"figures\s+in\s+millions\s+of\s+([A-Za-z. ]+)", text, re.I)
    if currency_match:
        currency = "USD" if "u.s" in currency_match.group(1).lower() else currency_match.group(1).strip().upper()
    else:
        currency_row = _find_row(rows, ("currency",))
        if currency_row is not None:
            for index, cell in enumerate(currency_row):
                if normalize_label(cell) == "currency":
                    currency = next((value.upper() for value in currency_row[index + 1 :] if value), "UNKNOWN")
                    break

    result: list[FactSetActual] = []
    for year in sorted(years, reverse=True):
        column, label, restated, is_actual = years[year]
        values: dict[str, Any] = {}
        for field, row in numeric.items():
            raw = row[column] if row is not None and column < len(row) else None
            if field == "auditor_opinion":
                values[field] = raw.strip() if raw and raw.strip() not in {"-", "N/A"} else None
            else:
                values[field] = parse_financial_number(raw)
        result.append(
            FactSetActual(
                source_file=section.source_label,
                identifier=section.identifier_hint,
                available_at=available_at,
                fiscal_year=year,
                period_label=label,
                restated=restated,
                reporting_currency=currency,
                **values,
                is_actual=is_actual,
            )
        )
    return tuple(result)


def replace_actual_identifier(rows: tuple[FactSetActual, ...], identifier: str) -> tuple[FactSetActual, ...]:
    return tuple(replace(row, identifier=identifier) for row in rows)
