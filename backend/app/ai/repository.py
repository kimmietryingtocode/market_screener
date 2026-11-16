from __future__ import annotations

import json
import os
from typing import Any

from app.services.errors import NotFoundError, RateLimitedError
from app.services.errors import ValidationError
from app.ai import providers


def _report(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": row[0], "kind": row[1], "ticker": row[2], "backtest_id": row[3],
        "status": row[4], "report": row[5], "error": row[6],
        "created_at": row[7], "completed_at": row[8], "provider": row[9], "data_policy": row[10],
    }


def _report_columns() -> str:
    return "id, kind, ticker, backtest_id, status, report_json, error, created_at, completed_at, provider, data_policy"


def create_report(connection: Any, user_id: int, kind: str, ticker: str | None, backtest_id: int | None) -> dict:
    try:
        providers.ensure_configured()
    except RuntimeError as exc:
        raise ValidationError(str(exc)) from exc
    if kind == "backtest":
        try:
            providers.ensure_allowed({"kind": kind})
        except RuntimeError as exc:
            raise ValidationError(str(exc)) from exc
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(8812)")
            cursor.execute(
                "SELECT count(*) FROM ai_jobs j JOIN ai_reports r ON r.id = j.report_id "
                "WHERE r.user_id = %s AND j.status IN ('queued', 'running')",
                (user_id,),
            )
            if cursor.fetchone()[0] >= 1:
                raise RateLimitedError("you already have an AI analysis in progress")
            cursor.execute("SELECT count(*) FROM ai_jobs WHERE status = 'queued'")
            if cursor.fetchone()[0] >= 50:
                raise RateLimitedError("the AI analysis queue is full")
            if backtest_id is not None:
                cursor.execute("SELECT 1 FROM backtests WHERE id = %s AND user_id = %s", (backtest_id, user_id))
                if cursor.fetchone() is None:
                    raise NotFoundError("backtest not found")
            cursor.execute(
                "INSERT INTO ai_reports (user_id, kind, ticker, backtest_id, status, request_json, model_version, prompt_version, provider, data_policy) "
                "VALUES (%s, %s, %s, %s, 'queued', %s, %s, %s, %s, %s) RETURNING id",
                (user_id, kind, ticker.upper() if ticker else None, backtest_id,
                 json.dumps({"kind": kind, "ticker": ticker, "backtest_id": backtest_id}),
                 providers.version(), "v2", providers.provider(), providers.data_policy()),
            )
            report_id = cursor.fetchone()[0]
            cursor.execute("INSERT INTO ai_jobs (report_id, status) VALUES (%s, 'queued')", (report_id,))
    return get_report(connection, user_id, report_id)


def get_report(connection: Any, user_id: int, report_id: int) -> dict:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {_report_columns()} FROM ai_reports WHERE id = %s AND user_id = %s", (report_id, user_id))
        row = cursor.fetchone()
    if row is None:
        raise NotFoundError("AI report not found")
    result = _report(row)
    with connection.cursor() as cursor:
        cursor.execute("SELECT source_key, excerpt, metadata FROM ai_report_citations WHERE report_id = %s ORDER BY id", (report_id,))
        result["citations"] = [dict(source_key=r[0], excerpt=r[1], metadata=r[2] or {}) for r in cursor.fetchall()]
    return result


def list_reports(connection: Any, user_id: int) -> list[dict]:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {_report_columns()} FROM ai_reports WHERE user_id = %s ORDER BY created_at DESC", (user_id,))
        return [_report(row) for row in cursor.fetchall()]


def list_messages(connection: Any, user_id: int, report_id: int) -> list[dict]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT m.id, m.role, m.content, m.status, m.citations, m.created_at "
            "FROM ai_messages m JOIN ai_reports r ON r.id = m.report_id "
            "WHERE m.report_id = %s AND r.user_id = %s ORDER BY m.created_at",
            (report_id, user_id),
        )
        return [dict(id=r[0], role=r[1], content=r[2], status=r[3], citations=r[4] or [], created_at=r[5]) for r in cursor.fetchall()]


def enqueue_message(connection: Any, user_id: int, report_id: int, content: str) -> dict:
    try:
        providers.ensure_configured()
    except RuntimeError as exc:
        raise ValidationError(str(exc)) from exc
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(8812)")
            cursor.execute("SELECT kind, provider, data_policy FROM ai_reports WHERE id = %s AND user_id = %s AND status = 'completed'", (report_id, user_id))
            report_row = cursor.fetchone()
            if report_row and report_row[0] == "backtest" and report_row[1] == "gemini" and report_row[2] == "public_only":
                raise ValidationError("Private backtest chat is disabled for Gemini; use Ollama")
            if report_row is None:
                raise NotFoundError("completed AI report not found")
            cursor.execute("SELECT count(*) FROM ai_jobs j JOIN ai_reports r ON r.id = j.report_id WHERE r.user_id = %s AND j.status IN ('queued', 'running')", (user_id,))
            if cursor.fetchone()[0] >= 1:
                raise RateLimitedError("you already have an AI analysis in progress")
            cursor.execute("SELECT count(*) FROM ai_jobs WHERE status = 'queued'")
            if cursor.fetchone()[0] >= 50:
                raise RateLimitedError("the AI analysis queue is full")
            cursor.execute("INSERT INTO ai_messages (report_id, user_id, role, content, status) VALUES (%s, %s, 'user', %s, 'queued') RETURNING id, created_at", (report_id, user_id, content))
            message_id, created_at = cursor.fetchone()
            cursor.execute("INSERT INTO ai_jobs (report_id, message_id, status) VALUES (%s, %s, 'queued')", (report_id, message_id))
    return {"id": message_id, "role": "user", "content": content, "status": "queued", "created_at": created_at, "citations": []}


def claim_job(connection: Any) -> tuple[int, int | None] | None:
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("UPDATE ai_jobs SET status = 'queued', claimed_at = NULL WHERE status = 'running' AND claimed_at < now() - interval '5 minutes'")
            cursor.execute(
                "UPDATE ai_jobs SET status = 'running', claimed_at = now(), attempts = attempts + 1 "
                "WHERE id = (SELECT id FROM ai_jobs WHERE status = 'queued' AND run_after <= now() ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1) "
                "RETURNING id, report_id, message_id"
            )
            row = cursor.fetchone()
            if row:
                cursor.execute("UPDATE ai_reports SET status = 'running', updated_at = now() WHERE id = %s AND status = 'queued'", (row[1],))
    return (row[1], row[2]) if row else None


def job_context(connection: Any, report_id: int, message_id: int | None) -> dict:
    with connection.cursor() as cursor:
        cursor.execute("SELECT user_id, kind, ticker, backtest_id, facts_json, provider, data_policy FROM ai_reports WHERE id = %s", (report_id,))
        report = cursor.fetchone()
        if report is None:
            raise NotFoundError("AI report not found")
        message = None
        if message_id:
            cursor.execute("SELECT content FROM ai_messages WHERE id = %s AND report_id = %s", (message_id, report_id))
            message = cursor.fetchone()[0]
        cursor.execute("SELECT role, content FROM ai_messages WHERE report_id = %s ORDER BY created_at DESC LIMIT 8", (report_id,))
        history = [{"role": r[0], "content": r[1]} for r in reversed(cursor.fetchall())]
    return {"user_id": report[0], "kind": report[1], "ticker": report[2], "backtest_id": report[3], "facts": report[4], "provider": report[5], "data_policy": report[6], "message": message, "history": history}


def save_report_result(connection: Any, report_id: int, facts: dict, result: dict, citations: list[dict]) -> None:
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("UPDATE ai_reports SET status = 'completed', facts_json = %s, report_json = %s, error = NULL, completed_at = now(), updated_at = now() WHERE id = %s", (json.dumps(facts), json.dumps(result), report_id))
            for citation in citations:
                cursor.execute("INSERT INTO ai_report_citations (report_id, document_id, source_key, excerpt, metadata) VALUES (%s, %s, %s, %s, %s)", (report_id, citation.get("document_id"), citation["source_key"], citation["excerpt"], json.dumps(citation.get("metadata", {}))))


def save_message_result(connection: Any, report_id: int, message_id: int, content: str, citations: list[dict]) -> None:
    if not content.strip():
        raise ValueError("assistant message cannot be empty")
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("UPDATE ai_messages SET status = 'completed' WHERE id = %s", (message_id,))
            cursor.execute("INSERT INTO ai_messages (report_id, user_id, role, content, status, citations) SELECT report_id, user_id, 'assistant', %s, 'completed', %s FROM ai_messages WHERE id = %s", (content, json.dumps(citations), message_id))
            cursor.execute("UPDATE ai_jobs SET status = 'completed' WHERE report_id = %s AND message_id = %s", (report_id, message_id))


def fail_job(connection: Any, report_id: int, message_id: int | None, error: str) -> None:
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("UPDATE ai_jobs SET status = 'failed', error = %s WHERE report_id = %s AND status = 'running'", (error, report_id))
            if message_id:
                cursor.execute("UPDATE ai_messages SET status = 'failed' WHERE id = %s", (message_id,))
            else:
                cursor.execute("UPDATE ai_reports SET status = 'failed', error = %s, updated_at = now() WHERE id = %s", (error, report_id))
