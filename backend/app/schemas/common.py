from datetime import datetime

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
