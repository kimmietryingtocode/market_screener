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


def latest_score_date(connection: Any, methodology_version: str | None = None) -> date | None:
    with connection.cursor() as cursor:
        if methodology_version:
            cursor.execute(
                "SELECT max(score_date) FROM scores WHERE methodology_version = %s",
                (methodology_version,),
            )
        else:
            cursor.execute("SELECT max(score_date) FROM scores")
        row = cursor.fetchone()
        return row[0] if row else None


def top_ranked(
    connection: Any,
    *,
    score_date: date,
    limit: int,
    sector: str | None = None,
    methodology_version: str | None = None,
) -> list[RankingRow]:
    """Highest composite scores for one score_date, best first."""
    query = """
        SELECT sec.ticker, sec.company_name, sec.sector, sc.score_date,
               sc.momentum_score, sc.fundamental_score, sc.composite_score,
               sc.components, sc.methodology_version
        FROM scores sc
        JOIN securities sec ON sec.id = sc.security_id
        WHERE sc.score_date = %s
    """
    params: list[Any] = [score_date]
    if methodology_version:
        query += " AND sc.methodology_version = %s"
        params.append(methodology_version)
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
