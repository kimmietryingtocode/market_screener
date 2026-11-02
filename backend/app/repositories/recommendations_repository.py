"""Read the immutable recommendation artifacts produced by the screener."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from app.repositories.scores_repository import RankingRow


DATA_ROOT = Path(__file__).resolve().parents[3] / "data" / "screener"


def latest_review_package() -> Path | None:
    packages = [path for path in DATA_ROOT.glob("*/top10_review") if (path / "ranking.csv").is_file()]
    return max(packages, default=None, key=lambda path: path.parent.name)


def _latest_validated_package(review: Path) -> Path | None:
    candidates: list[tuple[str, Path]] = []
    for manifest_path in (review / "factset").glob("*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        package = manifest_path.parent
        if manifest.get("status") == "success" and (package / "approved_candidates.csv").is_file():
            candidates.append((str(manifest.get("imported_at", "")), package))
    return max(candidates, default=("", None), key=lambda item: item[0])[1]


def _number(row: dict[str, str], key: str) -> float | None:
    value = (row.get(key) or "").strip()
    return float(value) if value else None


def _screen_date(review: Path) -> date:
    return date.fromisoformat(review.parent.name[:10])


def _screened_rows(review: Path) -> list[RankingRow]:
    with (review / "ranking.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [
        RankingRow(
            ticker=row["ticker"],
            company_name=row.get("company") or row["ticker"],
            sector=row.get("sector") or None,
            score_date=_screen_date(review),
            momentum_score=float(row["global_momentum_score"]),
            fundamental_score=None,
            composite_score=float(row["global_momentum_score"]),
            components={
                "ranking_stage": "screened",
                "industry": row.get("industry"),
                "selection_reason": row.get("selection_reason"),
                "factset_action": row.get("factset_action"),
            },
            methodology_version=f"market-screen-{_screen_date(review).isoformat()}",
        )
        for row in rows
    ]


def _validated_rows(review: Path, package: Path) -> list[RankingRow]:
    with (package / "approved_candidates.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [
        RankingRow(
            ticker=row["ticker"],
            company_name=row.get("company") or row["ticker"],
            sector=row.get("sector") or None,
            score_date=_screen_date(review),
            momentum_score=float(row.get("market_score") or row["score"]),
            fundamental_score=_number(row, "fundamental_score"),
            # FactSet is a quality gate; it does not silently reorder the market ranking.
            composite_score=float(row.get("market_score") or row["score"]),
            components={
                "ranking_stage": "factset_validated",
                "industry": row.get("industry"),
                "selection_reason": "Passed the FactSet financial-quality gate.",
                "factset_action": "FactSet validated",
            },
            methodology_version=f"factset-validated-{_screen_date(review).isoformat()}",
        )
        for row in rows
    ]


def top_ranked(*, limit: int, sector: str | None = None) -> list[RankingRow]:
    review = latest_review_package()
    if review is None:
        return []
    validated = _latest_validated_package(review)
    rows = _validated_rows(review, validated) if validated else _screened_rows(review)
    if sector:
        rows = [row for row in rows if row.sector == sector]
    return rows[:limit]
