FROM python:3.12-slim AS build

RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backtest /app/backtest
RUN rm -rf /app/backtest/build && make -C /app/backtest

FROM python:3.12-slim

WORKDIR /app
COPY worker/requirements.txt /app/worker/requirements.txt
RUN pip install --no-cache-dir -r /app/worker/requirements.txt
COPY --from=build /app/backtest /app/backtest
COPY worker /app/worker
COPY screener /app/screener

ENV PYTHONPATH=/app
ENTRYPOINT ["python"]
# Default keeps the original single-job behavior; docker-compose overrides
# `command` per service to run the ingestion or backtest poller loops instead.
CMD ["-m", "worker.entrypoint"]
