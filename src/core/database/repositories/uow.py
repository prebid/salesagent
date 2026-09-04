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

"""

from __future__ import annotations

import logging
import warnings
from types import TracebackType
from typing import Any, Self

from sqlalchemy.orm import Session

from src.core.database.database_session import get_db_session
from src.core.database.repositories.account import AccountRepository
from src.core.database.repositories.creative import CreativeAssignmentRepository, CreativeRepository
from src.core.database.repositories.currency_limit import CurrencyLimitRepository
from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository
from src.core.database.repositories.media_buy import MediaBuyRepository
from src.core.database.repositories.product import ProductRepository
from src.core.database.repositories.push_notification_config import PushNotificationConfigRepository
from src.core.database.repositories.tenant_config import TenantConfigRepository
from src.core.database.repositories.tmp_provider import TMPProviderRepository
from src.core.database.repositories.workflow import WorkflowRepository
from src.core.exceptions import AdCPServiceUnavailableError

logger = logging.getLogger(__name__)


class RepositoryAccessor[RepoT]:
    """Exposes a UoW repository as a non-optional attribute.

    The repositories a UoW owns are created by ``_init_repos`` on ``__enter__``
    and cleared on ``__exit__``, so inside the ``with`` block they are always
    present.  Typing them ``Repository | None`` pushed that fact onto every call
    site, which then invented its own narrowing — bare ``assert`` in some files,
    a typed ``raise`` in others, sixteen of them in one feature (#1197 review).
    ``assert`` is the wrong one twice over: ``python -O`` strips it, and an
    ``AssertionError`` escapes a protocol surface as an un-enveloped 500 rather
    than the typed AdCP envelope the contract promises.

    This descriptor answers the question once.  Callers read
    ``uow.tmp_providers`` with no guard and get the concrete repository; reading
    it outside an open session raises the typed
    :class:`AdCPServiceUnavailableError` that the surfaces want anyway.
    """

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name
        self._slot = f"_repo_{name}"

    def __get__(self, instance: object, owner: type | None = None) -> RepoT:
        repo = getattr(instance, self._slot, None)
        if repo is None:
            raise AdCPServiceUnavailableError(
                f"{self._name} repository unavailable.",
                suggestion="Retry shortly; the sales agent could not open a database session.",
            )
        return repo

    def __set__(self, instance: object, value: RepoT | None) -> None:
        setattr(instance, self._slot, value)


class BaseUoW:
    """Base Unit of Work — handles session lifecycle.

    Subclasses implement ``_init_repos()`` to create tenant-scoped repositories
    and ``_clear_repos()`` to reset them on exit.

    Auto-commits on clean exit, rolls back on exception.

    The session is private (``_session``). Business logic should use
    repository methods, not raw session access.

    Args:
        tenant_id: Tenant scope for all repository queries.
    """

    def __init__(self, tenant_id: str) -> None:
        self._tenant_id = tenant_id
        self._session_cm: Any = None
        self._session: Session | None = None

    @property
    def session(self) -> Session | None:
        """Deprecated — use repository methods instead of raw session access.

        This property exists for backward compatibility during the migration.
        It will be removed once all callers use repository methods.
        """
        warnings.warn(
            "uow.session is deprecated — use repository methods instead of raw session access.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._session

    @session.setter
    def session(self, value: Session | None) -> None:
        """Deprecated setter — only used by tests that mock uow.session."""
        self._session = value

    def __enter__(self) -> Self:
        self._session_cm = get_db_session()
        self._session = self._session_cm.__enter__()
        self._init_repos()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._session is not None
        assert self._session_cm is not None
        try:
            if exc_type is None:
                self._session.commit()
        finally:
            # Always close the session CM and clear references, even if
            # commit() raises.  Without this, the get_db_session() generator
            # is left suspended, leaking the session and DB connection.
            self._session_cm.__exit__(exc_type, exc_val, exc_tb)
            self._session = None
            self._clear_repos()

    def _init_repos(self) -> None:
        raise NotImplementedError

    def _clear_repos(self) -> None:
        raise NotImplementedError


class MediaBuyUoW(BaseUoW):
    """Unit of Work for MediaBuy operations.

    Wraps a database session and provides tenant-scoped repositories for
    media buys, products (read-side; create_media_buy resolves product_map
    via this), and currency limits.
    Auto-commits on clean exit, rolls back on exception.

    Args:
        tenant_id: Tenant scope for all repository queries.
    """

    media_buys: RepositoryAccessor[MediaBuyRepository] = RepositoryAccessor()
    products: RepositoryAccessor[ProductRepository] = RepositoryAccessor()
    creatives: RepositoryAccessor[CreativeRepository] = RepositoryAccessor()
    currency_limits: RepositoryAccessor[CurrencyLimitRepository] = RepositoryAccessor()
    idempotency_attempts: RepositoryAccessor[IdempotencyAttemptRepository] = RepositoryAccessor()

    def _init_repos(self) -> None:
        assert self._session is not None
        self.media_buys = MediaBuyRepository(self._session, self._tenant_id)
        self.products = ProductRepository(self._session, self._tenant_id)
        self.creatives = CreativeRepository(self._session, self._tenant_id)
        self.currency_limits = CurrencyLimitRepository(self._session, self._tenant_id)
        self.idempotency_attempts = IdempotencyAttemptRepository(self._session, self._tenant_id)

    def _clear_repos(self) -> None:
        self.media_buys = None
        self.products = None
        self.creatives = None
        self.currency_limits = None
        self.idempotency_attempts = None


class ProductUoW(BaseUoW):
    """Unit of Work for Product operations.

    Wraps a database session and provides a tenant-scoped ProductRepository.
    Auto-commits on clean exit, rolls back on exception.

    Args:
        tenant_id: Tenant scope for all repository queries.
    """

    products: RepositoryAccessor[ProductRepository] = RepositoryAccessor()

    def _init_repos(self) -> None:
        assert self._session is not None
        self.products = ProductRepository(self._session, self._tenant_id)

    def _clear_repos(self) -> None:
        self.products = None


class WorkflowUoW(BaseUoW):
    """Unit of Work for Workflow operations.

    Wraps a database session and provides a tenant-scoped WorkflowRepository.
    Auto-commits on clean exit, rolls back on exception.

    Args:
        tenant_id: Tenant scope for all repository queries.
    """

    workflows: RepositoryAccessor[WorkflowRepository] = RepositoryAccessor()

    def _init_repos(self) -> None:
        assert self._session is not None
        self.workflows = WorkflowRepository(self._session, self._tenant_id)

    def _clear_repos(self) -> None:
        self.workflows = None


class TenantConfigUoW(BaseUoW):
    """Unit of Work for tenant configuration reads.

    Wraps a database session and provides a tenant-scoped TenantConfigRepository.
    Auto-commits on clean exit, rolls back on exception.

    Args:
        tenant_id: Tenant scope for all repository queries.
    """

    # Non-optional to callers — see RepositoryAccessor.
    tenant_config: RepositoryAccessor[TenantConfigRepository] = RepositoryAccessor()

    def _init_repos(self) -> None:
        assert self._session is not None
        self.tenant_config = TenantConfigRepository(self._session, self._tenant_id)

    def _clear_repos(self) -> None:
        self.tenant_config = None


class AccountUoW(BaseUoW):
    """Unit of Work for Account operations.

    Wraps a database session and provides a tenant-scoped AccountRepository.
    Auto-commits on clean exit, rolls back on exception.

    Args:
        tenant_id: Tenant scope for all repository queries.

    """

    accounts: RepositoryAccessor[AccountRepository] = RepositoryAccessor()

    def _init_repos(self) -> None:
        assert self._session is not None
        self.accounts = AccountRepository(self._session, self._tenant_id)

    def _clear_repos(self) -> None:
        self.accounts = None


class PushNotificationConfigUoW(BaseUoW):
    """Unit of Work for PushNotificationConfig operations.

    Wraps a database session and provides a tenant-scoped
    ``PushNotificationConfigRepository``. Auto-commits on clean exit,
    rolls back on exception.

    Args:
        tenant_id: Tenant scope for all repository queries.
    """

    push_notification_configs: RepositoryAccessor[PushNotificationConfigRepository] = RepositoryAccessor()

    def _init_repos(self) -> None:
        assert self._session is not None
        self.push_notification_configs = PushNotificationConfigRepository(self._session, self._tenant_id)

    def _clear_repos(self) -> None:
        self.push_notification_configs = None


class CreativeUoW(BaseUoW):
    """Unit of Work for Creative operations.

    Wraps a database session and provides a tenant-scoped CreativeRepository.
    Auto-commits on clean exit, rolls back on exception.

    Args:
        tenant_id: Tenant scope for all repository queries.
    """

    creatives: RepositoryAccessor[CreativeRepository] = RepositoryAccessor()
    assignments: RepositoryAccessor[CreativeAssignmentRepository] = RepositoryAccessor()
    # Assigning a creative can move its media buy out of draft, and a media-buy
    # status change carries the revision bump and the confirmed_at stamp — both
    # owned by MediaBuyRepository. The UoW already reaches the entity
    # (find_package_with_media_buy returns it), so it needs the repository that
    # may legally write it rather than a bare attribute assignment.
    media_buys: RepositoryAccessor[MediaBuyRepository] = RepositoryAccessor()

    def _init_repos(self) -> None:
        assert self._session is not None
        self.creatives = CreativeRepository(self._session, self._tenant_id)
        self.assignments = CreativeAssignmentRepository(self._session, self._tenant_id)
        self.media_buys = MediaBuyRepository(self._session, self._tenant_id)

    def _clear_repos(self) -> None:
        self.creatives = None
        self.assignments = None
        self.media_buys = None


class AdminCreativeUoW(BaseUoW):
    """Unit of Work for admin creative operations.

    Provides CreativeRepository, CreativeAssignmentRepository, MediaBuyRepository,
    ProductRepository, WorkflowRepository, and TenantConfigRepository in a single
    session scope. Used by admin blueprint handlers that need cross-entity queries
    (e.g. creative + assignments + media buys + tenant config).

    Auto-commits on clean exit, rolls back on exception.

    Args:
        tenant_id: Tenant scope for all repository queries.

    """

    creatives: RepositoryAccessor[CreativeRepository] = RepositoryAccessor()
    assignments: RepositoryAccessor[CreativeAssignmentRepository] = RepositoryAccessor()
    media_buys: RepositoryAccessor[MediaBuyRepository] = RepositoryAccessor()
    products: RepositoryAccessor[ProductRepository] = RepositoryAccessor()
    workflows: RepositoryAccessor[WorkflowRepository] = RepositoryAccessor()
    tenant_config: RepositoryAccessor[TenantConfigRepository] = RepositoryAccessor()

    def _init_repos(self) -> None:
        assert self._session is not None
        self.creatives = CreativeRepository(self._session, self._tenant_id)
        self.assignments = CreativeAssignmentRepository(self._session, self._tenant_id)
        self.media_buys = MediaBuyRepository(self._session, self._tenant_id)
        self.products = ProductRepository(self._session, self._tenant_id)
        self.workflows = WorkflowRepository(self._session, self._tenant_id)
        self.tenant_config = TenantConfigRepository(self._session, self._tenant_id)

    def _clear_repos(self) -> None:
        self.creatives = None
        self.assignments = None
        self.media_buys = None
        self.products = None
        self.workflows = None
        self.tenant_config = None


class TMPProviderUoW(BaseUoW):
    """Unit of Work for TMP Provider operations.

    Wraps a database session and provides a tenant-scoped TMPProviderRepository
    and TenantConfigRepository.  The tenant_config repo is included so that
    admin blueprint handlers can resolve the Tenant row without a raw
    ``select(Tenant)`` — matching the pattern used by the discovery route.

    Auto-commits on clean exit, rolls back on exception.

    Args:
        tenant_id: Tenant scope for all repository queries.

    beads: salesagent-tmp-sync
    """

    # Non-optional to callers: see RepositoryAccessor. Reading either outside an
    # open session raises AdCPServiceUnavailableError instead of handing back a
    # None that every call site has to narrow for itself (#1197 review).
    tmp_providers: RepositoryAccessor[TMPProviderRepository] = RepositoryAccessor()
    tenant_config: RepositoryAccessor[TenantConfigRepository] = RepositoryAccessor()

    def _init_repos(self) -> None:
        assert self._session is not None
        self.tmp_providers = TMPProviderRepository(self._session, self._tenant_id)
        self.tenant_config = TenantConfigRepository(self._session, self._tenant_id)

    def _clear_repos(self) -> None:
        self.tmp_providers = None
        self.tenant_config = None
