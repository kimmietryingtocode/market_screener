"""Request correlation, access logging, and a catch-all error handler."""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.observability import request_id_var

logger = logging.getLogger("app.request")

REQUEST_ID_HEADER = "X-Request-ID"


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def correlate_and_log(request: Request, call_next):
        # Honour an id from an upstream proxy/gateway so one trace spans
        # services; generate one otherwise.
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            # Logged here with the traceback, then re-raised so the handler
            # below turns it into the client-facing response.
            logger.exception(
                "request failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": duration_ms,
                },
            )
            # Deliberately NOT resetting the contextvar here: the exception
            # handler runs after this frame unwinds and needs the id to put
            # in the response. Each request gets its own context, so leaving
            # it set can't leak into another request.
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.info(
            "request completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        response.headers[REQUEST_ID_HEADER] = request_id
        request_id_var.reset(token)
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Return an opaque 500 with the request id.

        Never echo the exception text: it can carry connection strings, SQL,
        or internal paths. The id is what ties this response to the full
        traceback already in the logs.
        """
        return JSONResponse(
            status_code=500,
            content={
                "detail": "internal server error",
                "request_id": request_id_var.get(),
            },
        )
