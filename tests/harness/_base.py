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

import datetime as _dt
from typing import TYPE_CHECKING, Any, Self
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from pydantic import BaseModel
    from sqlalchemy.orm import Session

    from src.core.resolved_identity import ResolvedIdentity
    from tests.harness.transport import E2EConfig, Transport, TransportResult


class TestClock:
    """Single source of test time.

    Produces 'now' and relative future/past instants as ISO 8601 Z-suffixed
    strings for Gherkin step consumption. Feature files express time relatively
    (e.g. ``{30 days from now}``) and steps resolve the tokens via this clock
    at execution time, so no hardcoded date ever becomes stale.
    """

    def now(self) -> _dt.datetime:
        return _dt.datetime.now(_dt.UTC)

    def future(self, days: int) -> _dt.datetime:
        return self.now() + _dt.timedelta(days=days)

    def past(self, days: int) -> _dt.datetime:
        return self.now() - _dt.timedelta(days=days)

    @staticmethod
    def _iso(dt: _dt.datetime) -> str:
        return dt.isoformat().replace("+00:00", "Z")

    def now_iso(self) -> str:
        return self._iso(self.now())

    def future_iso(self, days: int) -> str:
        return self._iso(self.future(days))

    def past_iso(self, days: int) -> str:
        return self._iso(self.past(days))


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
        AdCPNotFoundError,
        AdCPRateLimitError,
        AdCPServiceUnavailableError,
        AdCPValidationError,
    )

    _CODE_TO_CLASS: dict[str, type[AdCPError]] = {
        cls.error_code: cls
        for cls in (
            AdCPValidationError,
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

    from fastmcp.exceptions import ToolError

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

    # Fallback: try extract_error_info (handles direct ToolError construction)
    from src.core.tool_error_logging import extract_error_info

    error_code, message, recovery = extract_error_info(exc)
    if error_code != "TOOL_ERROR":
        return _adcp_error_from_code(error_code, message, recovery)

    return exc


def _unwrap_a2a_server_error(exc: Exception) -> Exception:
    """Translate a2a ServerError back to the corresponding AdCPError.

    The A2A handler wraps AdCPError → ServerError (via _adcp_to_a2a_error).
    This reverses that translation so callers can ``pytest.raises(AdCPAuthenticationError)``
    instead of catching the transport-level wrapper.

    If the exception is not a ServerError or lacks enough info, returns it unchanged.
    """
    from a2a.types import InternalError, InvalidParamsError, InvalidRequestError
    from a2a.utils.errors import ServerError

    if not isinstance(exc, ServerError):
        return exc

    error = exc.error
    message = getattr(error, "message", str(exc))
    data = getattr(error, "data", None) or {}

    # If _adcp_to_a2a_error stored the error_code, reconstruct the exact subclass.
    error_code = data.get("error_code")
    if error_code:
        return _adcp_error_from_code(error_code, message, data.get("recovery"))

    from src.core.exceptions import (
        AdCPAuthenticationError,
        AdCPValidationError,
    )

    if isinstance(error, InvalidRequestError):
        return AdCPAuthenticationError(message)
    if isinstance(error, InvalidParamsError):
        return AdCPValidationError(message)
    if isinstance(error, InternalError):
        return RuntimeError(message)
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
        database_url: str | None = None,
        e2e_config: E2EConfig | None = None,
        **tenant_overrides: Any,
    ) -> None:
        self._principal_id = principal_id
        self._tenant_id = tenant_id
        self._dry_run = dry_run
        self._database_url = database_url or (e2e_config.postgres_url if e2e_config else None)
        self.e2e_config: E2EConfig | None = e2e_config
        self._tenant_overrides = tenant_overrides
        self.mock: dict[str, MagicMock] = {}
        self._patchers: list[Any] = []
        self._session: Session | None = None
        self._e2e_engine: Any = None  # Engine created from explicit database_url
        self._identity_cache: dict[str, ResolvedIdentity] = {}
        self._rest_client: Any = None  # Lazy-created TestClient
        self.clock: TestClock = TestClock()

    # -- Identity (one function, all transports) ----------------------------

    def identity_for(self, transport: Transport) -> ResolvedIdentity:
        """Build ResolvedIdentity with the correct protocol for *transport*.

        This is the single source of truth for test identity across all
        transports. The identity is cached per protocol so repeated calls
        with the same transport return the same object.

        In integration mode (``use_real_db=True``), the identity is built
        from real DB data: auth_token from Principal, tenant from Tenant ORM
        model via ``TenantContext.from_orm_model()``. This ensures all
        transports — including IMPL — see the same tenant config that
        production code would resolve via ``get_principal_from_token()``.

        In unit mode, the identity uses a synthetic tenant dict from
        ``TenantFactory.make_tenant()`` (no DB available).
        """
        from tests.harness.transport import TRANSPORT_PROTOCOL

        protocol = TRANSPORT_PROTOCOL[transport]
        if protocol not in self._identity_cache:
            if self.use_real_db and self._session:
                self._identity_cache[protocol] = self._resolve_identity_from_db(protocol)
            else:
                from tests.factories.principal import PrincipalFactory

                self._identity_cache[protocol] = PrincipalFactory.make_identity(
                    principal_id=self._principal_id,
                    tenant_id=self._tenant_id,
                    protocol=protocol,
                    dry_run=self._dry_run,
                    **self._tenant_overrides,
                )
        return self._identity_cache[protocol]

    def _resolve_identity_from_db(self, protocol: str) -> ResolvedIdentity:
        """Build ResolvedIdentity from real DB data (integration mode).

        Loads the actual Tenant ORM model and converts it to TenantContext
        (same path as production). Resolves auth_token from the Principal row.
        Auto-creates tenant + principal if they don't exist yet.
        """
        from src.core.resolved_identity import ResolvedIdentity
        from src.core.tenant_context import TenantContext
        from src.core.testing_hooks import AdCPTestContext

        self._ensure_default_data_for_auth()
        self._commit_factory_data()

        auth_token = self._resolve_auth_token()
        assert auth_token is not None, (
            f"auth_token is None for {self._principal_id}@{self._tenant_id} — "
            f"Principal must exist in DB before identity_for() is called. "
            f"_ensure_default_data_for_auth() should have created it."
        )

        # Load real Tenant from DB — same data that get_principal_from_token() returns
        from sqlalchemy import select

        from src.core.database.models import Tenant

        tenant_orm = self._session.scalars(select(Tenant).filter_by(tenant_id=self._tenant_id)).first()
        assert tenant_orm is not None, f"Tenant {self._tenant_id} not found in DB"

        tenant_ctx = TenantContext.from_orm_model(tenant_orm)

        return ResolvedIdentity(
            principal_id=self._principal_id,
            tenant_id=self._tenant_id,
            tenant=tenant_ctx,
            auth_token=auth_token,
            protocol=protocol,
            testing_context=AdCPTestContext(
                dry_run=self._dry_run,
                mock_time=None,
                jump_to_event=None,
                test_session_id=None,
            ),
        )

    def _ensure_default_data_for_auth(self) -> None:
        """Auto-create tenant + principal if they don't exist yet.

        E2E transports require a real auth token in the database.
        Some UC branches (e.g., UC-005) don't call setup_default_data()
        because in-process transports don't need DB-backed identity.
        This ensures a Principal row exists before resolving the token.
        """
        if not self._session:
            return
        from sqlalchemy import select

        from src.core.database.models import Principal, Tenant

        principal = self._session.scalars(
            select(Principal).filter_by(
                principal_id=self._principal_id,
                tenant_id=self._tenant_id,
            )
        ).first()
        if principal:
            return

        from tests.factories import PrincipalFactory, TenantFactory

        tenant = self._session.scalars(select(Tenant).filter_by(tenant_id=self._tenant_id)).first()
        if not tenant:
            tenant = TenantFactory(tenant_id=self._tenant_id)
        PrincipalFactory(tenant=tenant, principal_id=self._principal_id)

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

    def _run_a2a_handler(
        self,
        skill_name: str,
        response_cls: type,
        **kwargs: Any,
    ) -> Any:
        """A2A dispatch via real AdCPRequestHandler — exercises full A2A pipeline.

        Dispatches through the real AdCPRequestHandler.on_message_send(), which
        exercises: message parsing → skill routing → normalize_request_params →
        handler dispatch → _serialize_for_a2a → Task/Artifact framing.

        When the identity carries a real ``auth_token`` (integration mode),
        constructs a real ServerCallContext with AuthContext so the full auth
        chain runs: _get_auth_token → _resolve_a2a_identity → resolve_identity
        → get_principal_from_token DB lookup.

        When no real token is available (unit mode), falls back to monkey-patching
        ``_resolve_a2a_identity`` and ``_get_auth_token`` on the handler instance.

        Args:
            skill_name: A2A skill name (e.g., "get_products").
            response_cls: Pydantic model class to parse artifact data into.
            **kwargs: Skill parameters. ``identity`` is popped and used for
                the auth context; remaining kwargs become skill parameters.
        """
        import asyncio
        from types import MappingProxyType

        from a2a.server.context import ServerCallContext
        from a2a.types import MessageSendParams, Task

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
        from src.core.auth_context import AUTH_CONTEXT_STATE_KEY, AuthContext
        from tests.harness.transport import Transport
        from tests.utils.a2a_helpers import create_a2a_message_with_skill, extract_data_from_artifact

        self._commit_factory_data()

        # Pop identity — used for the auth context, not sent as a skill parameter.
        _NO_OVERRIDE = object()
        identity = kwargs.pop("identity", _NO_OVERRIDE)
        a2a_identity = self.identity_for(Transport.A2A) if identity is _NO_OVERRIDE else identity

        # The real A2A handler writes audit logs which require the tenant to exist
        # in the DB. Ensure the tenant record exists (idempotent) so audit logging
        # doesn't fail with FK violations on discovery endpoints.
        if self.use_real_db and a2a_identity and a2a_identity.tenant_id:
            self._ensure_tenant_for_audit(a2a_identity.tenant_id)

        # Unpack req object into flat parameters if present.
        # A2A skills accept a flat parameter dict, not a request model.
        req = kwargs.pop("req", None)
        if req is not None and hasattr(req, "model_dump"):
            req_fields = req.model_dump(mode="json", exclude_none=True)
            parameters = {**req_fields, **kwargs}
        else:
            parameters = dict(kwargs)

        handler = AdCPRequestHandler()

        # Choose auth strategy based on whether we have a real DB token.
        auth_token = a2a_identity.auth_token if a2a_identity else None

        if auth_token:
            # Real auth chain: build ServerCallContext with AuthContext so
            # _get_auth_token and _resolve_a2a_identity exercise the full
            # production path: token extraction → resolve_identity →
            # get_principal_from_token DB lookup.
            headers = MappingProxyType(
                {
                    "x-adcp-auth": auth_token,
                    "x-adcp-tenant": a2a_identity.tenant_id or "",
                }
            )
            auth_ctx = AuthContext(auth_token=auth_token, headers=headers)
            server_context: ServerCallContext | None = ServerCallContext(
                state={AUTH_CONTEXT_STATE_KEY: auth_ctx},
            )
        elif a2a_identity is None:
            # No identity at all — test auth error paths with no context.
            server_context = None
        else:
            # Unit mode: no real token but identity exists (discovery endpoints).
            # Monkey-patch identity resolution directly.
            handler._resolve_a2a_identity = lambda *args, **kw: a2a_identity  # type: ignore[assignment]
            handler._get_auth_token = lambda *args, **kw: None  # type: ignore[assignment]
            server_context = None

        # Set tenant ContextVar so production code can read it
        if a2a_identity and a2a_identity.tenant:
            from src.core.config_loader import set_current_tenant

            set_current_tenant(a2a_identity.tenant)

        message = create_a2a_message_with_skill(skill_name=skill_name, parameters=parameters)
        params = MessageSendParams(message=message)

        async def _call():
            return await handler.on_message_send(params, context=server_context)

        try:
            task_result = asyncio.run(_call())
        except Exception as exc:
            # Translate ServerError back to AdCPError for callers that catch
            # domain exceptions (e.g., pytest.raises(AdCPAuthenticationError)).
            raise _unwrap_a2a_server_error(exc) from exc

        # Parse Task.artifacts[0] into response_cls
        if not isinstance(task_result, Task):
            raise TypeError(f"Expected Task, got {type(task_result).__name__}: {task_result}")
        if not task_result.artifacts:
            raise ValueError(f"Task has no artifacts. Status: {task_result.status}")
        artifact_data = extract_data_from_artifact(task_result.artifacts[0])
        # Strip protocol fields added by _serialize_for_a2a (message, success).
        # These are A2A-envelope fields, not part of the Pydantic response model,
        # and cause ValidationError under extra="forbid" in non-production mode.
        artifact_data.pop("message", None)
        artifact_data.pop("success", None)
        return response_cls(**artifact_data)

    def _run_mcp_client(
        self,
        tool_name: str,
        response_cls: type,
        **kwargs: Any,
    ) -> Any:
        """MCP dispatch via in-memory Client — exercises full FastMCP pipeline.

        Uses FastMCP's in-memory transport (FastMCPTransport) to go through the
        complete server path: middleware chain → TypeAdapter → tool function.

        When the identity carries a real ``auth_token`` (integration mode),
        patches ``get_http_headers`` so the full auth chain runs: header
        extraction → tenant detection → token-to-principal DB lookup →
        ResolvedIdentity from real data.

        When no real token is available (unit mode), patches
        ``resolve_identity_from_context`` directly.

        Args:
            tool_name: MCP tool name (e.g., "get_products").
            response_cls: Pydantic model class to parse structured_content into.
            **kwargs: Tool arguments. ``identity`` is popped and used for the
                auth mock; ``req`` is popped and its fields unpacked into the
                arguments dict.
        """
        import asyncio
        from unittest.mock import patch

        from fastmcp import Client

        from src.core.main import mcp
        from tests.harness.transport import Transport

        self._commit_factory_data()

        # Pop identity — used for the auth mock, not sent as a tool argument.
        _NO_OVERRIDE = object()
        identity = kwargs.pop("identity", _NO_OVERRIDE)
        mcp_identity = self.identity_for(Transport.MCP) if identity is _NO_OVERRIDE else identity

        # Unpack req object into flat arguments if present.
        # MCP tools accept individual params, not a request model.
        req = kwargs.pop("req", None)
        if req is not None and hasattr(req, "model_dump"):
            req_fields = req.model_dump(exclude_none=True)
            # kwargs override req fields (explicit > implicit)
            arguments = {**req_fields, **kwargs}
        else:
            arguments = dict(kwargs)

        # Choose auth strategy based on whether we have a real DB token.
        auth_token = mcp_identity.auth_token if mcp_identity else None

        if auth_token:
            # Real auth chain: header → token → DB lookup → identity.
            # Patch get_http_headers in BOTH modules that import it:
            # transport_helpers (called by resolve_identity_from_context) and
            # mcp_auth_middleware (called for context_id extraction).
            headers = {
                "x-adcp-auth": auth_token,
                "x-adcp-tenant": mcp_identity.tenant_id or "",
            }

            async def _call():
                mock_th = patch("src.core.transport_helpers.get_http_headers", return_value=headers)
                mock_mw = patch("src.core.mcp_auth_middleware.get_http_headers", return_value=headers)
                with mock_th as patched_th, mock_mw as patched_mw:
                    async with Client(mcp) as client:
                        result = await client.call_tool(tool_name, arguments)
                        # Guard: verify the header patches were called.
                        # If a third module imports get_http_headers without being
                        # patched, this won't catch it — but at least we verify
                        # the known auth paths were exercised.
                        assert patched_th.called or patched_mw.called, (
                            f"Auth chain not exercised for {tool_name} — get_http_headers patches were not called"
                        )
                        return response_cls(**result.structured_content)
        else:
            # Unit mode: inject identity directly.
            async def _call():
                with patch(
                    "src.core.mcp_auth_middleware.resolve_identity_from_context",
                    return_value=mcp_identity,
                ):
                    async with Client(mcp) as client:
                        result = await client.call_tool(tool_name, arguments)
                        return response_cls(**result.structured_content)

        try:
            return asyncio.run(_call())
        except Exception as exc:
            raise _unwrap_mcp_tool_error(exc) from exc

    def _run_mcp_wrapper(
        self,
        wrapper_fn: Any,
        response_cls: type,
        **kwargs: Any,
    ) -> Any:
        """Legacy MCP dispatch: mock Context → async wrapper → parse response.

        Identity handling (mirrors production auth middleware):
        - identity is None → Context returns None (no token)
        - identity is ResolvedIdentity → Context returns it (valid token)
        - identity absent → uses default self.identity_for(Transport.MCP)

        Context extraction (mirrors production FastMCP parameter dispatch):
        In production, FastMCP extracts ``context`` from the tool schema
        and passes it as a separate kwarg to the MCP wrapper. When the
        harness receives a ``req`` object with ``req.context`` set, we
        extract it and pass it as a separate ``context`` kwarg to exercise
        the MCP wrapper's context merge branch (``if context is not None``).

        Subclass call_mcp() should do any pre-processing (enum coercion,
        kwarg popping) then delegate here.
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from fastmcp.server.context import Context

        from tests.harness.transport import Transport

        self._commit_factory_data()

        # Pop identity — it goes on the mock Context, not to the wrapper function.
        _NO_OVERRIDE = object()
        identity = kwargs.pop("identity", _NO_OVERRIDE)
        mcp_identity = self.identity_for(Transport.MCP) if identity is _NO_OVERRIDE else identity

        # Extract context from req if present and no explicit context kwarg.
        # Mirrors FastMCP behavior: tool parameters are passed as separate kwargs.
        if "context" not in kwargs:
            req = kwargs.get("req")
            if req is not None and hasattr(req, "context") and req.context is not None:
                kwargs["context"] = req.context

        mock_ctx = MagicMock(spec=Context)
        mock_ctx.get_state = AsyncMock(return_value=mcp_identity)

        tool_result = asyncio.run(wrapper_fn(ctx=mock_ctx, **kwargs))
        return response_cls(**tool_result.structured_content)

    def _run_rest_request(self, endpoint: str, **kwargs: Any) -> Any:
        """Shared REST dispatch: build headers → build body → POST → return Response.

        Symmetric with ``_run_mcp_wrapper``. Handles the full REST lifecycle:
        1. Pop ``identity`` from kwargs and build auth headers
        2. Commit factory data
        3. Build request body from remaining kwargs
        4. POST via TestClient with real headers
        5. Return raw httpx.Response

        When the identity carries a real ``auth_token`` (integration mode),
        sends real x-adcp-auth and x-adcp-tenant headers. The TestClient runs
        through UnifiedAuthMiddleware (ASGI), which extracts the token. The
        real _require_auth_dep then calls resolve_identity() →
        get_principal_from_token() against the test DB.

        Identity handling (mirrors production auth middleware):
        - identity is None → no headers sent (middleware returns unauthenticated)
        - identity is ResolvedIdentity → real auth headers sent
        - identity absent → uses default self.identity_for(Transport.REST)
        """
        from tests.harness.transport import Transport

        _NO_OVERRIDE = object()
        identity = kwargs.pop("identity", _NO_OVERRIDE)
        if identity is _NO_OVERRIDE:
            identity = self.identity_for(Transport.REST)

        self._commit_factory_data()

        client = self.get_rest_client()

        # Build auth headers from identity (real token → real auth chain)
        headers: dict[str, str] = {}
        if identity is not None:
            auth_token = identity.auth_token
            if auth_token:
                headers["x-adcp-auth"] = auth_token
            if identity.tenant_id:
                headers["x-adcp-tenant"] = identity.tenant_id

        body = self.build_rest_body(**kwargs)
        method = getattr(self, "REST_METHOD", "post")
        return getattr(client, method)(endpoint, json=body, headers=headers)

    def call_rest(self, **kwargs: Any) -> Any:
        """Call the REST endpoint and parse the response.

        Symmetric with ``call_impl``, ``call_a2a``, ``call_mcp``.
        Pops identity, configures auth, POSTs, parses response.
        Raises on HTTP errors (dispatcher catches and wraps in TransportResult).
        """
        endpoint = self.REST_ENDPOINT  # type: ignore[attr-defined]
        response = self._run_rest_request(endpoint, **kwargs)

        if response.status_code >= 400:
            raise self.parse_rest_error(response.status_code, response.json())

        return self.parse_rest_response(response.json())

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Convert call_impl kwargs to the REST endpoint body shape.

        Default: if ``req`` is a Pydantic model, delegates serialization to it
        via ``model_dump(mode="json", exclude_none=True)``.  Enums, nested
        models, and optional fields are handled by Pydantic — no manual
        field-by-field extraction needed.

        If no ``req`` is present, returns empty dict (valid for endpoints
        where all parameters are optional).

        Subclasses that receive flat kwargs (not a ``req`` object) must
        override to build the body dict themselves.
        """
        from pydantic import BaseModel as PydanticBaseModel

        req = kwargs.get("req")
        if req is not None and isinstance(req, PydanticBaseModel):
            return req.model_dump(mode="json", exclude_none=True)
        if req is None:
            return {}
        raise NotImplementedError(
            f"{type(self).__name__}.build_rest_body() received non-Pydantic 'req': {type(req)}. "
            "Override build_rest_body() to handle this type."
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

        Prefers the structured error_code in the response body (same precision
        as MCP and A2A unwrappers). Falls back to HTTP status mapping.
        """
        message = data.get("message", data.get("error", str(data)))

        # Try structured error_code first (same as MCP/A2A unwrappers)
        error_code = data.get("error_code")
        if error_code:
            recovery = data.get("recovery")
            details = data.get("details")
            return _adcp_error_from_code(error_code, message, recovery, details)

        # Fallback: map HTTP status to exception class
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
        details = data.get("details")
        recovery = data.get("recovery")
        error_kwargs: dict[str, Any] = {}
        if details is not None:
            error_kwargs["details"] = details
        if recovery is not None:
            error_kwargs["recovery"] = recovery
        return error_cls(message, **error_kwargs)

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

        Invalidates the identity cache so the next ``identity_for()`` call
        picks up the real auth_token from newly-committed Principal rows.
        """
        if self._session:
            self._session.commit()
            self._identity_cache.clear()

    def _ensure_tenant_for_audit(self, tenant_id: str) -> None:
        """Create a minimal tenant record if none exists (idempotent).

        The real A2A handler writes audit logs which require the tenant FK.
        Discovery endpoints (list_creative_formats, get_products, etc.) don't
        need a tenant for their logic, but the handler's post-invocation audit
        logging does. This creates a stub tenant so audit logging doesn't fail.

        Uses ``self._session`` (env-managed), not ``get_db_session()``.
        """
        if not self._session:
            return
        from sqlalchemy import select

        from src.core.database.models import Tenant

        exists = self._session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not exists:
            from tests.factories import TenantFactory

            TenantFactory(tenant_id=tenant_id)
            self._session.commit()

    # -- Context manager protocol ------------------------------------------

    def __enter__(self) -> Self:
        # 1. Database setup (integration mode only)
        if self.use_real_db:
            from sqlalchemy.orm import Session as SASession

            from tests.factories import ALL_FACTORIES

            # Guard against nested envs — session binding is global
            for f in ALL_FACTORIES:
                assert f._meta.sqlalchemy_session is None, (
                    f"Factory {getattr(f, '__name__', type(f).__name__)} session already bound — "
                    "nested IntegrationEnv contexts are not supported"
                )

            if self._database_url:
                # E2E mode: connect directly to the specified database
                # (e.g., Docker PostgreSQL) instead of the cached engine.
                from sqlalchemy import create_engine

                from src.core.database.database_session import _pydantic_json_serializer

                self._e2e_engine = create_engine(
                    self._database_url,
                    echo=False,
                    json_serializer=_pydantic_json_serializer,
                )
                engine = self._e2e_engine
            else:
                from src.core.database.database_session import get_engine

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

            # Dispose E2E engine (created per-env, not cached globally)
            try:
                if self._e2e_engine is not None:
                    self._e2e_engine.dispose()
                    self._e2e_engine = None
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

    def setup_tenant_inventory(self, tenant: Any) -> None:
        """Set up inventory prerequisites needed by the setup checklist.

        Creates: PropertyTag("all_inventory"), PublisherPartner,
        AuthorizedProperty. Fixes DNS-incompatible subdomains.

        Satisfies the "Authorized Properties" critical setup task so
        _create_media_buy_impl doesn't raise SetupIncompleteError.
        """
        from tests.factories import (
            AuthorizedPropertyFactory,
            PropertyTagFactory,
            PublisherPartnerFactory,
        )

        if "_" in (tenant.subdomain or ""):
            tenant.subdomain = tenant.subdomain.replace("_", "-")
        PropertyTagFactory(tenant=tenant, tag_id="all_inventory", name="All Inventory")
        PublisherPartnerFactory(tenant=tenant, publisher_domain="testpublisher.example.com")
        AuthorizedPropertyFactory(tenant=tenant, publisher_domain="testpublisher.example.com")

    def setup_product_chain(
        self,
        tenant: Any,
        product_id: str = "guaranteed_display",
        placements: list[dict[str, str]] | None = None,
    ) -> tuple[Any, Any]:
        """Create product + pricing option with required supporting data.

        Creates: tenant inventory prerequisites (via setup_tenant_inventory),
        Product, PricingOption (CPM/USD).

        Returns (product, pricing_option).
        """
        from tests.factories import PricingOptionFactory, ProductFactory

        self.setup_tenant_inventory(tenant)
        product = ProductFactory(
            tenant=tenant,
            product_id=product_id,
            property_tags=["all_inventory"],
            **({"placements": placements} if placements else {}),
        )
        pricing_option = PricingOptionFactory(
            product=product,
            pricing_model="cpm",
            currency="USD",
            is_fixed=True,
        )
        return product, pricing_option

    def seed_media_buy(
        self,
        *,
        tenant: Any,
        principal: Any,
        product: Any,
        pricing_option: Any = None,
        status: str = "active",
        buyer_ref: str = "test-buyer-ref",
        packages: list[dict] | None = None,
    ) -> Any:
        """Create a media buy through the real production path.

        In E2E mode: sends HTTP POST to Docker server, then reads the ORM
        object back from Docker PostgreSQL. The server generates the
        media_buy_id (uuid). This exercises the full creation path.

        In in-process mode: creates via factory (per-test DB, no collision).

        Returns the MediaBuy ORM object in both modes — callers access
        mb.media_buy_id, mb.status, mb.packages uniformly.
        """
        if packages is None:
            packages = [{"product_id": product.product_id, "budget": 5000.0}]

        if self.e2e_config:
            return self._seed_media_buy_e2e(
                tenant=tenant,
                principal=principal,
                product=product,
                pricing_option=pricing_option,
                status=status,
                buyer_ref=buyer_ref,
                packages=packages,
            )
        return self._seed_media_buy_impl(
            tenant=tenant,
            principal=principal,
            product=product,
            status=status,
            buyer_ref=buyer_ref,
            packages=packages,
        )

    def _seed_media_buy_e2e(
        self,
        *,
        tenant: Any,
        principal: Any,
        product: Any,
        pricing_option: Any,
        status: str,
        buyer_ref: str,
        packages: list[dict],
    ) -> Any:
        """Create media buy via real HTTP to Docker server.

        Sends a complete AdCP-compliant CreateMediaBuyRequest to the REST
        endpoint, then reads the ORM object back from Docker PostgreSQL
        so callers get the same type as in-process mode.
        """
        from datetime import UTC, datetime, timedelta

        import httpx

        from tests.harness.transport import Transport

        self._commit_factory_data()
        identity = self.identity_for(Transport.E2E_REST)
        base_url = self.e2e_config.base_url

        # Build pricing_option_id string (same convention as BDD steps)
        po_id = "cpm_usd_fixed"
        if pricing_option is not None:
            fixed_str = "fixed" if pricing_option.is_fixed else "auction"
            po_id = f"{pricing_option.pricing_model}_{pricing_option.currency.lower()}_{fixed_str}"

        now = datetime.now(UTC)
        body = {
            "buyer_ref": buyer_ref,
            "brand": {"domain": "test-brand.example.com"},
            "start_time": (now + timedelta(days=1)).isoformat(),
            "end_time": (now + timedelta(days=30)).isoformat(),
            "packages": [
                {
                    "product_id": pkg.get("product_id", product.product_id),
                    "budget": pkg.get("budget", 5000.0),
                    "buyer_ref": f"pkg-{i + 1}",
                    "pricing_option_id": po_id,
                }
                for i, pkg in enumerate(packages)
            ],
        }

        with httpx.Client(base_url=base_url, timeout=30) as client:
            resp = client.post(
                "/api/v1/media-buys",
                json=body,
                headers={
                    "x-adcp-auth": identity.auth_token,
                    "x-adcp-tenant": identity.tenant["subdomain"],
                    "Content-Type": "application/json",
                },
            )

        assert resp.status_code == 200, f"seed_media_buy E2E failed: HTTP {resp.status_code}\n{resp.text[:500]}"
        data = resp.json()
        media_buy_id = data["media_buy_id"]

        # Read the ORM object back from Docker PostgreSQL so callers
        # get attribute access (mb.media_buy_id, mb.status, mb.packages)
        from sqlalchemy import select

        from src.core.database.models import MediaBuy

        self._session.expire_all()  # Clear stale cache
        mb = self._session.scalars(select(MediaBuy).filter_by(media_buy_id=media_buy_id)).first()
        assert mb is not None, (
            f"seed_media_buy E2E: created media_buy_id={media_buy_id} via HTTP "
            f"but can't read it back from DB. Check E2E_POSTGRES_URL points at "
            f"the same database the Docker server uses."
        )
        return mb

    def _seed_media_buy_impl(
        self,
        *,
        tenant: Any,
        principal: Any,
        product: Any,
        status: str,
        buyer_ref: str,
        packages: list[dict],
    ) -> dict:
        """Create media buy via factory (in-process, own DB per test).

        Returns the ORM object directly — step definitions access
        attributes like mb.media_buy_id, mb.status, mb.packages.
        """
        from tests.factories import MediaBuyFactory, MediaPackageFactory

        media_buy = MediaBuyFactory(
            tenant=tenant,
            principal=principal,
            buyer_ref=buyer_ref,
            currency="USD",
            status=status,
        )
        for pkg in packages:
            MediaPackageFactory(
                media_buy=media_buy,
                package_config={
                    "package_id": pkg.get("package_id", media_buy.media_buy_id + "_pkg"),
                    "product_id": pkg.get("product_id", product.product_id),
                    "budget": float(pkg.get("budget", 5000.0)),
                },
            )
        self._commit_factory_data()
        return media_buy

    def _build_mock_context_manager(self, tool_name: str = "tool_call") -> Any:
        """Build a mock context manager with real DB records for FK constraints.

        Creates real Context and WorkflowStep rows so FK constraints on
        ObjectWorkflowMapping succeed. Returns the configured MagicMock.

        Used by integration harnesses that mock the context manager but need
        real DB rows for FK integrity.
        """
        import uuid
        from unittest.mock import MagicMock

        mock_ctx_mgr = MagicMock()

        def _create_real_context(*args: Any, **kwargs: Any) -> MagicMock:
            from src.core.database.database_session import get_db_session
            from src.core.database.models import Context as DBContext

            ctx_id = f"test_ctx_{uuid.uuid4().hex[:8]}"
            with get_db_session() as session:
                db_ctx = DBContext(
                    context_id=ctx_id,
                    tenant_id=self._tenant_id,
                    principal_id=self._principal_id,
                    conversation_history=[],
                )
                session.add(db_ctx)
                session.commit()
            mock_context = MagicMock()
            mock_context.context_id = ctx_id
            return mock_context

        def _create_real_step(*args: Any, **kwargs: Any) -> MagicMock:
            from src.core.database.database_session import get_db_session
            from src.core.database.models import WorkflowStep

            step_id = f"test_step_{uuid.uuid4().hex[:8]}"
            ctx_id = kwargs.get("context_id") or (args[0] if args else None)
            if ctx_id is None:
                ctx = _create_real_context()
                ctx_id = ctx.context_id
            with get_db_session() as session:
                db_step = WorkflowStep(
                    step_id=step_id,
                    context_id=ctx_id,
                    step_type=kwargs.get("step_type", "tool_call"),
                    tool_name=kwargs.get("tool_name", tool_name),
                    status="pending",
                    owner="principal",
                )
                session.add(db_step)
                session.commit()
            mock_step = MagicMock()
            mock_step.step_id = step_id
            return mock_step

        # Wire both APIs (different envs use different create methods)
        mock_ctx_mgr.create_context.side_effect = _create_real_context
        mock_ctx_mgr.get_context.return_value = None
        mock_ctx_mgr.get_or_create_context.side_effect = _create_real_context
        mock_ctx_mgr.create_workflow_step.side_effect = _create_real_step
        mock_ctx_mgr.update_workflow_step.return_value = None
        mock_ctx_mgr.add_message.return_value = None

        return mock_ctx_mgr

    def get_rest_client(self) -> Any:
        """Return FastAPI TestClient — no dependency overrides.

        The TestClient runs through UnifiedAuthMiddleware (ASGI), which
        extracts tokens from real headers. Auth is handled per-request
        via headers in ``_run_rest_request``, not via dependency overrides.
        """
        if self._rest_client is None:
            from starlette.testclient import TestClient

            from src.app import app

            self._rest_client = TestClient(app)

        return self._rest_client
