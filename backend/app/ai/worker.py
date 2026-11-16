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


# Failures worth another attempt, matched on the text of the exception.
#
# Checking exception classes would read better, but the provider's errors
# reach us wrapped by langchain and langgraph, and which class survives the
# wrapping differs between the Gemini and Ollama paths. The message does
# not: an overloaded Gemini model says "503 ... currently experiencing high
# demand" whichever layer re-raises it.
#
# A missing attribute, a validation failure or a bad request is deterministic
# and excluded — retrying those just burns the attempt budget and makes the
# user wait longer for the same error.
_TRANSIENT_MARKERS = (
    "503", "unavailable", "overloaded", "high demand", "try again later",
    "429", "resource_exhausted", "resource exhausted", "rate limit", "quota",
    "500", "internal error", "502", "bad gateway", "504",
    "deadline exceeded", "timed out", "timeout",
    "connection reset", "connection aborted", "temporarily",
)


def is_transient(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def _finish_failure(connection, report_id: int, message_id: int | None, error: str, *, transient: bool) -> None:
    """Requeue a transient failure, fail anything else outright."""
    if transient and repository.retry_job(connection, report_id, message_id, error):
        logger.warning("AI job %s hit a transient error, requeued: %s", report_id, error)
        return
    repository.fail_job(connection, report_id, message_id, error)


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
            _finish_failure(connection, report_id, message_id, str(exc)[:1000], transient=is_transient(exc))


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
        # A run that overshot the timeout is usually a slow provider rather
        # than a broken job, so it gets another attempt like any other
        # transient failure. The attempt cap stops it repeating forever.
        error = f"AI job exceeded {timeout:g} seconds"
        logger.error("AI job %s failed: %s", report_id, error)
        _finish_failure(connection, report_id, message_id, error, transient=True)
    elif process.exitcode:
        # The child normally records its own outcome before exiting; this
        # covers the cases where it could not, such as being killed. If it
        # did record one, the job is no longer 'running' and both calls
        # below match nothing.
        error = f"AI worker subprocess exited with code {process.exitcode}"
        logger.error("AI job %s failed: %s", report_id, error)
        _finish_failure(connection, report_id, message_id, error, transient=True)
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
