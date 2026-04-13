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

        Identity is injected by monkey-patching ``_resolve_a2a_identity`` and
        ``_get_auth_token`` on the handler instance — single mock point, same
        as the MCP Client approach patches resolve_identity_from_context.

        Args:
            skill_name: A2A skill name (e.g., "get_products").
            response_cls: Pydantic model class to parse artifact data into.
            **kwargs: Skill parameters. ``identity`` is popped and used for
                the identity mock; remaining kwargs become skill parameters.
        """
        import asyncio

        from a2a.types import MessageSendParams, Task

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
        from tests.harness.transport import Transport
        from tests.utils.a2a_helpers import create_a2a_message_with_skill, extract_data_from_artifact

        self._commit_factory_data()

        # Pop identity — used for the handler mock, not sent as a skill parameter.
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
        # Single mock point: identity resolution.
        # _get_auth_token must return a non-None value when identity exists,
        # otherwise the handler rejects the request before _resolve_a2a_identity
        # is called. Use auth_token from identity, falling back to a sentinel.
        handler._resolve_a2a_identity = lambda *args, **kw: a2a_identity  # type: ignore[assignment]
        handler._get_auth_token = lambda *args, **kw: (  # type: ignore[assignment]
            (a2a_identity.auth_token or "harness-test-token") if a2a_identity else None
        )

        # Set tenant ContextVar so production code can read it
        if a2a_identity and a2a_identity.tenant:
            from src.core.config_loader import set_current_tenant

            set_current_tenant(a2a_identity.tenant)

        message = create_a2a_message_with_skill(skill_name=skill_name, parameters=parameters)
        params = MessageSendParams(message=message)

        async def _call():
            return await handler.on_message_send(params)

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

    def _run_rest_request(self, endpoint: str, **kwargs: Any) -> Any:
        """Shared REST dispatch: configure auth → build body → POST → return Response.

        Symmetric with ``_run_mcp_wrapper``. Handles the full REST lifecycle:
        1. Pop ``identity`` from kwargs and configure dep override for this request
        2. Commit factory data
        3. Build request body from remaining kwargs
        4. POST via TestClient
        5. Return raw httpx.Response

        Identity handling (mirrors production auth middleware):
        - identity is None → dep raises AdCPAuthenticationError (no token)
        - identity is ResolvedIdentity → dep returns it (valid token)
        - identity absent → uses default self.identity_for(Transport.REST)
        """
        from src.app import app
        from src.core.auth_context import _require_auth_dep, _resolve_auth_dep
        from tests.harness.transport import Transport

        _NO_OVERRIDE = object()
        identity = kwargs.pop("identity", _NO_OVERRIDE)
        if identity is _NO_OVERRIDE:
            identity = self.identity_for(Transport.REST)

        self._commit_factory_data()

        # Get client first (may set default dep overrides on first call),
        # then override per-request auth AFTER.
        client = self.get_rest_client()

        # Configure per-request auth (must be after get_rest_client)
        if identity is None:
            from src.core.exceptions import AdCPAuthenticationError

            def _no_auth() -> None:
                raise AdCPAuthenticationError("Authentication required")

            app.dependency_overrides[_require_auth_dep] = _no_auth
            app.dependency_overrides[_resolve_auth_dep] = lambda: None
        else:
            app.dependency_overrides[_require_auth_dep] = lambda: identity
            app.dependency_overrides[_resolve_auth_dep] = lambda: identity

        body = self.build_rest_body(**kwargs)
        return client.post(endpoint, json=body)

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
        """Return FastAPI TestClient with default auth dep override.

        The default dep override returns ``self.identity_for(Transport.REST)``.
        ``_run_rest_request`` overrides this per-request for multi-agent and
        no-auth scenarios. Direct callers of ``get_rest_client()`` get the
        default identity.
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
