"""Run the existing FactSet Summary quality gate for the current review queue."""

from __future__ import annotations

import csv
from pathlib import Path

from app.repositories import recommendations_repository
from app.services.errors import ValidationError
from screener.pipeline.factset_pipeline import FactSetPipelineResult, run_factset_pipeline


CONFIG_PATH = Path(__file__).resolve().parents[3] / "screener" / "config.yaml"


def import_summaries(source_directory: Path) -> FactSetPipelineResult:
    review = recommendations_repository.latest_review_package()
    if review is None:
        raise ValidationError("No recommendation review queue is available.")
    try:
        return run_factset_pipeline(review, source_directory, CONFIG_PATH)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def validation_messages(result: FactSetPipelineResult) -> list[dict]:
    report = result.output_directory / "validation_report.csv"
    if not report.is_file():
        return []
    with report.open(encoding="utf-8-sig", newline="") as handle:
        return [row for row in csv.DictReader(handle) if row.get("severity") in {"error", "warning"}]
