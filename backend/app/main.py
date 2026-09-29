from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core import middleware
from app.core.config import settings
from app.core.observability import configure_logging
from app.database.session import lifespan
from app.routers import analytics, auth, backtests, health, portfolios, rankings

configure_logging(settings.log_level)

app = FastAPI(title="Market Screener API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", middleware.REQUEST_ID_HEADER],
)
middleware.install(app)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(portfolios.router)
app.include_router(analytics.router)
app.include_router(backtests.router)
app.include_router(rankings.router)
