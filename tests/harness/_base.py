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

Multi-transport support (subclasses may also override):
    call_a2a(**kwargs): Any            -- call _raw() A2A wrapper
    REST_ENDPOINT: str                 -- POST endpoint path for REST dispatch
    build_rest_body(**kwargs): dict    -- convert kwargs to REST body
    parse_rest_response(data): model  -- parse JSON dict to Pydantic model
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from pydantic import BaseModel
    from sqlalchemy.orm import Session

    from src.core.resolved_identity import ResolvedIdentity
    from tests.harness.transport import Transport, TransportResult


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

    Usage (multi-transport)::

        @pytest.mark.parametrize("transport", [Transport.IMPL, Transport.A2A, Transport.REST])
        def test_something(self, integration_db, transport):
            with CreativeSyncEnv() as env:
                result = env.call_via(transport, creatives=[...])
                assert result.is_success

    Attributes:
        mock: dict[str, MagicMock]  -- active mocks keyed by short name
        identity: ResolvedIdentity  -- default identity (override via constructor)
    """

    EXTERNAL_PATCHES: dict[str, str] = {}
    ASYNC_PATCHES: set[str] = set()  # Names that need AsyncMock (for async functions)
    MODULE: str = ""  # Convenience for unit envs building patch paths
    REST_ENDPOINT: str = ""  # Override in subclass for REST dispatch
    use_real_db: bool = False

    def __init__(
        self,
        principal_id: str = "test_principal",
        tenant_id: str = "test_tenant",
        dry_run: bool = False,
        **tenant_overrides: Any,
    ) -> None:
        self._principal_id = principal_id
        self._tenant_id = tenant_id
        self._dry_run = dry_run
        self._tenant_overrides = tenant_overrides
        self.mock: dict[str, MagicMock] = {}
        self._patchers: list[Any] = []
        self._session: Session | None = None
        self._identity_cache: dict[str, ResolvedIdentity] = {}
        self._rest_client: Any = None  # Lazy-created TestClient

    # -- Identity (one function, all transports) ----------------------------

    def identity_for(self, transport: Transport) -> ResolvedIdentity:
        """Build ResolvedIdentity with the correct protocol for *transport*.

        This is the single source of truth for test identity across all
        transports. The identity is cached per protocol so repeated calls
        with the same transport return the same object.
        """
        from tests.harness.transport import TRANSPORT_PROTOCOL

        protocol = TRANSPORT_PROTOCOL[transport]
        if protocol not in self._identity_cache:
            from tests.factories.principal import PrincipalFactory

            self._identity_cache[protocol] = PrincipalFactory.make_identity(
                principal_id=self._principal_id,
                tenant_id=self._tenant_id,
                protocol=protocol,
                dry_run=self._dry_run,
                **self._tenant_overrides,
            )
        return self._identity_cache[protocol]

    @property
    def identity(self) -> ResolvedIdentity:
        """Default identity (protocol='mcp'). Backward-compatible.

        Supports direct override via ``env._identity = ...`` for integration
        tests that create tenants in the DB and need LazyTenantContext.
        """
        # Backward compat: tests may set env._identity directly
        direct = self.__dict__.get("_identity")
        if direct is not None:
            return direct
        from tests.harness.transport import Transport

        return self.identity_for(Transport.IMPL)

    # -- Transport dispatch -------------------------------------------------

    def call_via(self, transport: Transport, **kwargs: Any) -> TransportResult:
        """Dispatch through *transport* and return normalized TransportResult.

        Injects the correct identity for the transport into kwargs (unless
        the caller explicitly provides one). Routes to the appropriate
        dispatcher.
        """
        from tests.harness.dispatchers import DISPATCHERS

        # Inject transport-correct identity
        kwargs.setdefault("identity", self.identity_for(transport))

        dispatcher = DISPATCHERS[transport]
        return dispatcher.dispatch(self, **kwargs)

    # -- Per-transport hooks (override in subclass) -------------------------

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

    def call_a2a(self, **kwargs: Any) -> Any:
        """Call the _raw() A2A wrapper function.

        Override in subclass. Should call the _raw() function with
        the same kwargs as call_impl but through the A2A wrapper.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement call_a2a(). Override to enable Transport.A2A dispatch."
        )

    def call_mcp(self, **kwargs: Any) -> Any:
        """Call the async MCP wrapper with a mock Context.

        Override in subclass. Should create a mock Context with
        get_state("identity") returning the MCP identity, call the
        async MCP wrapper, and extract the payload from ToolResult.structured_content.

        Note on enum coercion: FastMCP auto-coerces string values to enums
        when calling tools through the MCP protocol. When calling wrappers
        directly in tests, you must coerce enum parameters yourself before
        passing them. See CreativeSyncEnv.call_mcp for an example with
        ValidationMode.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement call_mcp(). Override to enable Transport.MCP dispatch."
        )

    def _run_mcp_wrapper(
        self,
        wrapper_fn: Any,
        response_cls: type,
        **kwargs: Any,
    ) -> Any:
        """Shared MCP dispatch: mock Context → async wrapper → parse response.

        Handles the boilerplate that every call_mcp() repeats:
        1. Create mock Context with get_state returning MCP identity
        2. Call the async wrapper via asyncio.run()
        3. Extract structured_content from ToolResult
        4. Parse into response_cls

        Subclass call_mcp() should do any pre-processing (enum coercion,
        kwarg popping) then delegate here.
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from fastmcp.server.context import Context

        from tests.harness.transport import Transport

        self._commit_factory_data()

        mock_ctx = MagicMock(spec=Context)
        mock_ctx.get_state = AsyncMock(return_value=self.identity_for(Transport.MCP))

        tool_result = asyncio.run(wrapper_fn(ctx=mock_ctx, **kwargs))
        return response_cls(**tool_result.structured_content)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Convert call_impl kwargs to the REST endpoint body shape.

        Override in subclass. Must match the Pydantic body model
        defined in src/routes/api_v1.py.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement build_rest_body(). Override to enable Transport.REST dispatch."
        )

    def parse_rest_response(self, data: dict[str, Any]) -> BaseModel:
        """Parse REST JSON response dict into the expected Pydantic model.

        Override in subclass.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement parse_rest_response(). "
            "Override to enable Transport.REST dispatch."
        )

    def parse_rest_error(self, status_code: int, data: dict[str, Any]) -> Exception:
        """Reconstruct an AdCPError from REST error response.

        Default implementation maps status_code to exception class.
        """
        from src.core.exceptions import (
            AdCPAdapterError,
            AdCPAuthenticationError,
            AdCPAuthorizationError,
            AdCPNotFoundError,
            AdCPRateLimitError,
            AdCPValidationError,
        )

        STATUS_TO_ERROR: dict[int, type[Exception]] = {
            400: AdCPValidationError,
            401: AdCPAuthenticationError,
            403: AdCPAuthorizationError,
            404: AdCPNotFoundError,
            429: AdCPRateLimitError,
            502: AdCPAdapterError,
        }
        error_cls = STATUS_TO_ERROR.get(status_code, Exception)
        message = data.get("message", data.get("error", str(data)))
        return error_cls(message)

    def get_rest_client(self) -> Any:
        """Return FastAPI TestClient with auth dependency overridden.

        Created lazily. Only available on IntegrationEnv subclasses.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement get_rest_client(). REST dispatch requires IntegrationEnv."
        )

    def _commit_factory_data(self) -> None:
        """Flush pending session state before calling production code.

        Factories use ``sqlalchemy_session_persistence = "commit"`` and auto-commit
        each model creation. This explicit commit ensures any cascading saves or
        deferred flushes are visible to production code's separate database session.
        Called automatically by call_impl() before each test execution.
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
            if name in self.ASYNC_PATCHES:
                patcher = patch(target, new_callable=AsyncMock)
            else:
                patcher = patch(target)
            self.mock[name] = patcher.start()
            self._patchers.append(patcher)

        self._configure_mocks()
        return self

    def __exit__(self, *exc: object) -> bool:
        errors: list[Exception] = []

        # 1. Clean up REST client
        if self._rest_client is not None:
            try:
                from src.app import app

                app.dependency_overrides.clear()
                self._rest_client = None
            except Exception as e:
                errors.append(e)

        # 2. Unbind factories (integration mode only)
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

        # 3. Stop patches — each in its own try block
        for patcher in reversed(self._patchers):
            try:
                patcher.stop()
            except Exception as e:
                errors.append(e)
        self._patchers.clear()
        self.mock.clear()
        self._identity_cache.clear()

        if errors:
            if len(errors) == 1:
                raise errors[0]
            raise ExceptionGroup("Multiple teardown errors", errors)
        return False


class IntegrationEnv(BaseTestEnv):
    """Integration test environment — real database, only mocks external services.

    Requires ``integration_db`` pytest fixture.
    Supports REST dispatch via FastAPI TestClient.
    """

    use_real_db = True

    def setup_default_data(self) -> tuple[Any, Any]:
        """Create default tenant + principal via factories.

        Must be called inside the ``with env:`` block (factories are bound
        to the session during ``__enter__``).

        Returns (tenant, principal) ORM instances. Uses self._tenant_id
        and self._principal_id from constructor.
        """
        from tests.factories import PrincipalFactory, TenantFactory

        tenant = TenantFactory(tenant_id=self._tenant_id)
        principal = PrincipalFactory(tenant=tenant, principal_id=self._principal_id)
        return tenant, principal

    def get_rest_client(self) -> Any:
        """Return FastAPI TestClient with auth overridden to return self.identity.

        The TestClient uses the same app instance as production. Auth
        dependencies are overridden to inject the test identity directly,
        bypassing real token resolution.
        """
        if self._rest_client is None:
            from starlette.testclient import TestClient

            from src.app import app
            from src.core.auth_context import _require_auth_dep, _resolve_auth_dep
            from tests.harness.transport import Transport

            rest_identity = self.identity_for(Transport.REST)
            app.dependency_overrides[_require_auth_dep] = lambda: rest_identity
            app.dependency_overrides[_resolve_auth_dep] = lambda: rest_identity
            self._rest_client = TestClient(app)

        return self._rest_client
