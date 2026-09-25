import json

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.config import settings
from app.core.security import current_user_id

router = APIRouter(prefix="/api/v1/portfolios", tags=["analytics"])


@router.get("/{portfolio_id}/analytics")
def portfolio_analytics(
    request: Request,
    portfolio_id: int,
    user_id: int = Depends(current_user_id),
) -> dict:
    with request.app.state.db.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT ticker, weight FROM holdings
                   WHERE portfolio_id = %s
                   AND portfolio_id IN (
                       SELECT id FROM portfolios WHERE user_id = %s
                   )""",
                (portfolio_id, user_id),
            )
            holdings = [
                {"ticker": row[0], "weight": float(row[1])}
                for row in cursor.fetchall()
            ]

    if not holdings:
        raise HTTPException(status_code=404, detail="portfolio not found or has no holdings")

    try:
        response = httpx.post(
            f"{settings.quant_service_url}/analytics/portfolio",
            json={"holdings": holdings, "lookback": "1y"},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="quant service unreachable") from exc

    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="quant service returned an error")

    result = response.json()
    with request.app.state.db.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO analytics_runs (portfolio_id, result_json) VALUES (%s, %s)",
                (portfolio_id, json.dumps(result)),
            )

    return result
