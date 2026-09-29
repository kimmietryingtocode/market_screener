from fastapi import APIRouter, Depends, Request

from app.core.http_errors import to_http_exception
from app.core.security import current_user_id
from app.services import analytics_service
from app.services.errors import ServiceError

router = APIRouter(prefix="/api/v1/portfolios", tags=["analytics"])


@router.get("/{portfolio_id}/analytics")
def portfolio_analytics(
    request: Request,
    portfolio_id: int,
    user_id: int = Depends(current_user_id),
) -> dict:
    try:
        with request.app.state.db.connection() as connection:
            return analytics_service.get_portfolio_analytics(connection, portfolio_id, user_id)
    except ServiceError as exc:
        raise to_http_exception(exc)
