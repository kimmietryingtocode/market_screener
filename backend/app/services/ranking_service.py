from typing import Any

from app.repositories import recommendations_repository, scores_repository
from app.repositories.scores_repository import RankingRow
from app.services.errors import NotFoundError, ValidationError

MAX_LIMIT = 100


def get_rankings(
    connection: Any,
    *,
    limit: int = 20,
    sector: str | None = None,
    methodology_version: str | None = None,
) -> list[RankingRow]:
    """Top securities by composite score for the most recent scoring run.

    Raises NotFoundError when nothing has been scored yet — that's a real
    operational state (the scoring worker hasn't run) and an empty list would
    hide it from whoever is looking at the screen.
    """
    if limit < 1 or limit > MAX_LIMIT:
        raise ValidationError(f"limit must be between 1 and {MAX_LIMIT}")

    # Published FactSet recommendations win when no specific methodology was
    # asked for; the computed scores are the fallback.
    if methodology_version is None:
        recommendations = recommendations_repository.top_ranked(limit=limit, sector=sector)
        if recommendations:
            return recommendations

    # Resolve a (score_date, methodology) pair rather than a date alone:
    # filtering on date only lets two methodology versions scored the same day
    # land in one list, putting a ticker in it twice and ordering scores from
    # different methodologies against each other as if they were comparable.
    run = scores_repository.latest_run(connection, methodology_version)
    if run is None:
        raise NotFoundError("no scores have been calculated yet")
    score_date, resolved_version = run

    return scores_repository.top_ranked(
        connection,
        score_date=score_date,
        methodology_version=resolved_version,
        limit=limit,
        sector=sector,
    )
