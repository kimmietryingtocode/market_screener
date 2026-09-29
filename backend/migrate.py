"""Track and apply backend/migrations/*.sql against a real schema_migrations
table, instead of relying on docker's docker-entrypoint-initdb.d (which only
ever runs once, on a brand-new empty volume, and leaves every other
environment with no record of what's been applied).

Usage:
    python migrate.py                 # apply every pending migration
    python migrate.py --baseline      # mark all current files as applied
                                       # WITHOUT running them (for adopting
                                       # tracking on a database that already
                                       # has this schema by other means)
    python migrate.py --status        # print applied/pending, exit nonzero
                                       # if anything is pending
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
from typing import Any

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_tracking_table(connection: Any) -> None:
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    filename    TEXT PRIMARY KEY,
                    checksum    TEXT NOT NULL,
                    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )


def _applied(connection: Any) -> dict[str, str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT filename, checksum FROM schema_migrations")
        return dict(cursor.fetchall())


def _all_migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def pending(connection: Any) -> list[Path]:
    """Files not yet recorded as applied. Raises if a file that WAS applied
    has since changed on disk — migrations are append-only after they ship."""
    applied = _applied(connection)
    result = []
    for path in _all_migration_files():
        checksum = _checksum(path)
        if path.name in applied:
            if applied[path.name] != checksum:
                raise RuntimeError(
                    f"{path.name} was modified after being applied (checksum mismatch). "
                    "Never edit a migration that has shipped — add a new one instead."
                )
            continue
        result.append(path)
    return result


def migrate(connection: Any) -> list[str]:
    """Apply every pending migration, each in its own transaction."""
    ensure_tracking_table(connection)
    applied_names = []
    for path in pending(connection):
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(path.read_text())
                cursor.execute(
                    "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s)",
                    (path.name, _checksum(path)),
                )
        applied_names.append(path.name)
    return applied_names


def baseline(connection: Any) -> list[str]:
    """Record every currently pending file as applied WITHOUT running its
    SQL. For adopting migration tracking on a database whose schema already
    exists (e.g. it was created by hand, or by the old docker-entrypoint-
    initdb.d one-shot path)."""
    ensure_tracking_table(connection)
    names = []
    with connection.transaction():
        with connection.cursor() as cursor:
            for path in pending(connection):
                cursor.execute(
                    "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s)",
                    (path.name, _checksum(path)),
                )
                names.append(path.name)
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--baseline", action="store_true", help="mark current files applied without running them")
    parser.add_argument("--status", action="store_true", help="print status and exit nonzero if anything is pending")
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")

    with psycopg.connect(args.database_url) as connection:
        if args.status:
            ensure_tracking_table(connection)
            todo = [p.name for p in pending(connection)]
            if todo:
                print("pending: " + ", ".join(todo))
                return 1
            print("up to date")
            return 0

        if args.baseline:
            names = baseline(connection)
            print(f"baselined {len(names)} migration(s) as already applied: {', '.join(names) or '(none pending)'}")
            return 0

        names = migrate(connection)
        print(f"applied {len(names)} migration(s): {', '.join(names)}" if names else "no pending migrations")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
