from fastapi import FastAPI
from app.routers import analytics, market

app = FastAPI(title="Portfolio Quant Service", version="0.1.0")

app.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
app.include_router(market.router, prefix="/market", tags=["market"])


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
