"""Base integration test environment for _impl function testing.

Uses real PostgreSQL database (requires ``integration_db`` fixture).
Only mocks truly external services (adapters, HTTP calls).

Subclasses override:
    EXTERNAL_PATCHES: dict[str, str]   -- {name: patch_target} for external-only mocks
    _configure_mocks(): None           -- wire mock defaults
    call_impl(**kwargs): Any           -- call production function

Design: factory_boy factories are bound to a non-scoped session during
__enter__ and unbound during __exit__. Production code uses its own
sessions via get_db_session() — both see the same PostgreSQL test DB
because integration_db resets the engine globals.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, _patch, patch


class IntegrationEnv:
    """Base integration test environment.

    Uses real database (requires integration_db fixture).
    Only mocks truly external services.

    Subclasses define:
        EXTERNAL_PATCHES: dict[str, str]   -- {name: patch_target}
        _configure_mocks(): None           -- wire mock defaults
        call_impl(**kwargs): Any           -- call production function

    Usage::

        @pytest.mark.requires_db
        def test_something(self, integration_db):
            with DeliveryPollEnv() as env:
                tenant = TenantFactory(tenant_id="t1")
                ...
                response = env.call_impl(media_buy_ids=["mb_001"])
                assert response.field == expected
    """

    EXTERNAL_PATCHES: dict[str, str] = {}

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
        self._patchers: list[_patch[Any]] = []
        self._session: Any = None
        self._identity: Any = None

    @property
    def identity(self) -> Any:
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
        """Ensure all factory-created data is committed before production code reads it."""
        if self._session:
            self._session.commit()

    # -- Context manager protocol ------------------------------------------

    def __enter__(self) -> IntegrationEnv:
        # 1. Create non-scoped session for factory_boy (avoids conflicts
        #    with production code's scoped_session via get_db_session())
        from sqlalchemy.orm import Session as SASession

        from src.core.database.database_session import get_engine

        engine = get_engine()
        self._session = SASession(bind=engine)

        # 2. Bind all factory_boy factories to this session
        from tests.factories import ALL_FACTORIES

        for f in ALL_FACTORIES:
            f._meta.sqlalchemy_session = self._session

        # 3. Start external-only patches
        for name, target in self.EXTERNAL_PATCHES.items():
            patcher = patch(target)
            self.mock[name] = patcher.start()
            self._patchers.append(patcher)

        self._configure_mocks()
        return self

    def __exit__(self, *exc: object) -> bool:
        # Unbind factories
        from tests.factories import ALL_FACTORIES

        for f in ALL_FACTORIES:
            f._meta.sqlalchemy_session = None

        # Close factory session
        if self._session:
            self._session.close()
            self._session = None

        # Stop patches
        for patcher in reversed(self._patchers):
            patcher.stop()
        self._patchers.clear()
        self.mock.clear()
        return False
