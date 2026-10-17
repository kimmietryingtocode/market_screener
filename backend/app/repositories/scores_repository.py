"""Raw SQL for `scores` — written by worker/calculate_scores.py, read here."""

from datetime import date
from typing import Any, NamedTuple


class RankingRow(NamedTuple):
    ticker: str
    company_name: str | None
    sector: str | None
    score_date: date
    momentum_score: float
    fundamental_score: float
    composite_score: float
    components: dict
    methodology_version: str


def latest_run(connection: Any, methodology_version: str | None = None) -> tuple[date, str] | None:
    """Resolve the most recent scoring run as a (score_date, methodology) pair.

    Both halves matter. Filtering on score_date alone lets two methodology
    versions scored on the same day land in one ranking, which puts the same
    ticker in the list twice and orders scores produced by different
    methodologies against each other as if they were comparable. Running a
    new version alongside the old one to compare them is normal practice, so
    this has to resolve to exactly one version.
    """
    with connection.cursor() as cursor:
        if methodology_version:
            cursor.execute(
                """
                SELECT score_date, methodology_version FROM scores
                WHERE methodology_version = %s
                ORDER BY score_date DESC
                LIMIT 1
                """,
                (methodology_version,),
            )
        else:
            # Newest date wins; among versions sharing it, the highest
            # version string, so the choice is deterministic rather than
            # whatever the planner returns first.
            cursor.execute(
                """
                SELECT score_date, methodology_version FROM scores
                ORDER BY score_date DESC, methodology_version DESC
                LIMIT 1
                """
            )
        row = cursor.fetchone()
        return (row[0], row[1]) if row else None


def top_ranked(
    connection: Any,
    *,
    score_date: date,
    methodology_version: str,
    limit: int,
    sector: str | None = None,
) -> list[RankingRow]:
    """Highest composite scores for one (score_date, methodology) run."""
    query = """
        SELECT sec.ticker, sec.company_name, sec.sector, sc.score_date,
               sc.momentum_score, sc.fundamental_score, sc.composite_score,
               sc.components, sc.methodology_version
        FROM scores sc
        JOIN securities sec ON sec.id = sc.security_id
        WHERE sc.score_date = %s AND sc.methodology_version = %s
    """
    params: list[Any] = [score_date, methodology_version]
    if sector:
        query += " AND sec.sector = %s"
        params.append(sector)
    query += " ORDER BY sc.composite_score DESC, sec.ticker LIMIT %s"
    params.append(limit)

    with connection.cursor() as cursor:
        cursor.execute(query, tuple(params))
        return [
            RankingRow(
                ticker=row[0],
                company_name=row[1],
                sector=row[2],
                score_date=row[3],
                # NUMERIC -> Decimal from psycopg; cast to match the declared types.
                momentum_score=float(row[4]),
                fundamental_score=float(row[5]),
                composite_score=float(row[6]),
                components=row[7] or {},
                methodology_version=row[8],
            )
            for row in cursor.fetchall()
        ]
