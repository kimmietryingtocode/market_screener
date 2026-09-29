"""Raw SQL for `analytics_runs` — the cached result of a quant-service call."""

import json
from typing import Any


def insert_run(connection: Any, portfolio_id: int, result: dict) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO analytics_runs (portfolio_id, result_json) VALUES (%s, %s)",
            (portfolio_id, json.dumps(result)),
        )
