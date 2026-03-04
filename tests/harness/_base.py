"""Base test environment for _impl function testing.

Unified base for both integration and unit test environments:

- **Integration mode** (``use_real_db = True``): Creates a non-scoped SQLAlchemy
  session, binds factory_boy factories, only mocks external services.
  Requires ``integration_db`` pytest fixture.
- **Unit mode** (``use_real_db = False``): No database setup, patches all
  dependencies including DB.

Subclasses override:
    EXTERNAL_PATCHES: dict[str, str]   -- {name: patch_target} for mocks
    _configure_mocks(): None           -- wire mock defaults
    call_impl(**kwargs): Any           -- call production function
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from src.core.resolved_identity import ResolvedIdentity


class BaseTestEnv:
    """Base test environment for _impl function testing.

    Subclasses define:
        EXTERNAL_PATCHES: dict[str, str]   -- {name: patch_target}
        _configure_mocks(): None           -- wire mock defaults
        call_impl(**kwargs): Any           -- call production function

    Set ``use_real_db = True`` in integration subclasses to enable
    factory_boy session binding.

    Usage (integration)::

        @pytest.mark.requires_db
        def test_something(self, integration_db):
            with DeliveryPollEnv() as env:
                tenant = TenantFactory(tenant_id="t1")
                response = env.call_impl(media_buy_ids=["mb_001"])

    Usage (unit)::

        with DeliveryPollEnvUnit() as env:
            env.add_buy(media_buy_id="mb_001")
            response = env.call_impl(media_buy_ids=["mb_001"])

    Attributes:
        mock: dict[str, MagicMock]  -- active mocks keyed by short name
        identity: ResolvedIdentity  -- default identity (override via constructor)
    """

    EXTERNAL_PATCHES: dict[str, str] = {}
    MODULE: str = ""  # Convenience for unit envs building patch paths
    use_real_db: bool = False

    def __init__(
        self,
        principal_id: str = "test_principal",
        tenant_id: str = "test_tenant",
        dry_run: bool = False,
    ) -> None:
        self._principal_id = principal_id
        self._tenant_id = tenant_id
        self._dry_run = dry_run
        self.mock: dict[str, MagicMock] = {}
        self._patchers: list[Any] = []
        self._session: Session | None = None
        self._identity: ResolvedIdentity | None = None

    @property
    def identity(self) -> ResolvedIdentity:
        """ResolvedIdentity with sane defaults. Built lazily to avoid import at class level."""
        if self._identity is None:
            from src.core.resolved_identity import ResolvedIdentity
            from src.core.testing_hooks import AdCPTestContext

            self._identity = ResolvedIdentity(
                principal_id=self._principal_id,
                tenant_id=self._tenant_id,
                tenant={"tenant_id": self._tenant_id, "name": "Test Tenant"},
                protocol="mcp",
                testing_context=AdCPTestContext(
                    dry_run=self._dry_run,
                    mock_time=None,
                    jump_to_event=None,
                    test_session_id=None,
                ),
            )
        return self._identity

    def _configure_mocks(self) -> None:
        """Wire up happy-path return values on self.mock entries.

        Called automatically after all patches are started.
        Override in subclass.
        """

    def call_impl(self, **kwargs: Any) -> Any:
        """Call the production function under test.

        Override in subclass. Should construct the request object
        and call the _impl function.
        """
        raise NotImplementedError

    def _commit_factory_data(self) -> None:
        """Ensure all factory-created data is committed before production code reads it.

        Safety net: factories use ``sqlalchemy_session_persistence = "commit"``
        which auto-commits each creation. This explicit commit guards against
        any edge cases where data might not be visible to production code's
        separate session.
        """
        if self._session:
            self._session.commit()

    # -- Context manager protocol ------------------------------------------

    def __enter__(self) -> Self:
        # 1. Database setup (integration mode only)
        if self.use_real_db:
            from sqlalchemy.orm import Session as SASession

            from src.core.database.database_session import get_engine
            from tests.factories import ALL_FACTORIES

            # Guard against nested envs — session binding is global
            for f in ALL_FACTORIES:
                assert f._meta.sqlalchemy_session is None, (
                    f"Factory {getattr(f, '__name__', type(f).__name__)} session already bound — "
                    "nested IntegrationEnv contexts are not supported"
                )

            engine = get_engine()
            self._session = SASession(bind=engine)

            for f in ALL_FACTORIES:
                f._meta.sqlalchemy_session = self._session

        # 2. Start patches
        for name, target in self.EXTERNAL_PATCHES.items():
            patcher = patch(target)
            self.mock[name] = patcher.start()
            self._patchers.append(patcher)

        self._configure_mocks()
        return self

    def __exit__(self, *exc: object) -> bool:
        errors: list[Exception] = []

        # 1. Unbind factories (integration mode only)
        if self.use_real_db:
            try:
                from tests.factories import ALL_FACTORIES

                for f in ALL_FACTORIES:
                    f._meta.sqlalchemy_session = None
            except Exception as e:
                errors.append(e)

            try:
                if self._session:
                    self._session.close()
                    self._session = None
            except Exception as e:
                errors.append(e)

        # 2. Stop patches — each in its own try block
        for patcher in reversed(self._patchers):
            try:
                patcher.stop()
            except Exception as e:
                errors.append(e)
        self._patchers.clear()
        self.mock.clear()

        if errors:
            raise errors[0]
        return False


class IntegrationEnv(BaseTestEnv):
    """Integration test environment — real database, only mocks external services.

    Requires ``integration_db`` pytest fixture.
    """

    use_real_db = True
