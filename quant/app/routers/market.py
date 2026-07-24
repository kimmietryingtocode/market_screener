from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import MarketQuotesResponse, PriceHistoryResponse
from app.services.market_data import fetch_market_quotes, fetch_price_bars, parse_tickers

router = APIRouter()


@router.get("/quotes", response_model=MarketQuotesResponse)
def get_market_quotes(
    tickers: str | None = Query(
        default=None,
        description="Comma-separated ticker list. Defaults to broad market tickers.",
    ),
):
    try:
        parsed_tickers = parse_tickers(tickers)
        quotes = fetch_market_quotes(parsed_tickers)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return MarketQuotesResponse(tickers=parsed_tickers, quotes=quotes)


@router.get("/history", response_model=PriceHistoryResponse)
def get_price_history(
    tickers: str | None = Query(
        default=None,
        description="Comma-separated ticker list. Defaults to broad market tickers.",
    ),
    period: str = Query(default="1mo", description='yfinance period, e.g. "5d", "1mo", "1y".'),
    interval: str = Query(default="1d", description='yfinance interval, e.g. "1d", "1h", "5m".'),
):
    try:
        parsed_tickers = parse_tickers(tickers)
        bars = fetch_price_bars(parsed_tickers, period, interval)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return PriceHistoryResponse(
        tickers=parsed_tickers,
        period=period,
        interval=interval,
        bars=bars,
    )
