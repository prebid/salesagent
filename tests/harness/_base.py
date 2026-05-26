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

Multi-transport support: subclasses may override ``call_mcp(**kwargs)`` or
``call_a2a(**kwargs)`` to dispatch through the in-process protocol servers.
``Transport.IMPL`` calls ``call_impl`` directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from src.core.resolved_identity import ResolvedIdentity
    from tests.harness.transport import Transport, TransportResult


def _adcp_error_from_code(
    error_code: str,
    message: str,
    recovery: str | None = None,
    details: dict | None = None,
) -> Exception:
    """Reconstruct the exact AdCPError subclass from an error_code string.

    Shared by MCP and A2A unwrappers. Maps error codes like 'NOT_FOUND'
    to AdCPNotFoundError, 'VALIDATION_ERROR' to AdCPValidationError, etc.
    Falls back to base AdCPError for unknown codes.
    """
    from src.core.exceptions import (
        AdCPAccountAmbiguousError,
        AdCPAccountNotFoundError,
        AdCPAccountPaymentRequiredError,
        AdCPAccountSetupRequiredError,
        AdCPAccountSuspendedError,
        AdCPAdapterError,
        AdCPAuthenticationError,
        AdCPAuthorizationError,
        AdCPBudgetExhaustedError,
        AdCPConflictError,
        AdCPError,
        AdCPInvalidRequestError,
        AdCPNotFoundError,
        AdCPRateLimitError,
        AdCPServiceUnavailableError,
        AdCPValidationError,
    )

    _CODE_TO_CLASS: dict[str, type[AdCPError]] = {
        cls.error_code: cls
        for cls in (
            AdCPValidationError,
            AdCPInvalidRequestError,
            AdCPAuthenticationError,
            AdCPAuthorizationError,
            AdCPNotFoundError,
            AdCPAccountNotFoundError,
            AdCPAccountSetupRequiredError,
            AdCPAccountSuspendedError,
            AdCPAccountPaymentRequiredError,
            AdCPConflictError,
            AdCPAccountAmbiguousError,
            AdCPBudgetExhaustedError,
            AdCPRateLimitError,
            AdCPAdapterError,
            AdCPServiceUnavailableError,
        )
    }
    exc_cls = _CODE_TO_CLASS.get(error_code, AdCPError)
    reconstructed = exc_cls(
        message=message,
        details=details,
        recovery=recovery or "terminal",
    )
    if exc_cls is AdCPError:
        reconstructed.error_code = error_code
    return reconstructed


def _unwrap_mcp_tool_error(exc: Exception) -> Exception:
    """Translate FastMCP ToolError back to the corresponding AdCPError.

    The MCP tool wrappers (via with_error_logging) convert AdCPError to
    ToolError(error_code, message, recovery). When the error travels through
    the MCP Client, the structured args are serialized to a single string:
    ``"('VALIDATION_ERROR', 'message', 'correctable')"``.

    This parses the string back to a tuple via ast.literal_eval and
    reconstructs the AdCPError subclass.

    If the exception is not a ToolError or can't be parsed, returns it unchanged.
    """
    import ast

    import httpx
    from fastmcp.exceptions import ToolError

    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 401:
        message = "Authentication failed: a valid ADCP auth token is required."
        return _adcp_error_from_code(
            "AUTH_TOKEN_INVALID",
            message,
            recovery="correctable",
            details={"suggestion": message},
        )

    if not isinstance(exc, ToolError):
        return exc

    # ToolError from Client has a single string arg containing the repr'd tuple.
    error_str = str(exc)

    # Try to parse as a Python tuple: ('CODE', 'message', 'recovery', '{"details": ...}')
    try:
        parsed = ast.literal_eval(error_str)
        if isinstance(parsed, tuple) and len(parsed) >= 2:
            error_code = str(parsed[0])
            message = str(parsed[1])
            recovery = str(parsed[2]) if len(parsed) > 2 else None

            # 4th element is JSON-serialized details dict (if present)
            details = None
            if len(parsed) > 3 and parsed[3] is not None:
                import json

                try:
                    details = json.loads(str(parsed[3]))
                except (json.JSONDecodeError, TypeError):
                    pass

            return _adcp_error_from_code(error_code, message, recovery, details)
    except (ValueError, SyntaxError):
        pass

    # adcp.server's platform dispatcher surfaces AdcpError to MCP clients as
    # ToolError("CODE: message"). Normalize that newer wire shape too so
    # transport-agnostic BDD assertions can inspect error_code consistently.
    if ":" in error_str:
        code, message = error_str.split(":", 1)
        code = code.strip()
        if code.isupper() and all(char.isalpha() or char == "_" for char in code):
            return _adcp_error_from_code(
                code,
                message.strip(),
                details={"suggestion": message.strip()},
            )

    # Fallback: try extract_error_info (handles direct ToolError construction)
    from src.core.tool_error_logging import extract_error_info

    error_code, message, recovery = extract_error_info(exc)
    if error_code != "TOOL_ERROR":
        return _adcp_error_from_code(error_code, message, recovery)

    return exc


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

        @pytest.mark.parametrize("transport", [Transport.IMPL, Transport.MCP])
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

    # -- Identity (one function, all transports) ----------------------------

    def identity_for(self, transport: Transport) -> ResolvedIdentity:
        """Build ResolvedIdentity with the correct protocol for *transport*.

        This is the single source of truth for test identity across all
        transports. The identity is cached per protocol so repeated calls
        with the same transport return the same object.

        In integration mode (``use_real_db=True``), the identity carries
        the real ``auth_token`` from the factory-created Principal row.
        This enables full auth chain testing: header → token → DB lookup.
        """
        from tests.harness.transport import TRANSPORT_PROTOCOL

        protocol = TRANSPORT_PROTOCOL[transport]
        if protocol not in self._identity_cache:
            from tests.factories.principal import PrincipalFactory

            # In integration mode, commit factory data first so the token
            # is visible to other sessions (e.g., get_principal_from_token
            # in the MCP auth chain uses a separate get_db_session() call).
            auth_token = None
            if self.use_real_db:
                self._commit_factory_data()
                auth_token = self._resolve_auth_token()

            self._identity_cache[protocol] = PrincipalFactory.make_identity(
                principal_id=self._principal_id,
                tenant_id=self._tenant_id,
                protocol=protocol,
                dry_run=self._dry_run,
                auth_token=auth_token,
                **self._tenant_overrides,
            )
        return self._identity_cache[protocol]

    def _ensure_principal_for_mcp(self, mcp_identity: ResolvedIdentity | None) -> str | None:
        """Create a Principal row on the fly so the bearer middleware can find it.

        Integration tests that create only a Tenant (no Principal) have no
        ``access_token`` to pass through ``x-adcp-auth``. The bearer
        middleware would then 401 every MCP call. Build a placeholder
        Principal under the test's ``(tenant_id, principal_id)`` so the
        full auth chain (token → Principal → ContextVars → ToolContext →
        ResolvedIdentity) runs against real DB rows.

        No-op when no session is bound (not integration mode) or when the
        identity has no tenant_id (auth-rejection tests that test the
        missing-tenant code path).
        """
        if mcp_identity is None or not mcp_identity.tenant_id or not mcp_identity.principal_id:
            return None

        from sqlalchemy import select

        from src.core.database.models import Principal as PrincipalRow
        from src.core.database.models import Tenant as TenantRow

        assert self._session is not None
        # Tenant must already exist — the harness's caller is responsible
        # for creating one via TenantFactory before dispatching MCP calls.
        tenant = self._session.scalars(select(TenantRow).filter_by(tenant_id=mcp_identity.tenant_id)).first()
        if tenant is None:
            return None

        existing = self._session.scalars(
            select(PrincipalRow).filter_by(
                tenant_id=mcp_identity.tenant_id,
                principal_id=mcp_identity.principal_id,
            )
        ).first()
        if existing is not None:
            return existing.access_token

        token = f"test-token-{mcp_identity.tenant_id}-{mcp_identity.principal_id}"
        principal = PrincipalRow(
            tenant_id=mcp_identity.tenant_id,
            principal_id=mcp_identity.principal_id,
            name=f"Test {mcp_identity.principal_id}",
            access_token=token,
            platform_mappings={"mock": {"advertiser_id": f"adv_{mcp_identity.principal_id}"}},
        )
        self._session.add(principal)
        self._session.commit()
        return token

    def _resolve_auth_token(self) -> str | None:
        """Look up the real access_token from the session-bound Principal.

        Only called in integration mode where ``self._session`` is bound
        to factory-created ORM models. Returns None if the principal
        hasn't been created yet (identity built before Given steps run).
        """
        if not self._session:
            return None
        from sqlalchemy import select

        from src.core.database.models import Principal

        token = self._session.scalars(
            select(Principal.access_token).filter_by(
                principal_id=self._principal_id,
                tenant_id=self._tenant_id,
            )
        ).first()
        return token

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

    def call_a2a(self, **kwargs: Any) -> Any:
        """Call an A2A skill through the production JSON-RPC surface.

        Override in subclasses that support ``Transport.A2A``. The helper
        method ``_run_a2a_client`` below handles the shared ASGI transport,
        auth headers, response extraction, and AdCP error reconstruction.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement call_a2a(). Override to enable Transport.A2A dispatch."
        )

    def _run_mcp_client(
        self,
        tool_name: str,
        response_cls: type,
        **kwargs: Any,
    ) -> Any:
        """MCP dispatch via httpx ``ASGITransport`` against ``core.main.build_app()``.

        Drives the full production pipeline: bearer-token middleware,
        FastMCP streamable-http transport, tool dispatcher, response
        envelope. The same Starlette app production binds with uvicorn
        runs in-process — no socket, but every middleware and validation
        hook is exercised.

        Identity resolution:

        * **Integration mode** (factory-created Principal with real
          ``access_token``): the harness sends ``x-adcp-auth: <token>``
          and the bearer middleware looks it up via ``_validate_token``
          → DB → :class:`Principal` → ContextVars consumed by
          ``auth_context_factory``. Tests exercise the same auth chain
          a real buyer hits.
        * **Unit mode** (no token): the harness patches
          ``resolve_identity_from_context`` so the wrapper layer
          synthesises identity from the in-memory mock. The bearer
          middleware accepts the missing/invalid token, but the patched
          resolver overrides identity downstream so business logic still
          sees a populated identity.

        Args:
            tool_name: MCP tool name (e.g., "get_products").
            response_cls: Pydantic model class to parse structured_content into.
            **kwargs: Tool arguments. ``identity`` is popped and used for
                the auth chain; ``req`` is popped and its fields unpacked
                into the arguments dict.
        """
        from unittest.mock import patch

        import httpx
        from fastmcp import Client
        from fastmcp.client.transports import StreamableHttpTransport

        from tests.harness._asgi_app import run_on_app_loop
        from tests.harness.transport import Transport

        self._commit_factory_data()

        _NO_OVERRIDE = object()
        identity = kwargs.pop("identity", _NO_OVERRIDE)
        mcp_identity = self.identity_for(Transport.MCP) if identity is _NO_OVERRIDE else identity

        # Unpack req object into flat arguments — MCP tools accept individual
        # params, not a request model.
        req = kwargs.pop("req", None)
        if req is not None and hasattr(req, "model_dump"):
            req_fields = req.model_dump(exclude_none=True)
            arguments = {**req_fields, **kwargs}
        else:
            arguments = dict(kwargs)

        auth_token = mcp_identity.auth_token if mcp_identity else None

        # The bearer-token middleware is captured at app build time and
        # validates every request against the real ``Principal.access_token``
        # column. Tests that don't create a Principal via factory (only a
        # Tenant) have no token to pass, but the middleware will still 401
        # on a stub. Auto-create a Principal so the chain works.
        if not auth_token and self.use_real_db and self._session is not None:
            auth_token = self._ensure_principal_for_mcp(mcp_identity)

        request_headers = {
            "x-adcp-auth": auth_token or "test-stub-token",
        }
        if mcp_identity and mcp_identity.tenant_id:
            request_headers["x-adcp-tenant"] = mcp_identity.tenant_id

        def _factory(app: Any):
            def httpx_factory(**hk: Any) -> httpx.AsyncClient:
                hk.setdefault("timeout", 30.0)
                hk["transport"] = httpx.ASGITransport(app=app)
                hk["base_url"] = "http://testserver"
                return httpx.AsyncClient(**hk)

            transport = StreamableHttpTransport(
                url="http://testserver/mcp/",
                headers=request_headers,
                httpx_client_factory=httpx_factory,
            )

            async def _call() -> Any:
                async with Client(transport) as client:
                    result = await client.call_tool(tool_name, arguments)
                    return response_cls(**result.structured_content)

            return _call()

        # Watchdog: if the MCP request hangs (psycopg pool wait, dropped DB,
        # FastMCP session deadlock, etc.), pytest-timeout's traceback only sees
        # idle worker threads — the actual culprit is the asyncio task on the
        # dedicated ASGI loop. faulthandler dumps every Python thread's stack,
        # surfacing the real frame. Fires before the 30s httpx timeout so the
        # dump precedes the eventual failure. MCP_HARNESS_HANG_DUMP_SECONDS=0
        # disables the watchdog.
        #
        # Process-global: only one faulthandler watchdog can be armed per process,
        # so sibling test infrastructure must not double-arm dump_traceback_later
        # while an MCP call is in flight.
        import os
        import sys
        import warnings

        raw = os.environ.get("MCP_HARNESS_HANG_DUMP_SECONDS", "20")
        try:
            hang_dump_after = float(raw)
        except ValueError:
            warnings.warn(
                f"Invalid MCP_HARNESS_HANG_DUMP_SECONDS={raw!r}; using default 20s.",
                stacklevel=2,
            )
            hang_dump_after = 20.0

        if hang_dump_after > 0:
            import faulthandler

            faulthandler.dump_traceback_later(hang_dump_after, file=sys.stderr, repeat=False)
        try:
            try:
                if mcp_identity is None:
                    return run_on_app_loop(_factory)
                if not auth_token:
                    # Unit mode: inject identity at the wrapper layer, since no
                    # real Principal row exists for the bearer middleware to find.
                    with patch(
                        "src.core.transport_helpers.resolve_identity_from_context",
                        return_value=mcp_identity,
                    ):
                        return run_on_app_loop(_factory)
                return run_on_app_loop(_factory)
            except Exception as exc:
                raise _unwrap_mcp_tool_error(exc) from exc
        finally:
            if hang_dump_after > 0:
                faulthandler.cancel_dump_traceback_later()

    def _run_a2a_client(
        self,
        skill_name: str,
        response_cls: type,
        **kwargs: Any,
    ) -> Any:
        """A2A dispatch via httpx ``ASGITransport`` against ``core.main.build_app()``."""
        import httpx

        from tests.a2a_helpers import make_a2a_skill_message
        from tests.harness._asgi_app import run_on_app_loop
        from tests.harness.transport import Transport

        self._commit_factory_data()

        _NO_OVERRIDE = object()
        identity = kwargs.pop("identity", _NO_OVERRIDE)
        a2a_identity = self.identity_for(Transport.A2A) if identity is _NO_OVERRIDE else identity

        req = kwargs.pop("req", None)
        if req is not None and hasattr(req, "model_dump"):
            req_fields = req.model_dump(exclude_none=True)
            parameters = {**req_fields, **kwargs}
        else:
            parameters = dict(kwargs)

        auth_token = a2a_identity.auth_token if a2a_identity else None
        if not auth_token and self.use_real_db and self._session is not None:
            auth_token = self._ensure_principal_for_mcp(a2a_identity)

        request_headers = {
            "Authorization": f"Bearer {auth_token or 'test-stub-token'}",
            "Content-Type": "application/json",
        }
        if a2a_identity and a2a_identity.tenant_id:
            request_headers["x-adcp-tenant"] = a2a_identity.tenant_id

        body = make_a2a_skill_message(skill_name, self._jsonable(parameters))

        def _factory(app: Any):
            async def _call() -> Any:
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                    timeout=30.0,
                ) as client:
                    response = await client.post("/", json=body, headers=request_headers)
                    response.raise_for_status()
                    payload = response.json()

                data = self._extract_a2a_data(payload)
                adcp_error = data.get("adcp_error")
                if adcp_error:
                    raise _adcp_error_from_code(
                        str(adcp_error.get("code") or "INTERNAL_ERROR"),
                        str(adcp_error.get("message") or adcp_error.get("code") or "A2A request failed"),
                        adcp_error.get("recovery"),
                        adcp_error.get("details") if isinstance(adcp_error.get("details"), dict) else None,
                    )
                return response_cls(**data)

            return _call()

        return run_on_app_loop(_factory)

    @classmethod
    def _jsonable(cls, value: Any) -> Any:
        """Convert test request payloads to JSON-compatible primitives."""
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json", exclude_none=True)
        if isinstance(value, dict):
            return {key: cls._jsonable(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._jsonable(item) for item in value]
        if isinstance(value, tuple):
            return [cls._jsonable(item) for item in value]
        return value

    @staticmethod
    def _extract_a2a_data(body: dict[str, Any]) -> dict[str, Any]:
        """Extract the first A2A DataPart payload from a JSON-RPC response."""
        if "error" in body:
            error = body["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise RuntimeError(f"A2A JSON-RPC error: {message}")

        result = body.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"A2A response missing result object: {body!r}")

        artifacts = result.get("artifacts") or []
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            for part in artifact.get("parts") or []:
                if isinstance(part, dict) and part.get("kind") == "data" and isinstance(part.get("data"), dict):
                    return part["data"]

        raise RuntimeError(f"A2A response missing DataPart payload: {body!r}")

    def _run_mcp_wrapper(
        self,
        wrapper_fn: Any,
        response_cls: type,
        **kwargs: Any,
    ) -> Any:
        """Legacy MCP dispatch: mock Context → async wrapper → parse response.

        .. deprecated::
            Use ``_run_mcp_client`` instead for full-pipeline dispatch.
            This method bypasses FastMCP middleware and TypeAdapter validation.
            Kept for unit-mode envs that cannot use the in-memory Client.
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from fastmcp.server.context import Context

        from tests.harness.transport import Transport

        self._commit_factory_data()

        _NO_OVERRIDE = object()
        identity = kwargs.pop("identity", _NO_OVERRIDE)
        mcp_identity = self.identity_for(Transport.MCP) if identity is _NO_OVERRIDE else identity

        # Unpack req object into flat kwargs — MCP wrappers accept individual
        # parameters, not a request model.
        req = kwargs.pop("req", None)
        if req is not None and hasattr(req, "model_dump"):
            req_fields = req.model_dump(exclude_none=True)
            # kwargs override req fields (explicit > implicit)
            kwargs = {**req_fields, **kwargs}

        mock_ctx = MagicMock(spec=Context)
        mock_ctx.get_state = AsyncMock(return_value=mcp_identity)

        tool_result = asyncio.run(wrapper_fn(ctx=mock_ctx, **kwargs))
        return response_cls(**tool_result.structured_content)

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

            # Defensively unbind any session left from a previous env's
            # __exit__ that aborted before its cleanup ran. Nested envs are
            # still unsupported (the second one would clobber the first's
            # session), but failing the entire test class because one
            # earlier test crashed mid-context is the wrong behaviour.
            for f in ALL_FACTORIES:
                f._meta.sqlalchemy_session = None

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
        self._identity_cache.clear()

        if errors:
            if len(errors) == 1:
                raise errors[0]
            raise ExceptionGroup("Multiple teardown errors", errors)
        return False


class IntegrationEnv(BaseTestEnv):
    """Integration test environment — real database, only mocks external services.

    Requires ``integration_db`` pytest fixture.
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
