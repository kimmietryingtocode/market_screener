from datetime import date, datetime

from pydantic import BaseModel, Field


class AuthRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)


class AuthResponse(BaseModel):
    token: str


class HoldingInput(BaseModel):
    ticker: str = Field(min_length=1, max_length=16)
    weight: float = Field(gt=0, le=1)


class CreatePortfolioRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    holdings: list[HoldingInput] = Field(min_length=1)


class HoldingResponse(HoldingInput):
    id: int
    portfolio_id: int


class PortfolioResponse(BaseModel):
    id: int
    name: str
    created_at: datetime
    holdings: list[HoldingResponse] = []


class CreateBacktestRequest(BaseModel):
    portfolio_id: int
    benchmark: str = Field(min_length=1, max_length=16)
    start_date: date
    end_date: date
    rebalance_frequency: str = "monthly"
    cost_bps: float = Field(default=10.0, ge=0)
    initial_capital: float = Field(default=100_000.0, gt=0)


class BacktestResponse(BaseModel):
    id: int
    portfolio_id: int | None
    status: str
    benchmark: str
    start_date: date
    end_date: date
    rebalance_frequency: str
    created_at: datetime
    completed_at: datetime | None


class BacktestMetrics(BaseModel):
    total_return: float | None = None
    annualized_return: float | None = None
    volatility: float | None = None
    sharpe_ratio: float | None = None
    max_drawdown: float | None = None
    alpha_vs_benchmark: float | None = None


class EquityCurvePoint(BaseModel):
    trading_date: date
    portfolio_value: float
    benchmark_value: float


class BacktestDetailResponse(BacktestResponse):
    research_outcome: str | None = None
    validation_messages: list[dict] = []
    metrics: BacktestMetrics | None = None
    equity_curve: list[EquityCurvePoint] = []


class RankingEntry(BaseModel):
    rank: int
    ticker: str
    company_name: str | None = None
    sector: str | None = None
    score_date: date
    momentum_score: float
    fundamental_score: float
    composite_score: float
    components: dict = {}
    methodology_version: str
