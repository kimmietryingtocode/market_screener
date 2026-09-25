from fastapi import APIRouter, Depends, HTTPException, Request, status
from psycopg.errors import UniqueViolation

from app.core.security import current_user_id
from app.schemas import CreatePortfolioRequest, HoldingResponse, PortfolioResponse

router = APIRouter(prefix="/api/v1/portfolios", tags=["portfolios"])


def validate_weights(payload: CreatePortfolioRequest) -> None:
    total = sum(holding.weight for holding in payload.holdings)
    if not 0.999 <= total <= 1.001:
        raise HTTPException(status_code=400, detail=f"holding weights must sum to 1.0, got {total:.4f}")


@router.post("", response_model=PortfolioResponse, status_code=status.HTTP_201_CREATED)
def create_portfolio(
    request: Request,
    payload: CreatePortfolioRequest,
    user_id: int = Depends(current_user_id),
) -> PortfolioResponse:
    validate_weights(payload)
    try:
        with request.app.state.db.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO portfolios (user_id, name) VALUES (%s, %s) RETURNING id, created_at",
                    (user_id, payload.name),
                )
                portfolio_id, created_at = cursor.fetchone()
                holdings = []
                for holding in payload.holdings:
                    cursor.execute(
                        """INSERT INTO holdings (portfolio_id, ticker, weight)
                           VALUES (%s, %s, %s) RETURNING id""",
                        (portfolio_id, holding.ticker.upper(), holding.weight),
                    )
                    holdings.append(HoldingResponse(
                        id=cursor.fetchone()[0],
                        portfolio_id=portfolio_id,
                        ticker=holding.ticker.upper(),
                        weight=holding.weight,
                    ))
    except UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="portfolio name or ticker already exists") from exc

    return PortfolioResponse(
        id=portfolio_id,
        name=payload.name,
        created_at=created_at,
        holdings=holdings,
    )


@router.get("", response_model=list[PortfolioResponse])
def list_portfolios(
    request: Request,
    user_id: int = Depends(current_user_id),
) -> list[PortfolioResponse]:
    with request.app.state.db.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, name, created_at FROM portfolios WHERE user_id = %s ORDER BY created_at DESC",
                (user_id,),
            )
            rows = cursor.fetchall()

    return [PortfolioResponse(id=row[0], name=row[1], created_at=row[2]) for row in rows]
