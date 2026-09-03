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
from typing import TYPE_CHECKING, Any, Self

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
from src.core.database.repositories.workflow import WorkflowRepository

if TYPE_CHECKING:
    from src.core.database.models import MediaBuy

logger = logging.getLogger(__name__)


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


class BuyKeyedSandboxMixin:
    """The single seam for buy-keyed sandbox decisions.

    Operations addressed only by ``media_buy_id`` — update, performance, deferred
    creative push, the approval executor, the admin detail route — carry no account
    reference, so ``identity.sandbox`` is structurally False for them and the account
    owning the buy is the only correct source.

    All five now share ONE derivation, but they do not all reach it the same way, and
    the difference matters to anyone following an error message here:

    - Four hold a UoW and use this mixin: update and the approval executor via
      :meth:`sandbox_mode` (the row is already loaded), performance and deferred
      creative push via :meth:`sandbox_mode_by_id`. Creative push holds an
      ``AdminCreativeUoW``, not a ``MediaBuyUoW`` — which is why this is a mixin over
      "any UoW owning both repositories" rather than a method on one class.
    - The admin detail route holds a raw Flask session and no UoW, so it calls
      ``account_helpers.sandbox_mode_for_buy`` directly. Sending it through a UoW would
      close the request's own session underneath it (scoped_session with
      expire_on_commit) and 500 the page.

    This mixin therefore owns the derivation for UoW holders and delegates the decision
    itself to that shared function, so the two entry points cannot drift apart.

    Mixed into every UoW that provides both repositories; the annotations below are
    the contract those UoWs must satisfy.
    """

    media_buys: MediaBuyRepository | None
    accounts: AccountRepository | None

    def sandbox_mode(self, media_buy: MediaBuy | None) -> bool:
        """Sandbox mode of the account owning ``media_buy``.

        A ``None`` buy yields False (live); the caller's own not-found handling owns
        that case. A buy whose non-null account cannot be resolved raises
        ``AdCPAccountNotFoundError`` rather than silently defaulting to live.
        """
        assert self.accounts is not None
        # Function-local: importing from the src.core.helpers PACKAGE runs its __init__,
        # which eagerly pulls the full adapter graph (adapter_helpers -> src.adapters).
        # A module-level import here closes a load cycle once a service in the adapter
        # graph reaches this repository (webhook_delivery_service -> webhook_conclusion
        # -> repositories.delivery, added in #1802). Resolving at call time defers that
        # until the graph is fully loaded; the source-module patch strategy still works.
        from src.core.helpers.account_helpers import sandbox_mode_for_buy

        return sandbox_mode_for_buy(self.accounts, media_buy)

    def sandbox_mode_by_id(self, media_buy_id: str) -> bool:
        """Sandbox mode of the account owning the buy with ``media_buy_id``.

        Convenience over :meth:`sandbox_mode` for callers holding only an id; prefer
        :meth:`sandbox_mode` when the row is already loaded, to avoid a second lookup.

        Raises ``AdCPMediaBuyNotFoundError`` for an id that resolves to no row, rather
        than returning False. False here means LIVE — "dispatch this to the tenant's
        real ad server" — decided about a buy that could not be found, which is the
        fail-OPEN shape ``_account_is_sandbox`` refuses one call below for a
        non-resolvable account. Two not-found policies a function apart is how they
        drift. Callers were safe only by ordering (media_buy_create built the adapter
        at :1349 before checking the buy existed at :1359, and was saved by a later
        join returning None); safety supplied by call order rather than by the seam
        that owns the decision is exactly what this seam was created to end.

        The ``None``-buy → live allowance stays on :meth:`sandbox_mode`, where the
        caller has already decided the row's absence is acceptable.
        """
        assert self.media_buys is not None
        return self.sandbox_mode(self.media_buys.get_by_id_or_raise(media_buy_id))


class MediaBuyUoW(BuyKeyedSandboxMixin, BaseUoW):
    """Unit of Work for MediaBuy operations.

    Wraps a database session and provides tenant-scoped repositories for
    media buys, products (read-side; create_media_buy resolves product_map
    via this), and currency limits.
    Auto-commits on clean exit, rolls back on exception.

    Args:
        tenant_id: Tenant scope for all repository queries.
    """

    media_buys: MediaBuyRepository | None
    accounts: AccountRepository | None
    products: ProductRepository | None
    creatives: CreativeRepository | None
    currency_limits: CurrencyLimitRepository | None
    idempotency_attempts: IdempotencyAttemptRepository | None

    def _init_repos(self) -> None:
        assert self._session is not None
        self.media_buys = MediaBuyRepository(self._session, self._tenant_id)
        # Buy-keyed operations derive sandbox mode from the owning account.
        self.accounts = AccountRepository(self._session, self._tenant_id)
        self.products = ProductRepository(self._session, self._tenant_id)
        self.creatives = CreativeRepository(self._session, self._tenant_id)
        self.currency_limits = CurrencyLimitRepository(self._session, self._tenant_id)
        self.idempotency_attempts = IdempotencyAttemptRepository(self._session, self._tenant_id)

    def _clear_repos(self) -> None:
        self.media_buys = None
        self.accounts = None
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

    products: ProductRepository | None

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

    workflows: WorkflowRepository | None

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

    tenant_config: TenantConfigRepository | None

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

    accounts: AccountRepository | None

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

    push_notification_configs: PushNotificationConfigRepository | None

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

    creatives: CreativeRepository | None
    assignments: CreativeAssignmentRepository | None
    # Assigning a creative can move its media buy out of draft, and a media-buy
    # status change carries the revision bump and the confirmed_at stamp — both
    # owned by MediaBuyRepository. The UoW already reaches the entity
    # (find_package_with_media_buy returns it), so it needs the repository that
    # may legally write it rather than a bare attribute assignment.
    media_buys: MediaBuyRepository | None

    def _init_repos(self) -> None:
        assert self._session is not None
        self.creatives = CreativeRepository(self._session, self._tenant_id)
        self.assignments = CreativeAssignmentRepository(self._session, self._tenant_id)
        self.media_buys = MediaBuyRepository(self._session, self._tenant_id)

    def _clear_repos(self) -> None:
        self.creatives = None
        self.assignments = None
        self.media_buys = None


class AdminCreativeUoW(BuyKeyedSandboxMixin, BaseUoW):
    """Unit of Work for admin creative operations.

    Provides CreativeRepository, CreativeAssignmentRepository, MediaBuyRepository,
    ProductRepository, WorkflowRepository, and TenantConfigRepository in a single
    session scope. Used by admin blueprint handlers that need cross-entity queries
    (e.g. creative + assignments + media buys + tenant config).

    Auto-commits on clean exit, rolls back on exception.

    Args:
        tenant_id: Tenant scope for all repository queries.

    """

    creatives: CreativeRepository | None
    assignments: CreativeAssignmentRepository | None
    media_buys: MediaBuyRepository | None
    products: ProductRepository | None
    workflows: WorkflowRepository | None
    tenant_config: TenantConfigRepository | None
    accounts: AccountRepository | None

    def _init_repos(self) -> None:
        assert self._session is not None
        self.creatives = CreativeRepository(self._session, self._tenant_id)
        self.assignments = CreativeAssignmentRepository(self._session, self._tenant_id)
        self.media_buys = MediaBuyRepository(self._session, self._tenant_id)
        self.products = ProductRepository(self._session, self._tenant_id)
        self.workflows = WorkflowRepository(self._session, self._tenant_id)
        self.tenant_config = TenantConfigRepository(self._session, self._tenant_id)
        # Sandbox mode of the owning account gates adapter dispatch on the deferred
        # creative-push path; without it that path cannot tell a sandbox buy from a live one.
        self.accounts = AccountRepository(self._session, self._tenant_id)

    def _clear_repos(self) -> None:
        self.creatives = None
        self.assignments = None
        self.media_buys = None
        self.products = None
        self.workflows = None
        self.tenant_config = None
        self.accounts = None
