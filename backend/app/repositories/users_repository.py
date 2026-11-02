"""Raw SQL for the `users` table. No hashing, tokens, or HTTP concerns here —
that belongs to app.services.auth_service."""

from typing import Any


def insert_user(connection: Any, email: str, password_hash: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id",
            (email, password_hash),
        )
        return cursor.fetchone()[0]


def find_by_email(connection: Any, email: str) -> tuple[int, str] | None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, password_hash FROM users WHERE email = %s",
            (email,),
        )
        return cursor.fetchone()


def find_by_google_sub(connection: Any, google_sub: str) -> tuple[int, str] | None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, email FROM users WHERE google_sub = %s",
            (google_sub,),
        )
        return cursor.fetchone()


def insert_google_user(
    connection: Any,
    email: str,
    password_hash: str,
    google_sub: str,
) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO users (email, password_hash, google_sub)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (email, password_hash, google_sub),
        )
        return cursor.fetchone()[0]
