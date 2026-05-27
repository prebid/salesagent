"""Shared DB session accessor for BDD steps."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Generator

    from sqlalchemy.orm import Session


@contextmanager
def db_session(ctx: dict[str, Any]) -> Generator[Session, None, None]:
    """Get a DB session from the harness env or global session factory."""
    env = ctx.get("env")
    if env is not None and hasattr(env, "_session") and env._session is not None:
        yield env._session
        return
    from src.core.database.database_session import get_db_session

    with get_db_session() as session:
        yield session
