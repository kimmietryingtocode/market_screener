"""Archive, validate, and score manual FactSet Standardized Summary exports."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .factset_common import (
    PIPELINE_VERSION,
    FactSetParseError,
    ValidationIssue,
    normalize_identifier,
)
from .factset_summary import (
    FactSetActual,
    load_summary_sections,
    parse_summary_section,
    replace_actual_identifier,
)
from .fundamental_score import build_summary_scores


@dataclass(frozen=True)
class FactSetPipelineResult:
    output_directory: Path
    success: bool
    imported_count: int
    manual_rejection_count: int
    error_count: int
    warning_count: int
    reused_existing_run: bool = False


@dataclass(frozen=True)
class _Resolution:
    identifier: str | None
    method: str | None
    code: str | None = None
    message: str | None = None


_NAME_NOISE = {
    "ADR", "ADS", "CLASS", "CO", "COMMON", "COMPANY", "CORP", "CORPORATION",
    "INC", "INCORPORATED", "LIMITED", "LLC", "LP", "LTD", "ORDINARY", "PLC",
    "SHARE", "SHARES", "STOCK",
}


def _ticker(value: str | None) -> str:
    text = normalize_identifier(str(value or ""))
    text = re.sub(r"(?:[-./:\s]US)$", "", text)
    return re.sub(r"[^A-Z0-9]", "", text)


def _company(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    tokens = re.findall(r"[A-Z0-9]+", text.upper())
    if tokens and tokens[0] == "THE":
        tokens = tokens[1:]
    return " ".join(token for token in tokens if token not in _NAME_NOISE)


class _Resolver:
    def __init__(self, shortlist: pd.DataFrame):
        self.rows = shortlist[["factset_symbol", "ticker", "company"]].to_dict("records")
        self.exact = {
            normalize_identifier(str(row["factset_symbol"])): str(row["factset_symbol"])
            for row in self.rows
        }

    def resolve(self, raw: str | None, reported_name: str | None) -> _Resolution:
        exact = self.exact.get(normalize_identifier(raw or ""))
        ticker_matches = {
            str(row["factset_symbol"])
            for row in self.rows
            if _ticker(raw) and _ticker(row["ticker"]) == _ticker(raw)
        }
        name_matches = {
            str(row["factset_symbol"])
            for row in self.rows
            if _company(reported_name) and _company(row["company"]) == _company(reported_name)
        }
        if exact:
            if name_matches and exact not in name_matches:
                return _Resolution(None, None, "IDENTITY_CONFLICT", "identifier and company name match different securities")
            return _Resolution(exact, "exact_identifier")
        if len(ticker_matches) > 1 or len(name_matches) > 1:
            return _Resolution(None, None, "AMBIGUOUS_IDENTIFIER", "report identity matches multiple queue members")
        ticker_match = next(iter(ticker_matches), None)
        name_match = next(iter(name_matches), None)
        if ticker_match and name_match and ticker_match != name_match:
            return _Resolution(None, None, "IDENTITY_CONFLICT", "identifier and company name match different securities")
        if ticker_match:
            return _Resolution(ticker_match, "ticker_variant")
        if name_match:
            return _Resolution(name_match, "company_name")
        return _Resolution(None, None, "UNMATCHED_IDENTIFIER", "report did not uniquely match the selected queue")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _import_id(source_hashes: dict[str, str], shortlist_hash: str, config: dict) -> str:
    payload = {
        "pipeline_version": PIPELINE_VERSION,
        "shortlist_hash": shortlist_hash,
        "config": config,
        "sources": source_hashes,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]


def _existing(path: Path) -> FactSetPipelineResult:
    manifest = json.loads((path / "manifest.json").read_text())
    counts = manifest["counts"]
    return FactSetPipelineResult(
        path,
        manifest["status"] == "success",
        counts["imported"],
        counts["manual_rejections"],
        counts["errors"],
        counts["warnings"],
        True,
    )


def run_factset_pipeline(
    screen_run: Path,
    factset_directory: Path,
    config_path: Path,
) -> FactSetPipelineResult:
    """Import one complete Summary review package at its first observed timestamp."""
    screen_run = screen_run.resolve()
    factset_directory = factset_directory.resolve()
    config_path = config_path.resolve()
    shortlist_path = screen_run / "factset_lookup.csv"
    if not shortlist_path.is_file():
        raise ValueError(f"screen run is missing factset_lookup.csv: {screen_run}")
    if not factset_directory.is_dir():
        raise ValueError(f"FactSet directory does not exist: {factset_directory}")
    dated = next(
        (match.group(1) for part in reversed(screen_run.parts) if (match := re.match(r"^(\d{4}-\d{2}-\d{2})", part))),
        "",
    )
    try:
        screen_date = datetime.strptime(dated, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("screen run path must contain a YYYY-MM-DD dated directory") from exc
    imported_at = datetime.now(timezone.utc)

    config = yaml.safe_load(config_path.read_text())
    factset_config = config.get("factset")
    if not isinstance(factset_config, dict):
        raise ValueError(f"missing factset configuration in {config_path}")
    source_paths = sorted(
        path for path in factset_directory.iterdir()
        if path.is_file() and path.suffix.lower() in {".csv", ".xlsx", ".xls"}
    )
    source_hashes = {path.name: _sha256(path) for path in source_paths}
    shortlist_hash = _sha256(shortlist_path)
    import_id = _import_id(source_hashes, shortlist_hash, factset_config)
    output = screen_run / "factset" / import_id
    if (output / "manifest.json").is_file():
        return _existing(output)

    shortlist = pd.read_csv(shortlist_path, dtype={"factset_symbol": "string"})
    required = {"factset_symbol", "ticker", "company", "score"}
    if not required.issubset(shortlist.columns):
        raise ValueError("factset_lookup.csv is missing: " + ", ".join(sorted(required - set(shortlist.columns))))
    shortlist["factset_symbol"] = shortlist["factset_symbol"].str.strip().str.upper()
    if shortlist["factset_symbol"].isna().any() or shortlist["factset_symbol"].duplicated().any():
        raise ValueError("factset_lookup.csv contains blank or duplicate identifiers")
    expected = set(shortlist["factset_symbol"])
    resolver = _Resolver(shortlist)

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{import_id}-", dir=output.parent))
    raw = temporary / "raw"
    raw.mkdir()
    for source in source_paths:
        shutil.copyfile(source, raw / source.name)
    shutil.copyfile(config_path, temporary / "configuration.yaml")

    actuals: list[FactSetActual] = []
    issues: list[ValidationIssue] = []
    submitted: dict[str, list[str]] = {}
    report_count = 0
    if not source_paths:
        issues.append(ValidationIssue("__batch__", None, "error", "NO_FACTSET_FILES", None, "FactSet directory contains no Summary files"))
    for source in source_paths:
        try:
            sections = load_summary_sections(source)
        except FactSetParseError as exc:
            code = "UNSUPPORTED_FACTSET_FILE" if exc.code == "NOT_SUMMARY_REPORT" else exc.code
            issues.append(ValidationIssue(source.name, None, "error", code, exc.field, str(exc)))
            continue
        report_count += len(sections)
        for section in sections:
            resolution = resolver.resolve(section.identifier_hint, section.reported_company_name)
            identifier = resolution.identifier or section.identifier_hint
            if resolution.identifier is None:
                issues.append(ValidationIssue(section.source_label, identifier, "error", resolution.code or "UNMATCHED_IDENTIFIER", "identifier", resolution.message or "unmatched report"))
            else:
                submitted.setdefault(resolution.identifier, []).append(section.source_label)
                if resolution.method != "exact_identifier":
                    issues.append(ValidationIssue(section.source_label, resolution.identifier, "info", "IDENTIFIER_RESOLVED", "identifier", f"resolved using {resolution.method}"))
            try:
                periods = parse_summary_section(section, imported_at.date())
            except FactSetParseError as exc:
                issues.append(ValidationIssue(section.source_label, identifier, "error", exc.code, exc.field, str(exc)))
                continue
            if resolution.identifier:
                periods = replace_actual_identifier(periods, resolution.identifier)
            actuals.extend(periods)
            latest = max((row for row in periods if row.is_actual), key=lambda row: row.fiscal_year)
            missing = [
                field for field in ("sales", "ebitda", "total_debt", "cash_and_short_term_investments", "free_cash_flow")
                if getattr(latest, field) is None
            ]
            if missing:
                issues.append(ValidationIssue(section.source_label, identifier, "info", "MISSING_SUMMARY_VALUE", ",".join(missing), "latest actual year is blank for: " + ", ".join(missing)))

    for identifier, labels in submitted.items():
        if len(labels) > 1:
            issues.extend(
                ValidationIssue(label, identifier, "error", "DUPLICATE_SUMMARY_REPORT", "identifier", f"{identifier} appears in {len(labels)} Summary sections")
                for label in labels
            )
    for identifier in sorted(expected - set(submitted)):
        issues.append(ValidationIssue("", identifier, "info", "MANUAL_REJECTED", "identifier", "queue member had no supplied Summary report"))

    invalid = {issue.identifier for issue in issues if issue.severity == "error" and issue.identifier}
    valid_ids = {
        identifier for identifier in expected
        if identifier not in invalid
        and len(submitted.get(identifier, [])) == 1
        and any(row.identifier == identifier and row.is_actual for row in actuals)
    }
    valid_actuals = [row for row in actuals if row.identifier in valid_ids]
    approved = pd.DataFrame()
    if valid_actuals:
        approved, scoring_issues = build_summary_scores(shortlist, valid_actuals, factset_config["scoring"])
        issues.extend(scoring_issues)
        approved["fundamental_eligible"] = approved["fundamental_viable"]
        for row in approved.itertuples(index=False):
            if not row.fundamental_viable:
                issues.append(ValidationIssue("", row.factset_symbol, "info", "FUNDAMENTAL_QUALITY_GATE_FAILED", "summary_final_ebitda,summary_final_fcf", "latest available outlook lacks positive EBITDA and free cash flow"))
            elif row.turnaround_dependent:
                issues.append(ValidationIssue("", row.factset_symbol, "warning", "TURNAROUND_DEPENDENT", "ebitda,free_cash_flow", "eligibility depends on forecast recovery from a nonpositive latest actual period"))
        approved = approved[approved["fundamental_eligible"]].copy()

    pd.DataFrame([row.to_record() for row in actuals], columns=FactSetActual.__dataclass_fields__).to_csv(temporary / "factset_actuals.csv", index=False)
    pd.DataFrame([issue.to_record() for issue in issues], columns=ValidationIssue.__dataclass_fields__).to_csv(temporary / "validation_report.csv", index=False)
    errors = sum(issue.severity == "error" for issue in issues)
    warnings = sum(issue.severity == "warning" for issue in issues)
    manual_rejections = sum(issue.code == "MANUAL_REJECTED" for issue in issues)
    success = errors == 0
    artifacts = ["factset_actuals.csv", "validation_report.csv", "configuration.yaml"]
    if success:
        approved.to_csv(temporary / "approved_candidates.csv", index=False)
        artifacts.append("approved_candidates.csv")
    maximum_age = int(factset_config.get("maximum_age_days", 120))
    manifest = {
        "pipeline_version": PIPELINE_VERSION,
        "status": "success" if success else "failure",
        "screen_date": screen_date.isoformat(),
        "available_at": imported_at.isoformat(),
        "imported_at": imported_at.isoformat(),
        "expires_at": (imported_at.date() + timedelta(days=maximum_age)).isoformat(),
        "import_id": import_id,
        "shortlist": {"path": str(shortlist_path), "sha256": shortlist_hash, "count": len(shortlist)},
        "sources": [{"file": name, "sha256": digest} for name, digest in source_hashes.items()],
        "artifacts": [{"file": name, "sha256": _sha256(temporary / name)} for name in artifacts],
        "config": {"path": str(config_path), "sha256": _sha256(config_path)},
        "counts": {
            "supplied_files": len(source_paths), "supplied_reports": report_count,
            "parsed": len({row.identifier for row in actuals}), "imported": len(valid_ids),
            "summary_actual_rows": len(actuals), "valid_summary_actual_rows": len(valid_actuals),
            "manual_rejections": manual_rejections, "errors": errors, "warnings": warnings,
        },
    }
    (temporary / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    try:
        temporary.rename(output)
    except FileExistsError:
        shutil.rmtree(temporary)
        return _existing(output)
    return FactSetPipelineResult(output, success, len(valid_ids), manual_rejections, errors, warnings)
