"""Retry behaviour for AI jobs, against a real database.

A report died permanently the first time the provider said it was busy.
ai_jobs already carried attempts and run_after and claim_job already read
both, but nothing ever wrote them, so every failure was final. On a free
API tier "currently experiencing high demand" is routine, which made the
feature look broken when it was only busy.
"""

from __future__ import annotations

import os

import pytest

from app.ai import repository, worker
from app.ai.conftest import requires_db  # noqa: F401


def _queue(connection, user_id: int, kind: str = "stock", ticker: str | None = "AAPL") -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO ai_reports (user_id, kind, ticker, status, model_version, prompt_version) "
            "VALUES (%s, %s, %s, 'queued', 'test', 'v2') RETURNING id",
            (user_id, kind, ticker),
        )
        report_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO ai_jobs (report_id, status) VALUES (%s, 'queued')", (report_id,))
    return report_id


def _job(connection, report_id: int) -> tuple[str, int, bool]:
    """(status, attempts, run_after is in the future)"""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT status, attempts, run_after > now() FROM ai_jobs WHERE report_id = %s",
            (report_id,),
        )
        return cursor.fetchone()


def _report_status(connection, report_id: int) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT status FROM ai_reports WHERE id = %s", (report_id,))
        return cursor.fetchone()[0]


class TestTransientClassification:
    @pytest.mark.parametrize(
        "message",
        [
            "503 This model is currently experiencing high demand. Please try again later.",
            "429 Resource exhausted: quota exceeded",
            "504 Deadline Exceeded",
            "Connection reset by peer",
            "Request timed out",
        ],
    )
    def test_provider_capacity_errors_are_worth_retrying(self, message):
        assert worker.is_transient(RuntimeError(message))

    @pytest.mark.parametrize(
        "exc",
        [
            AttributeError("module 'app.repositories.scores_repository' has no attribute 'latest_score_date'"),
            ValueError("model output failed validation after one correction: empty structured response"),
            KeyError("ticker"),
        ],
    )
    def test_deterministic_failures_are_not_retried(self, exc):
        """Retrying these only delays the same error and spends the budget."""
        assert not worker.is_transient(exc)

    def test_the_503_that_actually_broke_a_run_is_caught(self):
        # Verbatim from the run against the live API.
        real = RuntimeError(
            "This model is currently experiencing high demand. Spikes in demand "
            "are usually temporary. Please try again later."
        )
        assert worker.is_transient(real)


@requires_db
class TestRetryJob:
    def test_a_transient_failure_goes_back_on_the_queue(self, connection, make_user):
        user_id = make_user()
        report_id = _queue(connection, user_id)
        assert repository.claim_job(connection) is not None

        assert repository.retry_job(connection, report_id, None, "503 high demand") is True

        status, attempts, delayed = _job(connection, report_id)
        assert status == "queued"
        assert attempts == 1
        assert delayed, "a retry must wait, or it re-hits the same busy model immediately"

    def test_the_report_shows_pending_not_failed_while_retrying(self, connection, make_user):
        """The UI polls the report. Flashing 'failed' for a run that is about
        to succeed is worse than showing it still working."""
        user_id = make_user()
        report_id = _queue(connection, user_id)
        repository.claim_job(connection)

        repository.retry_job(connection, report_id, None, "503 high demand")

        assert _report_status(connection, report_id) == "queued"

    def test_a_requeued_job_is_not_claimable_until_its_delay_passes(self, connection, make_user):
        user_id = make_user()
        report_id = _queue(connection, user_id)
        repository.claim_job(connection)
        repository.retry_job(connection, report_id, None, "503 high demand")

        assert repository.claim_job(connection) is None

        with connection.cursor() as cursor:
            cursor.execute("UPDATE ai_jobs SET run_after = now() - interval '1 second' WHERE report_id = %s", (report_id,))
        assert repository.claim_job(connection) == (report_id, None)

    def test_each_attempt_waits_longer_than_the_last(self, connection, make_user):
        user_id = make_user()
        report_id = _queue(connection, user_id)
        delays = []
        for _ in range(3):
            repository.claim_job(connection)
            assert repository.retry_job(connection, report_id, None, "503") is True
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT extract(epoch from (run_after - now())) FROM ai_jobs WHERE report_id = %s",
                    (report_id,),
                )
                delays.append(float(cursor.fetchone()[0]))
                cursor.execute("UPDATE ai_jobs SET run_after = now() WHERE report_id = %s", (report_id,))

        assert delays == sorted(delays), f"backoff must not shrink: {delays}"
        assert delays[-1] > delays[0]

    def test_retrying_stops_at_the_attempt_cap(self, connection, make_user):
        user_id = make_user()
        report_id = _queue(connection, user_id)
        cap = repository.max_job_attempts()

        for attempt in range(1, cap):
            repository.claim_job(connection)
            assert repository.retry_job(connection, report_id, None, "503") is True, f"attempt {attempt}"
            with connection.cursor() as cursor:
                cursor.execute("UPDATE ai_jobs SET run_after = now() WHERE report_id = %s", (report_id,))

        repository.claim_job(connection)
        assert repository.retry_job(connection, report_id, None, "503") is False
        assert _job(connection, report_id)[1] == cap

    def test_the_caller_fails_the_job_once_retries_are_exhausted(self, connection, make_user):
        user_id = make_user()
        report_id = _queue(connection, user_id)
        with connection.cursor() as cursor:
            cursor.execute("UPDATE ai_jobs SET attempts = %s WHERE report_id = %s", (repository.max_job_attempts(), report_id))
        repository.claim_job(connection)

        worker._finish_failure(connection, report_id, None, "503 high demand", transient=True)

        assert _job(connection, report_id)[0] == "failed"
        assert _report_status(connection, report_id) == "failed"

    def test_a_deterministic_failure_is_not_requeued_even_once(self, connection, make_user):
        user_id = make_user()
        report_id = _queue(connection, user_id)
        repository.claim_job(connection)

        worker._finish_failure(connection, report_id, None, "no attribute 'latest_score_date'", transient=False)

        assert _job(connection, report_id)[0] == "failed"
        assert _report_status(connection, report_id) == "failed"


@requires_db
class TestStaleJobReaper:
    def test_a_worker_that_died_mid_run_releases_its_job(self, connection, make_user):
        user_id = make_user()
        report_id = _queue(connection, user_id)
        repository.claim_job(connection)
        with connection.cursor() as cursor:
            cursor.execute("UPDATE ai_jobs SET claimed_at = now() - interval '10 minutes' WHERE report_id = %s", (report_id,))

        assert repository.claim_job(connection) == (report_id, None)

    def test_a_job_that_hangs_every_time_is_eventually_failed(self, connection, make_user):
        """Without the attempt cap the reaper cycles it between running and
        queued forever, holding the user's one concurrent slot."""
        user_id = make_user()
        report_id = _queue(connection, user_id)
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE ai_jobs SET status = 'running', attempts = %s, claimed_at = now() - interval '10 minutes' "
                "WHERE report_id = %s",
                (repository.max_job_attempts(), report_id),
            )

        assert repository.claim_job(connection) is None
        assert _job(connection, report_id)[0] == "failed"
        assert _report_status(connection, report_id) == "failed"


@requires_db
def test_a_user_whose_job_is_retrying_still_holds_their_one_slot(connection, make_user):
    """create_report refuses a second concurrent job. A requeued job is
    still in flight, so it has to keep counting."""
    from app.services.errors import RateLimitedError

    user_id = make_user()
    report_id = _queue(connection, user_id)
    repository.claim_job(connection)
    repository.retry_job(connection, report_id, None, "503")

    os.environ.setdefault("AI_PROVIDER", "ollama")
    with pytest.raises(RateLimitedError):
        repository.create_report(connection, user_id, "stock", "MSFT", None)
