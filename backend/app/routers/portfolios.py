from fastapi import APIRouter, Depends, Request, status

from app.core.http_errors import to_http_exception
from app.core.security import current_user_id
from app.schemas import CreatePortfolioRequest, HoldingResponse, PortfolioResponse
from app.services import portfolio_service
from app.services.errors import ServiceError

router = APIRouter(prefix="/api/v1/portfolios", tags=["portfolios"])


@router.post("", response_model=PortfolioResponse, status_code=status.HTTP_201_CREATED)
def create_portfolio(
    request: Request,
    payload: CreatePortfolioRequest,
    user_id: int = Depends(current_user_id),
) -> PortfolioResponse:
    try:
        with request.app.state.db.connection() as connection:
            portfolio, holdings = portfolio_service.create_portfolio(connection, user_id, payload)
    except ServiceError as exc:
        raise to_http_exception(exc)

    return PortfolioResponse(
        id=portfolio.id,
        name=portfolio.name,
        created_at=portfolio.created_at,
        holdings=[HoldingResponse(**h._asdict()) for h in holdings],
    )


@router.get("", response_model=list[PortfolioResponse])
def list_portfolios(
    request: Request,
    user_id: int = Depends(current_user_id),
) -> list[PortfolioResponse]:
    with request.app.state.db.connection() as connection:
        portfolios = portfolio_service.list_portfolios(connection, user_id)

    return [PortfolioResponse(id=p.id, name=p.name, created_at=p.created_at) for p in portfolios]


@router.put("/{portfolio_id}", response_model=PortfolioResponse)
def update_portfolio(
    request: Request,
    portfolio_id: int,
    payload: CreatePortfolioRequest,
    user_id: int = Depends(current_user_id),
) -> PortfolioResponse:
    try:
        with request.app.state.db.connection() as connection:
            portfolio, holdings = portfolio_service.update_portfolio(connection, user_id, portfolio_id, payload)
    except ServiceError as exc:
        raise to_http_exception(exc)

    return PortfolioResponse(
        id=portfolio.id,
        name=portfolio.name,
        created_at=portfolio.created_at,
        holdings=[HoldingResponse(**h._asdict()) for h in holdings],
    )


@router.delete("/{portfolio_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_portfolio(
    request: Request,
    portfolio_id: int,
    user_id: int = Depends(current_user_id),
) -> None:
    try:
        with request.app.state.db.connection() as connection:
            portfolio_service.delete_portfolio(connection, user_id, portfolio_id)
    except ServiceError as exc:
        raise to_http_exception(exc)
