import uuid
from datetime import date, timedelta

import pytest

from app.services import ranking_service
from app.services.conftest import requires_db
from app.services.errors import NotFoundError, ValidationError

METHODOLOGY = "ranking-test-v1"


@pytest.fixture
def scored_universe(connection):
    """Three securities scored on two dates, under a test-only methodology
    version so these rows can't collide with real scores-v1 data."""
    suffix = uuid.uuid4().hex[:6].upper()
    tickers = [f"Z{suffix}A", f"Z{suffix}B", f"Z{suffix}C"]
    today = date.today()
    yesterday = today - timedelta(days=1)

    with connection.cursor() as cursor:
        ids = {}
        for ticker, sector in zip(tickers, ["Technology", "Technology", "Energy"]):
            cursor.execute(
                "INSERT INTO securities (ticker, company_name, sector) VALUES (%s, %s, %s) RETURNING id",
                (ticker, f"{ticker} Inc.", sector),
            )
            ids[ticker] = cursor.fetchone()[0]

        # yesterday's run: deliberately inverted ordering, to prove only the
        # latest score_date is served.
        for ticker, composite in zip(tickers, [10.0, 20.0, 90.0]):
            cursor.execute(
                """INSERT INTO scores (security_id, score_date, momentum_score, fundamental_score,
                                       composite_score, methodology_version)
                   VALUES (%s, %s, 50, 50, %s, %s)""",
                (ids[ticker], yesterday, composite, METHODOLOGY),
            )
        for ticker, composite in zip(tickers, [80.0, 60.0, 40.0]):
            cursor.execute(
                """INSERT INTO scores (security_id, score_date, momentum_score, fundamental_score,
                                       composite_score, methodology_version)
                   VALUES (%s, %s, 70, 30, %s, %s)""",
                (ids[ticker], today, composite, METHODOLOGY),
            )

    yield tickers

    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM securities WHERE ticker = ANY(%s)", (tickers,))


@requires_db
def test_rankings_are_ordered_best_composite_first(connection, scored_universe):
    rows = ranking_service.get_rankings(connection, limit=10, methodology_version=METHODOLOGY)

    composites = [r.composite_score for r in rows]
    assert composites == sorted(composites, reverse=True)
    assert rows[0].ticker == scored_universe[0]  # composite 80 today


@requires_db
def test_rankings_only_use_the_latest_score_date(connection, scored_universe):
    """Yesterday's run had the reverse ordering; if it leaked in, the top
    name would be the third ticker."""
    rows = ranking_service.get_rankings(connection, limit=10, methodology_version=METHODOLOGY)

    assert len(rows) == 3  # not 6 — one date only
    assert all(r.score_date == date.today() for r in rows)
    assert rows[0].ticker != scored_universe[2]


@requires_db
def test_limit_truncates_from_the_top(connection, scored_universe):
    rows = ranking_service.get_rankings(connection, limit=2, methodology_version=METHODOLOGY)

    assert [r.ticker for r in rows] == scored_universe[:2]


@requires_db
def test_sector_filter_narrows_the_universe(connection, scored_universe):
    rows = ranking_service.get_rankings(
        connection, limit=10, sector="Energy", methodology_version=METHODOLOGY
    )

    assert [r.ticker for r in rows] == [scored_universe[2]]


@requires_db
def test_rejects_out_of_range_limit(connection):
    with pytest.raises(ValidationError):
        ranking_service.get_rankings(connection, limit=0)
    with pytest.raises(ValidationError):
        ranking_service.get_rankings(connection, limit=ranking_service.MAX_LIMIT + 1)


@requires_db
def test_unscored_methodology_raises_not_found_rather_than_empty_list(connection):
    """An empty list would look like 'nothing ranks well'; this is really
    'the scoring worker hasn't run for this methodology'."""
    with pytest.raises(NotFoundError):
        ranking_service.get_rankings(connection, limit=10, methodology_version="does-not-exist-v99")


@requires_db
def test_scores_come_back_as_floats_not_decimals(connection, scored_universe):
    """NUMERIC columns arrive as Decimal from psycopg; the repository casts
    them so callers get what RankingRow's type hints promise."""
    rows = ranking_service.get_rankings(connection, limit=1, methodology_version=METHODOLOGY)

    assert isinstance(rows[0].composite_score, float)
    assert isinstance(rows[0].momentum_score, float)
