"""Fixtures for the AI tests.

Reuses the service-layer fixtures rather than defining a second set: same
TEST_DATABASE_URL convention, same per-test throwaway user that cascades
away at teardown.
"""

from __future__ import annotations

from app.services.conftest import (  # noqa: F401  (re-exported as fixtures)
    DB_AVAILABLE,
    connection,
    make_user,
    requires_db,
)
