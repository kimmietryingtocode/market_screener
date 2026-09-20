from __future__ import annotations

import json
import shutil
from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from screener.pipeline.factset_common import parse_financial_number
from screener.pipeline.factset_pipeline import run_factset_pipeline
from screener.pipeline.factset_summary import load_summary_sections, parse_summary_section
from screener.pipeline.fundamental_score import build_summary_scores


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "wmt_summary.csv"
CONFIG = ROOT / "config.yaml"


def _row(symbol: str = "WMT-US", ticker: str = "WMT", company: str = "Walmart Inc.") -> dict:
    return {
        "factset_symbol": symbol,
        "ticker": ticker,
        "company": company,
        "sector": "Consumer Defensive",
        "industry": "Discount Stores",
        "score": 80.0,
    }


def _inputs(tmp_path: Path, rows: list[dict] | None = None) -> tuple[Path, Path]:
    screen = tmp_path / f"{date.today().isoformat()}-screen"
    source = tmp_path / "summary"
    screen.mkdir()
    source.mkdir()
    pd.DataFrame(rows or [_row()]).to_csv(screen / "factset_lookup.csv", index=False)
    shutil.copyfile(FIXTURE, source / "summary.csv")
    return screen, source


@pytest.mark.parametrize(
    "raw,expected",
    [("1,234.5", 1234.5), ("(12.5)", -12.5), ("25%", 0.25), ("8.2x", 8.2), ("-", None)],
)
def test_financial_number_formats(raw, expected):
    assert parse_financial_number(raw) == expected


def test_summary_parser_preserves_actual_forecast_and_restatement_flags():
    section = load_summary_sections(FIXTURE)[0]
    periods = parse_summary_section(section, date.today())
    assert periods
    assert any(row.is_actual for row in periods)
    assert any(row.restated for row in periods)
    assert all(row.identifier == "WMT" for row in periods)


def test_no_forecast_gate_uses_latest_actual():
    periods = list(parse_summary_section(load_summary_sections(FIXTURE)[0], date.today()))
    actuals = [row for row in periods if row.is_actual]
    latest_year = max(row.fiscal_year for row in actuals)
    actuals = [
        replace(
            row,
            identifier="WMT-US",
            is_actual=True,
            ebitda=-1.0 if row.fiscal_year == latest_year else 10.0,
            free_cash_flow=10.0,
        )
        for row in actuals
    ]
    scored, _ = build_summary_scores(
        pd.DataFrame([_row()]),
        actuals,
        {"pillar_weights": {"growth": 0.35, "operating_quality": 0.35, "financial_strength": 0.30}},
    )
    assert not bool(scored.iloc[0].fundamental_viable)


def test_summary_import_is_immutable_and_records_missing_queue_members(tmp_path: Path):
    screen, source = _inputs(tmp_path, [_row(), _row("MSFT-US", "MSFT", "Microsoft Corp.")])
    first = run_factset_pipeline(screen, source, CONFIG)
    second = run_factset_pipeline(screen, source, CONFIG)
    assert first.success
    assert second.reused_existing_run
    assert first.output_directory == second.output_directory
    manifest = json.loads((first.output_directory / "manifest.json").read_text())
    assert manifest["counts"]["manual_rejections"] == 1
    assert "parsed_dcf" not in manifest["counts"]
    assert not (first.output_directory / "factset_snapshots.csv").exists()
    assert not (first.output_directory / "factset_forecasts.csv").exists()


def test_duplicate_summary_blocks_approval_publication(tmp_path: Path):
    screen, source = _inputs(tmp_path)
    shutil.copyfile(FIXTURE, source / "duplicate.csv")
    result = run_factset_pipeline(screen, source, CONFIG)
    assert not result.success
    assert not (result.output_directory / "approved_candidates.csv").exists()
    validation = pd.read_csv(result.output_directory / "validation_report.csv")
    assert validation["code"].eq("DUPLICATE_SUMMARY_REPORT").any()


def test_identity_mismatch_blocks_approval_publication(tmp_path: Path):
    screen, source = _inputs(tmp_path, [_row("AAPL-US", "AAPL", "Apple Inc.")])
    result = run_factset_pipeline(screen, source, CONFIG)
    assert not result.success
    assert not (result.output_directory / "approved_candidates.csv").exists()
