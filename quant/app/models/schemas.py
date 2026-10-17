from typing import Optional
from pydantic import BaseModel, Field, field_validator


class Holding(BaseModel):
    ticker: str
    weight: float = Field(gt=0, le=1)


class AnalyticsRequest(BaseModel):
    holdings: list[Holding]
    lookback: str = "1y"  # e.g. "6mo", "1y", "5y" — passed straight to yfinance

    @field_validator("holdings")
    @classmethod
    def weights_sum_to_one(cls, v: list[Holding]) -> list[Holding]:
        total = sum(h.weight for h in v)
        if not (0.999 <= total <= 1.001):
            raise ValueError(f"holding weights must sum to 1.0, got {total:.4f}")
        return v


class AnalyticsResponse(BaseModel):
    # Optional because a metric can be genuinely uncomputable (a wiped-out
    # portfolio has no annual rate). null says so; NaN would be invalid JSON.
    annualized_return: Optional[float] = None
    volatility: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    max_drawdown: Optional[float] = None
    total_return: Optional[float] = None
    lookback: str
    observations: Optional[int] = None
    extra: Optional[dict[str, float]] = None


class MarketQuote(BaseModel):
    ticker: str
    price: Optional[float] = None
    previous_close: Optional[float] = None
    change: Optional[float] = None
    change_percent: Optional[float] = None
    open: Optional[float] = None
    day_high: Optional[float] = None
    day_low: Optional[float] = None
    fifty_two_week_high: Optional[float] = None
    fifty_two_week_low: Optional[float] = None
    volume: Optional[int] = None
    market_cap: Optional[int] = None
    currency: Optional[str] = None
    exchange: Optional[str] = None
    quote_type: Optional[str] = None


class MarketQuotesResponse(BaseModel):
    tickers: list[str]
    quotes: list[MarketQuote]


class PriceBar(BaseModel):
    timestamp: str
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    adjusted_close: Optional[float] = None
    volume: Optional[int] = None


class PriceHistoryResponse(BaseModel):
    tickers: list[str]
    period: str
    interval: str
    bars: dict[str, list[PriceBar]]
