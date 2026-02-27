"""Unit of Work — single-session boundary for MediaBuy operations.

Manages session lifecycle: creates on entry, commits on clean exit,
rolls back on exception. Provides tenant-scoped repositories.

Usage:
    with MediaBuyUoW(tenant_id) as uow:
        media_buy = uow.media_buys.get_by_id("mb_123")
        # ... business logic ...
        # auto-commits when exiting the `with` block
        # auto-rolls-back if an exception is raised

beads: salesagent-t735 (foundation), salesagent-2lp8 (epic)
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self

from sqlalchemy.orm import Session

from src.core.database.database_session import get_db_session
from src.core.database.repositories.media_buy import MediaBuyRepository


class MediaBuyUoW:
    """Unit of Work for MediaBuy operations.

    Wraps a database session and provides a tenant-scoped MediaBuyRepository.
    Auto-commits on clean exit, rolls back on exception.

    Args:
        tenant_id: Tenant scope for all repository queries.
    """

    def __init__(self, tenant_id: str) -> None:
        self._tenant_id = tenant_id
        self._session_cm: Any = None
        self.session: Session | None = None
        self.media_buys: MediaBuyRepository | None = None

    def __enter__(self) -> Self:
        self._session_cm = get_db_session()
        self.session = self._session_cm.__enter__()
        self.media_buys = MediaBuyRepository(self.session, self._tenant_id)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self.session is not None
        assert self._session_cm is not None
        if exc_type is None:
            self.session.commit()
        # get_db_session()'s __exit__ handles rollback on exception and cleanup
        self._session_cm.__exit__(exc_type, exc_val, exc_tb)
        self.session = None
        self.media_buys = None
