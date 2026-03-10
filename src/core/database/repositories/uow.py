"""Unit of Work — single-session boundary for repository operations.

Manages session lifecycle: creates on entry, commits on clean exit,
rolls back on exception. Provides tenant-scoped repositories.

Usage:
    with MediaBuyUoW(tenant_id) as uow:
        media_buy = uow.media_buys.get_by_id("mb_123")
        # auto-commits when exiting the `with` block
        # auto-rolls-back if an exception is raised

    with ProductUoW(tenant_id) as uow:
        products = uow.products.list_all()
        # auto-commits when exiting the `with` block

    with WorkflowUoW(tenant_id) as uow:
        steps = uow.workflows.list_by_tenant(status="pending")
        # auto-commits when exiting the `with` block

    with TenantConfigUoW(tenant_id) as uow:
        partners = uow.tenant_config.list_publisher_partners()
        # auto-commits when exiting the `with` block

beads: salesagent-t735 (foundation), salesagent-2lp8 (epic), salesagent-rn59 (ProductUoW), salesagent-4d4 (WorkflowUoW), salesagent-9y0 (TenantConfigUoW)
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self

from sqlalchemy.orm import Session

from src.core.database.database_session import get_db_session
from src.core.database.repositories.media_buy import MediaBuyRepository
from src.core.database.repositories.product import ProductRepository
from src.core.database.repositories.tenant_config import TenantConfigRepository
from src.core.database.repositories.workflow import WorkflowRepository


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


class ProductUoW:
    """Unit of Work for Product operations.

    Wraps a database session and provides a tenant-scoped ProductRepository.
    Auto-commits on clean exit, rolls back on exception.

    Args:
        tenant_id: Tenant scope for all repository queries.
    """

    def __init__(self, tenant_id: str) -> None:
        self._tenant_id = tenant_id
        self._session_cm: Any = None
        self.session: Session | None = None
        self.products: ProductRepository | None = None

    def __enter__(self) -> Self:
        self._session_cm = get_db_session()
        self.session = self._session_cm.__enter__()
        self.products = ProductRepository(self.session, self._tenant_id)
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
        self.products = None


class WorkflowUoW:
    """Unit of Work for Workflow operations.

    Wraps a database session and provides a tenant-scoped WorkflowRepository.
    Auto-commits on clean exit, rolls back on exception.

    Args:
        tenant_id: Tenant scope for all repository queries.
    """

    def __init__(self, tenant_id: str) -> None:
        self._tenant_id = tenant_id
        self._session_cm: Any = None
        self.session: Session | None = None
        self.workflows: WorkflowRepository | None = None

    def __enter__(self) -> Self:
        self._session_cm = get_db_session()
        self.session = self._session_cm.__enter__()
        self.workflows = WorkflowRepository(self.session, self._tenant_id)
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
        self.workflows = None


class TenantConfigUoW:
    """Unit of Work for tenant configuration reads.

    Wraps a database session and provides a tenant-scoped TenantConfigRepository.
    Auto-commits on clean exit, rolls back on exception.

    Args:
        tenant_id: Tenant scope for all repository queries.
    """

    def __init__(self, tenant_id: str) -> None:
        self._tenant_id = tenant_id
        self._session_cm: Any = None
        self.session: Session | None = None
        self.tenant_config: TenantConfigRepository | None = None

    def __enter__(self) -> Self:
        self._session_cm = get_db_session()
        self.session = self._session_cm.__enter__()
        self.tenant_config = TenantConfigRepository(self.session, self._tenant_id)
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
        self._session_cm.__exit__(exc_type, exc_val, exc_tb)
        self.session = None
        self.tenant_config = None
