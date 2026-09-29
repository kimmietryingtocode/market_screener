from fastapi import APIRouter, Depends, Query, Request

from app.core.http_errors import to_http_exception
from app.core.security import current_user_id
from app.schemas import RankingEntry
from app.services import ranking_service
from app.services.errors import ServiceError

router = APIRouter(prefix="/api/v1/rankings", tags=["rankings"])


@router.get("", response_model=list[RankingEntry])
def list_rankings(
    request: Request,
    limit: int = Query(default=20, ge=1, le=ranking_service.MAX_LIMIT),
    sector: str | None = Query(default=None),
    methodology_version: str | None = Query(default=None),
    user_id: int = Depends(current_user_id),
) -> list[RankingEntry]:
    try:
        with request.app.state.db.connection() as connection:
            rows = ranking_service.get_rankings(
                connection, limit=limit, sector=sector, methodology_version=methodology_version
            )
    except ServiceError as exc:
        raise to_http_exception(exc)

    return [
        RankingEntry(rank=index, **row._asdict())
        for index, row in enumerate(rows, start=1)
    ]
