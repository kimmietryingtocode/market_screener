from typing import Any, NamedTuple

from app.repositories import backtests_repository, portfolios_repository
from app.repositories.backtests_repository import BacktestRow, EquityCurveRow, MetricsRow
from app.schemas import CreateBacktestRequest
from app.services.errors import NotFoundError, ValidationError


class BacktestDetail(NamedTuple):
    backtest: BacktestRow
    research_outcome: str | None
    validation_messages: list[dict]
    metrics: MetricsRow | None
    equity_curve: list[EquityCurveRow]


def create_backtest(connection: Any, user_id: int, payload: CreateBacktestRequest) -> BacktestRow:
    if payload.end_date < payload.start_date:
        raise ValidationError("end_date cannot be before start_date")
    if not portfolios_repository.exists_for_user(connection, payload.portfolio_id, user_id):
        raise NotFoundError("portfolio not found")

    return backtests_repository.insert_backtest(
        connection,
        user_id=user_id,
        portfolio_id=payload.portfolio_id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        benchmark=payload.benchmark.upper(),
        rebalance_frequency=payload.rebalance_frequency,
        cost_bps=payload.cost_bps,
        initial_capital=payload.initial_capital,
    )


def list_backtests(connection: Any, user_id: int) -> list[BacktestRow]:
    return backtests_repository.list_backtests(connection, user_id)


def get_backtest_detail(connection: Any, backtest_id: int, user_id: int) -> BacktestDetail:
    owned = backtests_repository.get_owned_backtest(connection, backtest_id, user_id)
    if owned is None:
        raise NotFoundError("backtest not found")

    metrics = None
    equity_curve: list[EquityCurveRow] = []
    if owned.backtest.status == "completed":
        metrics = backtests_repository.get_results(connection, backtest_id)
        equity_curve = backtests_repository.get_equity_curve(connection, backtest_id)

    return BacktestDetail(
        backtest=owned.backtest,
        research_outcome=owned.research_outcome,
        validation_messages=owned.validation_messages,
        metrics=metrics,
        equity_curve=equity_curve,
    )
