from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


ReportKind = Literal["recommendations", "stock", "backtest"]


class CreateAIReportRequest(BaseModel):
    kind: ReportKind
    ticker: str | None = Field(default=None, min_length=1, max_length=16)
    backtest_id: int | None = None

    @model_validator(mode="after")
    def validate_target(self):
        if self.kind == "stock" and not self.ticker:
            raise ValueError("ticker is required for a stock report")
        if self.kind == "backtest" and self.backtest_id is None:
            raise ValueError("backtest_id is required for a backtest report")
        if self.kind != "backtest" and self.backtest_id is not None:
            raise ValueError("backtest_id is only valid for backtest reports")
        if self.kind != "stock" and self.ticker is not None:
            raise ValueError("ticker is only valid for stock reports")
        return self


class CreateAIMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class AIReportResponse(BaseModel):
    id: int
    kind: ReportKind
    ticker: str | None = None
    backtest_id: int | None = None
    status: str
    report: dict | None = None
    citations: list[dict] = []
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    provider: str = "ollama"
    data_policy: str = "private_allowed"


class AIMessageResponse(BaseModel):
    id: int
    role: str
    content: str
    status: str
    citations: list[dict] = []
    created_at: datetime


class AIConfigResponse(BaseModel):
    provider: str
    data_policy: str
    model: str
    embedding_model: str
    embedding_dimensions: int
    api_key_configured: bool
