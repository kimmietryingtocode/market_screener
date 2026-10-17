from fastapi import APIRouter, Depends, Request, status

from app.core.http_errors import to_http_exception
from app.core.security import current_user_id
from app.schemas import (
    BacktestDetailResponse,
    BacktestMetrics,
    BacktestResponse,
    CreateBacktestRequest,
    EquityCurvePoint,
)
from app.services import backtest_service
from app.services.errors import ServiceError

router = APIRouter(prefix="/api/v1/backtests", tags=["backtests"])


def _to_response(row) -> BacktestResponse:
    return BacktestResponse(**row._asdict())


@router.post("", response_model=BacktestResponse, status_code=status.HTTP_202_ACCEPTED)
def create_backtest(
    request: Request,
    payload: CreateBacktestRequest,
    user_id: int = Depends(current_user_id),
) -> BacktestResponse:
    try:
        with request.app.state.db.connection() as connection:
            backtest = backtest_service.create_backtest(connection, user_id, payload)
    except ServiceError as exc:
        raise to_http_exception(exc)
    return _to_response(backtest)


@router.get("", response_model=list[BacktestResponse])
def list_backtests(
    request: Request,
    user_id: int = Depends(current_user_id),
) -> list[BacktestResponse]:
    with request.app.state.db.connection() as connection:
        backtests = backtest_service.list_backtests(connection, user_id)
    return [_to_response(b) for b in backtests]


@router.get("/{backtest_id}", response_model=BacktestDetailResponse)
def get_backtest(
    request: Request,
    backtest_id: int,
    user_id: int = Depends(current_user_id),
) -> BacktestDetailResponse:
    try:
        with request.app.state.db.connection() as connection:
            detail = backtest_service.get_backtest_detail(connection, backtest_id, user_id)
    except ServiceError as exc:
        raise to_http_exception(exc)

    return BacktestDetailResponse(
        **detail.backtest._asdict(),
        research_outcome=detail.research_outcome,
        publishable=detail.publishable,
        available_at=detail.available_at,
        validation_messages=detail.validation_messages,
        metrics=BacktestMetrics(**detail.metrics._asdict()) if detail.metrics else None,
        equity_curve=[EquityCurvePoint(**p._asdict()) for p in detail.equity_curve],
    )
