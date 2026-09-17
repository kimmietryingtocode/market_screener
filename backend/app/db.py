from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from psycopg_pool import ConnectionPool

from app.config import settings


def create_pool() -> ConnectionPool:
    return ConnectionPool(settings.database_url, open=False, kwargs={"autocommit": True})


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    pool = create_pool()
    pool.open()
    app.state.db = pool
    try:
        yield
    finally:
        pool.close()
