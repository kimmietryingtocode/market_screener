from fastapi import APIRouter, HTTPException

from app.models.schemas import AnalyticsRequest, AnalyticsResponse
from app.services.portfolio_analytics import compute_portfolio_analytics

router = APIRouter()


@router.post("/portfolio", response_model=AnalyticsResponse)
def get_portfolio_analytics(req: AnalyticsRequest):
    tickers = [h.ticker for h in req.holdings]
    weights = {h.ticker: h.weight for h in req.holdings}

    try:
        result = compute_portfolio_analytics(tickers, weights, req.lookback)
    except ValueError as e:
        # Bad ticker, no data for lookback window, etc. — surface as 422
        # rather than letting it bubble up as an opaque 500.
        raise HTTPException(status_code=422, detail=str(e))

    return AnalyticsResponse(**result)
