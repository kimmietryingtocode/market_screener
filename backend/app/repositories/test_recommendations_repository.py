import json

from app.repositories import recommendations_repository


HEADER = "recommendation_rank,ticker,company,sector,industry,global_momentum_score,selection_reason,factset_action\n"


def review_package(tmp_path, monkeypatch):
    review = tmp_path / "2026-09-16" / "top10_review"
    review.mkdir(parents=True)
    (review / "ranking.csv").write_text(
        HEADER
        + "1,AAA,Alpha Inc.,Healthcare,Biotech,91.2,Ranked first,FactSet Summary required\n"
        + "2,BBB,Beta Inc.,Energy,Oil,82.5,Ranked second,FactSet Summary required\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(recommendations_repository, "DATA_ROOT", tmp_path)
    return review


def test_initial_recommendations_keep_real_score_and_pending_fundamentals(tmp_path, monkeypatch):
    review_package(tmp_path, monkeypatch)

    rows = recommendations_repository.top_ranked(limit=10)

    assert [row.ticker for row in rows] == ["AAA", "BBB"]
    assert rows[0].composite_score == 91.2
    assert rows[0].fundamental_score is None
    assert rows[0].components["ranking_stage"] == "screened"


def test_latest_successful_factset_package_becomes_the_validated_set(tmp_path, monkeypatch):
    review = review_package(tmp_path, monkeypatch)
    package = review / "factset" / "import-1"
    package.mkdir(parents=True)
    (package / "manifest.json").write_text(
        json.dumps({"status": "success", "imported_at": "2026-09-20T12:00:00Z"}),
        encoding="utf-8",
    )
    (package / "approved_candidates.csv").write_text(
        "ticker,company,sector,industry,market_score,fundamental_score\n"
        "BBB,Beta Inc.,Energy,Oil,82.5,74.0\n",
        encoding="utf-8",
    )

    rows = recommendations_repository.top_ranked(limit=10)

    assert [row.ticker for row in rows] == ["BBB"]
    assert rows[0].fundamental_score == 74.0
    assert rows[0].components["ranking_stage"] == "factset_validated"
