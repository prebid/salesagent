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
from collections.abc import Mapping
from types import TracebackType
from typing import Any, ClassVar, Self

from sqlalchemy.orm import Session

from src.core.database.database_session import get_db_session
from src.core.database.repositories.account import AccountRepository
from src.core.database.repositories.authorized_property import AuthorizedPropertyRepository
from src.core.database.repositories.creative import CreativeAssignmentRepository, CreativeRepository
from src.core.database.repositories.currency_limit import CurrencyLimitRepository
from src.core.database.repositories.effects import begin_effects, drain_after_commit, end_effects
from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository
from src.core.database.repositories.media_buy import MediaBuyRepository
from src.core.database.repositories.product import ProductRepository
from src.core.database.repositories.push_notification_config import PushNotificationConfigRepository
from src.core.database.repositories.signing_key import SigningKeyRepository
from src.core.database.repositories.tenant_config import TenantConfigRepository
from src.core.database.repositories.workflow import WorkflowRepository

logger = logging.getLogger(__name__)


class BaseUoW:
    """Base Unit of Work — handles session lifecycle.

    Subclasses implement ``_init_repos()`` to create tenant-scoped repositories
    and ``_clear_repos()`` to reset them on exit.

    Auto-commits on clean exit, rolls back on exception.

    The session is private (``_session``). Business logic should use
    repository methods, not raw session access.

    ``dry_run`` makes the whole unit a PREVIEW: the identical write path runs
    and every read inside the block sees its own uncommitted writes (they are
    flushed in-session), but the transaction is ROLLED BACK instead of
    committed on clean exit. Preview/live parity therefore holds by
    construction -- there is no second "simulated" write path to keep in sync,
    which is the class of bug a shadow preview state machine reintroduces
    every time it drifts from the real one.

    Rolling back disposes of the TRANSACTION only -- an outbound HTTP call, a
    job handed to a background executor, or a write through a different unit of
    work is not undone by it. Those effects are therefore routed through this
    same boundary rather than gated at their call sites (see
    ``repositories/effects.py``): register a deferrable one with
    ``repo.after_commit(fn)`` and it runs only if this transaction commits;
    wrap an effect whose RESULT you need with ``repo.outbound(call)`` and it is
    suppressed for a preview. A call site inside the transaction should never
    need to ask whether it is a preview.

    Args:
        tenant_id: Tenant scope for all repository queries.
        dry_run: Roll back on clean exit instead of committing.
    """

    def __init__(self, tenant_id: str, dry_run: bool = False) -> None:
        self._tenant_id = tenant_id
        self._dry_run = dry_run
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
        begin_effects(self._session, preview=self._dry_run)
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
        session = self._session
        committed = False
        try:
            if exc_type is None:
                # dry_run is a preview: discard the identical write path's work
                # rather than skipping it, so the results the caller already
                # built describe exactly what a live run would have persisted.
                if self._dry_run:
                    session.rollback()
                else:
                    session.commit()
                    committed = True
        finally:
            # Always close the session CM and clear references, even if
            # commit() raises.  Without this, the get_db_session() generator
            # is left suspended, leaking the session and DB connection.
            self._session_cm.__exit__(exc_type, exc_val, exc_tb)
            self._session = None
            self._clear_repos()

        # Deferred effects run HERE -- after the session is closed, outside the
        # finally. Every one of them opens its own unit of work, and the session
        # is scoped: draining while this one was still open would let an inner
        # unit close and de-register the session out from under this exit.
        # Only a commit releases them; a rollback (preview or exception) drops
        # the queue, which is what lets their call sites stop asking about dry_run.
        try:
            if committed:
                drain_after_commit(session)
        finally:
            end_effects(session)

    #: ``attribute name -> repository class`` for subclasses that declare their
    #: repositories instead of hand-writing the two hooks below. Every repository in this
    #: layer takes ``(session, tenant_id)``, so the construction is the SAME line with a
    #: different class — which is exactly the shape that should be data, not code.
    #:
    #: Subclasses may still override ``_init_repos``/``_clear_repos`` directly; the eight
    #: pre-#1757 units of work do, and they keep working unchanged. New ones declare.
    _REPOSITORIES: ClassVar[Mapping[str, type]] = {}

    def _init_repos(self) -> None:
        """Construct every declared repository on the open session.

        Raises when a subclass declares nothing AND does not override, so "forgot to
        wire the repositories" stays a loud failure rather than a unit of work whose
        attributes are all ``None`` at the first use.
        """
        if not self._REPOSITORIES:
            raise NotImplementedError(
                f"{type(self).__name__} must declare _REPOSITORIES or override _init_repos/_clear_repos"
            )
        assert self._session is not None
        for attribute, repository in self._REPOSITORIES.items():
            setattr(self, attribute, repository(self._session, self._tenant_id))

    def _clear_repos(self) -> None:
        """Drop every declared repository, so a closed unit of work holds no session.

        The mirror of :meth:`_init_repos`, and derived from the SAME declaration — the
        two hand-written halves could previously disagree, leaving a repository (and its
        session) attached after ``__exit__``.
        """
        for attribute in self._REPOSITORIES:
            setattr(self, attribute, None)


class MediaBuyUoW(BaseUoW):
    """Unit of Work for MediaBuy operations.

    Wraps a database session and provides tenant-scoped repositories for
    media buys, products (read-side; create_media_buy resolves product_map
    via this), creative assignments, and currency limits.
    Auto-commits on clean exit, rolls back on exception.

    Args:
        tenant_id: Tenant scope for all repository queries.
    """

    media_buys: MediaBuyRepository | None
    products: ProductRepository | None
    creatives: CreativeRepository | None
    # create_media_buy and update_media_buy both write a package's creative
    # assignments in the same transaction as the buy itself. They used to do it
    # through the raw session with the model imported as ``DBAssignment`` — an
    # alias the raw-select guard cannot resolve, so four such queries were
    # invisible to it and to its allowlist (salesagent-3cs7o.27).
    assignments: CreativeAssignmentRepository | None
    currency_limits: CurrencyLimitRepository | None
    idempotency_attempts: IdempotencyAttemptRepository | None

    def _init_repos(self) -> None:
        assert self._session is not None
        self.media_buys = MediaBuyRepository(self._session, self._tenant_id)
        self.products = ProductRepository(self._session, self._tenant_id)
        self.creatives = CreativeRepository(self._session, self._tenant_id)
        self.assignments = CreativeAssignmentRepository(self._session, self._tenant_id)
        self.currency_limits = CurrencyLimitRepository(self._session, self._tenant_id)
        self.idempotency_attempts = IdempotencyAttemptRepository(self._session, self._tenant_id)

    def _clear_repos(self) -> None:
        self.media_buys = None
        self.products = None
        self.creatives = None
        self.assignments = None
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
    workflows: WorkflowRepository | None
    # The account the sync request names (required by the pin) decides whether the
    # response is flagged as sandbox; read through the same transaction.
    accounts: AccountRepository | None

    def _init_repos(self) -> None:
        assert self._session is not None
        self.creatives = CreativeRepository(self._session, self._tenant_id)
        self.assignments = CreativeAssignmentRepository(self._session, self._tenant_id)
        self.media_buys = MediaBuyRepository(self._session, self._tenant_id)
        self.accounts = AccountRepository(self._session, self._tenant_id)
        # Approval workflow steps are written by the same request that writes
        # the creatives they approve, so they must join the same transaction:
        # a preview's rollback has to discard them too, and the approval
        # notification (an after_commit effect) must not be able to name a
        # step the commit has not yet released (#2002).
        self.workflows = WorkflowRepository(self._session, self._tenant_id)

    def _clear_repos(self) -> None:
        self.creatives = None
        self.assignments = None
        self.media_buys = None
        self.workflows = None
        self.accounts = None


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

    creatives: CreativeRepository | None
    assignments: CreativeAssignmentRepository | None
    media_buys: MediaBuyRepository | None
    products: ProductRepository | None
    workflows: WorkflowRepository | None
    tenant_config: TenantConfigRepository | None

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


class SigningKeyUoW(BaseUoW):
    """Unit of Work for this agent's own signing keys (#1291 A2).

    Wraps a database session and provides a tenant-scoped
    ``SigningKeyRepository``. Auto-commits on clean exit, rolls back on
    exception.

    Args:
        tenant_id: Tenant scope for all repository queries.

    beads: salesagent-z6nr.8
    """

    signing_keys: SigningKeyRepository | None

    _REPOSITORIES: ClassVar[Mapping[str, type]] = {"signing_keys": SigningKeyRepository}


class CapabilitiesUoW(BaseUoW):
    """Unit of Work for one ``get_adcp_capabilities`` request (#1291 D1).

    ONE session for everything the capabilities response reads from the database: the
    tenant's publisher partners, its signing keys, and the ``tenants`` row itself —
    whose stored host IS the agent identity the ``identity`` block points at.

    One session because the ORM ``Tenant`` must stay attached while the identity URLs
    are derived from it: no session in this codebase sets ``expire_on_commit=False``, so
    handing the row out to the response-construction site would raise
    ``DetachedInstanceError`` on the first attribute read.

    Deliberately NOT :class:`TrustRootUoW`, which carries the same two repositories plus
    ``authorized_properties``: its name asserts a different purpose, and its third
    repository is dead weight on a per-request read path.

    Added for #1291 D1 (the declarable signing family).
    """

    tenant_config: TenantConfigRepository | None
    signing_keys: SigningKeyRepository | None

    _REPOSITORIES: ClassVar[Mapping[str, type]] = {
        "tenant_config": TenantConfigRepository,
        "signing_keys": SigningKeyRepository,
    }


class TrustRootUoW(BaseUoW):
    """Unit of Work for the trust-root documents this agent publishes (#1291 A3).

    One session for all three reads a trust-root request performs — the tenant
    (whose stored host IS the agent identity), the publishable key set, and the
    authorized-property records that back an adagents claim. One session because
    the JWKS and the adagents pin must describe the same key set: two sessions
    could observe a rotation from either side of it.

    Args:
        tenant_id: Tenant scope for all repository queries.

    beads: salesagent-z6nr.9
    """

    tenant_config: TenantConfigRepository | None
    signing_keys: SigningKeyRepository | None
    authorized_properties: AuthorizedPropertyRepository | None

    _REPOSITORIES: ClassVar[Mapping[str, type]] = {
        "tenant_config": TenantConfigRepository,
        "signing_keys": SigningKeyRepository,
        "authorized_properties": AuthorizedPropertyRepository,
    }
