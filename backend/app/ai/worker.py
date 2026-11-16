from __future__ import annotations

import os
import time
import logging
import multiprocessing

import psycopg

from app.ai import repository
from app.ai.workflow import run_analysis, run_chat

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


def _process_claimed_job(database_url: str, report_id: int, message_id: int | None) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        try:
            context = repository.job_context(connection, report_id, message_id)
            if message_id is None:
                facts, result, citations = run_analysis(connection, context)
                repository.save_report_result(connection, report_id, facts, result, citations)
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE ai_jobs SET status = 'completed' WHERE report_id = %s AND message_id IS NULL AND status = 'running'", (report_id,))
            else:
                answer = run_chat(context)
                repository.save_message_result(connection, report_id, message_id, answer, [])
        except Exception as exc:
            logger.exception("AI job %s failed", report_id)
            repository.fail_job(connection, report_id, message_id, str(exc)[:1000])


def process_once(connection, database_url: str | None = None) -> bool:
    job = repository.claim_job(connection)
    if job is None:
        return False
    report_id, message_id = job
    database_url = database_url or connection.info.dsn
    timeout = float(os.getenv("AI_JOB_TIMEOUT_SECONDS", "600"))
    process = multiprocessing.get_context("spawn").Process(
        target=_process_claimed_job,
        args=(database_url, report_id, message_id),
    )
    process.start()
    process.join(timeout)
    if process.is_alive():
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)
        error = f"AI job exceeded {timeout:g} seconds"
        logger.error("AI job %s failed: %s", report_id, error)
        repository.fail_job(connection, report_id, message_id, error)
    elif process.exitcode:
        error = f"AI worker subprocess exited with code {process.exitcode}"
        logger.error("AI job %s failed: %s", report_id, error)
        repository.fail_job(connection, report_id, message_id, error)
    return True


def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    poll_seconds = float(os.getenv("AI_POLL_SECONDS", "2"))
    with psycopg.connect(database_url, autocommit=True) as connection:
        while True:
            worked = process_once(connection, database_url)
            if not worked:
                time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
