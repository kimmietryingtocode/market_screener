from datetime import date

import pytest

from app.schemas import CreateBacktestRequest, CreatePortfolioRequest, HoldingInput
from app.services import backtest_service, portfolio_service
from app.services.conftest import requires_db
from app.services.errors import NotFoundError, ValidationError


def _request(portfolio_id: int, **overrides) -> CreateBacktestRequest:
    params = dict(
        portfolio_id=portfolio_id,
        benchmark="spy",
        start_date=date(2025, 1, 2),
        end_date=date(2025, 12, 31),
    )
    params.update(overrides)
    return CreateBacktestRequest(**params)


@requires_db
def test_create_backtest_for_a_portfolio_the_user_owns(connection, make_user):
    user_id = make_user()
    portfolio, _ = portfolio_service.create_portfolio(
        connection, user_id, CreatePortfolioRequest(name="P1", holdings=[HoldingInput(ticker="AAPL", weight=1.0)])
    )

    backtest = backtest_service.create_backtest(connection, user_id, _request(portfolio.id))

    assert backtest.status == "queued"
    assert backtest.benchmark == "SPY"  # uppercased


@requires_db
def test_create_backtest_for_unknown_portfolio_raises_not_found(connection, make_user):
    user_id = make_user()
    with pytest.raises(NotFoundError):
        backtest_service.create_backtest(connection, user_id, _request(999_999_999))


@requires_db
def test_create_backtest_for_another_users_portfolio_raises_not_found(connection, make_user):
    """Ownership check: ids are guessable, so this must not be a 403 that
    confirms the portfolio exists — it should look identical to a portfolio
    that was never there."""
    owner_id = make_user()
    attacker_id = make_user()
    portfolio, _ = portfolio_service.create_portfolio(
        connection, owner_id, CreatePortfolioRequest(name="Owner's Portfolio", holdings=[HoldingInput(ticker="AAPL", weight=1.0)])
    )

    with pytest.raises(NotFoundError):
        backtest_service.create_backtest(connection, attacker_id, _request(portfolio.id))


@requires_db
def test_create_backtest_rejects_end_date_before_start_date(connection, make_user):
    user_id = make_user()
    portfolio, _ = portfolio_service.create_portfolio(
        connection, user_id, CreatePortfolioRequest(name="P1", holdings=[HoldingInput(ticker="AAPL", weight=1.0)])
    )

    with pytest.raises(ValidationError):
        backtest_service.create_backtest(
            connection, user_id, _request(portfolio.id, start_date=date(2025, 12, 31), end_date=date(2025, 1, 2))
        )


@requires_db
def test_get_backtest_detail_for_another_users_backtest_raises_not_found(connection, make_user):
    owner_id = make_user()
    attacker_id = make_user()
    portfolio, _ = portfolio_service.create_portfolio(
        connection, owner_id, CreatePortfolioRequest(name="P1", holdings=[HoldingInput(ticker="AAPL", weight=1.0)])
    )
    backtest = backtest_service.create_backtest(connection, owner_id, _request(portfolio.id))

    with pytest.raises(NotFoundError):
        backtest_service.get_backtest_detail(connection, backtest.id, attacker_id)


@requires_db
def test_get_backtest_detail_while_queued_has_no_metrics_yet(connection, make_user):
    user_id = make_user()
    portfolio, _ = portfolio_service.create_portfolio(
        connection, user_id, CreatePortfolioRequest(name="P1", holdings=[HoldingInput(ticker="AAPL", weight=1.0)])
    )
    backtest = backtest_service.create_backtest(connection, user_id, _request(portfolio.id))

    detail = backtest_service.get_backtest_detail(connection, backtest.id, user_id)

    assert detail.backtest.status == "queued"
    assert detail.metrics is None
    assert detail.equity_curve == []


@requires_db
def test_get_backtest_detail_once_completed_includes_metrics_and_equity_curve(connection, make_user):
    """Simulates what worker/persistence.import_job_result would have written,
    without depending on the worker/engine — this test is about the read
    path assembling metrics + equity_curve only when status is completed."""
    user_id = make_user()
    portfolio, _ = portfolio_service.create_portfolio(
        connection, user_id, CreatePortfolioRequest(name="P1", holdings=[HoldingInput(ticker="AAPL", weight=1.0)])
    )
    backtest = backtest_service.create_backtest(connection, user_id, _request(portfolio.id))

    with connection.cursor() as cursor:
        cursor.execute("UPDATE backtests SET status = 'completed' WHERE id = %s", (backtest.id,))
        cursor.execute(
            """INSERT INTO backtest_results (backtest_id, total_return, sharpe_ratio)
               VALUES (%s, 0.2, 1.1)""",
            (backtest.id,),
        )
        cursor.execute(
            """INSERT INTO equity_curve_points (backtest_id, trading_date, portfolio_value, benchmark_value)
               VALUES (%s, '2025-01-02', 100000, 100000)""",
            (backtest.id,),
        )

    detail = backtest_service.get_backtest_detail(connection, backtest.id, user_id)

    assert detail.metrics.total_return == 0.2
    assert len(detail.equity_curve) == 1
