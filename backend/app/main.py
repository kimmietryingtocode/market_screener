from fastapi import FastAPI

from app.db import lifespan
from app.routers import analytics, auth, portfolios

app = FastAPI(title="Market Screener API", version="0.1.0", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(portfolios.router)
app.include_router(analytics.router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
