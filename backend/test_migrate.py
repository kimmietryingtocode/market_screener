"""Tests for migrate.py, against a live Postgres and a scratch migrations
directory (never the real backend/migrations/) so it's safe to actually
apply/break things."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest

import migrate

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")


def _db_available() -> bool:
    if not TEST_DATABASE_URL:
        return False
    try:
        with psycopg.connect(TEST_DATABASE_URL, connect_timeout=2):
            return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(not _db_available(), reason="TEST_DATABASE_URL not set or unreachable")


@pytest.fixture
def scratch_schema(tmp_path, monkeypatch):
    """Points migrate.py at a throwaway migrations dir and a throwaway
    Postgres schema, so tests never touch the real schema_migrations table
    or the real app tables."""
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)
    schema = f"migrate_test_{uuid.uuid4().hex[:8]}"
    connection = psycopg.connect(TEST_DATABASE_URL)
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute(f"CREATE SCHEMA {schema}")
        cursor.execute(f"SET search_path TO {schema}")
    yield tmp_path, connection
    with connection.cursor() as cursor:
        cursor.execute(f"DROP SCHEMA {schema} CASCADE")
    connection.close()


@requires_db
def test_migrate_applies_pending_files_in_order(scratch_schema):
    tmp_path, connection = scratch_schema
    (tmp_path / "001_a.sql").write_text("CREATE TABLE a (id int);")
    (tmp_path / "002_b.sql").write_text("CREATE TABLE b (id int);")

    applied = migrate.migrate(connection)

    assert applied == ["001_a.sql", "002_b.sql"]
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM a")
        cursor.execute("SELECT count(*) FROM b")  # both tables exist, no error


@requires_db
def test_migrate_is_idempotent_on_rerun(scratch_schema):
    tmp_path, connection = scratch_schema
    (tmp_path / "001_a.sql").write_text("CREATE TABLE a (id int);")

    first = migrate.migrate(connection)
    second = migrate.migrate(connection)

    assert first == ["001_a.sql"]
    assert second == []  # already applied, not re-run (which would error: table exists)


@requires_db
def test_migrate_raises_if_an_applied_file_was_edited(scratch_schema):
    tmp_path, connection = scratch_schema
    path = tmp_path / "001_a.sql"
    path.write_text("CREATE TABLE a (id int);")
    migrate.migrate(connection)

    path.write_text("CREATE TABLE a (id int, extra text);")  # edited after shipping

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        migrate.migrate(connection)


@requires_db
def test_failed_migration_does_not_get_marked_applied(scratch_schema):
    tmp_path, connection = scratch_schema
    (tmp_path / "001_bad.sql").write_text("THIS IS NOT VALID SQL;")

    with pytest.raises(psycopg.errors.SyntaxError):
        migrate.migrate(connection)

    assert migrate.pending(connection) == [tmp_path / "001_bad.sql"]


@requires_db
def test_baseline_records_without_running(scratch_schema):
    tmp_path, connection = scratch_schema
    (tmp_path / "001_a.sql").write_text("CREATE TABLE a (id int);")

    applied = migrate.baseline(connection)

    assert applied == ["001_a.sql"]
    with connection.cursor() as cursor:
        with pytest.raises(psycopg.errors.UndefinedTable):
            cursor.execute("SELECT * FROM a")  # baseline never ran the SQL
