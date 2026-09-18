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
    call_a2a(**kwargs): Any            -- dispatch through the A2A handler
    REST_ENDPOINT: str                 -- POST endpoint path for REST dispatch
    build_rest_body(**kwargs): dict    -- convert kwargs to REST body
    parse_rest_response(data): model  -- parse JSON dict to Pydantic model
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Self, cast, get_type_hints
from unittest.mock import AsyncMock, MagicMock, patch

from tests.harness._realize import e2e_unsupported, realize_e2e

# The MCP transport boots the real FastMCP app lifespan, which starts the
# background schedulers. Those run a batch immediately on the *real* wall clock
# and rewrite media-buy status rows — silently mutating data a test just seeded
# (e.g. promoting a seeded pending_start buy to active). Suppress them for all
# harness-driven tests; setdefault so an explicit override still wins.
# (src.core.main._background_schedulers_enabled reads this at lifespan runtime.)
os.environ.setdefault("ADCP_RUN_BACKGROUND_SCHEDULERS", "false")

# RUNTIME imports, not TYPE_CHECKING ones. json_safe does isinstance() checks against
# these at call time, and DeliverResult is CONSTRUCTED here (the dispatch return
# contract) -- a TYPE_CHECKING-only import would be a NameError, not a type-checker
# convenience.
from datetime import date, datetime  # noqa: E402
from decimal import Decimal  # noqa: E402
from enum import Enum  # noqa: E402

from pydantic import BaseModel  # noqa: E402

from tests.factories.account import DEFAULT_TEST_ACCOUNT_ID  # noqa: E402  (re-export)
from tests.harness.transport import DeliverResult  # noqa: E402
from tests.helpers.credentials import credential_headers  # noqa: E402

#: The token the harness presents when a scenario wants a credential that is presented and
#: rejected: it matches no Principal row, so the resolver answers AUTH_INVALID on a protected
#: tool and treats it as absent on a public one.
INVALID_TOKEN = "invalid-token-harness"

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from src.core.resolved_identity import PublicIdentity, ResolvedIdentity
    from tests.harness.transport import E2EConfig, Transport, TransportResult


def json_safe(value: Any) -> Any:
    """Recursively convert pydantic models into the JSON forms a wire body carries.

    A REST body is JSON. When a step dispatches a RAW parameter bag rather than a built
    request -- which is what lets a schema-invalid payload actually reach the transport and
    be graded on the wire -- that bag can hold typed objects a scenario constructed for
    setup (an AccountReference, a Budget). ``req.model_dump(mode="json")`` used to convert
    them on the way out; the raw path has to do the same or serialization fails with
    "Object of type X is not JSON serializable" and the scenario grades a TypeError instead
    of the server's answer.

    Leaves everything else untouched, so a deliberately-malformed value still reaches the
    wire malformed -- which is the entire point of dispatching raw.
    """
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, Enum):
        return value.value
    # datetime/date/Decimal are what model_dump(mode="json") converts and a raw bag does
    # not: a step that stashed a real datetime for setup would otherwise reach the wire as
    # a Python object and be rejected for the WRONG reason -- the scenario would grade a
    # serialization artefact instead of the server's answer.
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    return value


class WireError(Exception):
    """A transport failure carrying the envelope the buyer received, VERBATIM.

    Deliberately NOT an ``AdCPSalesAgentError`` subclass. The harness used to rebuild the
    matching production exception from wire bytes so a test could write
    ``pytest.raises(AdCPNotFoundError)`` through a transport; that map covered 20 of
    43 classes, was silent about the other 23, and its own docstring conceded the
    reconstruction was lossy. Grading OUR class hierarchy through a lossy copy of
    production's constructor is not the same as grading the buyer's contract.

    This type carries no code-to-class knowledge at all. It exists so a failed wire
    dispatch can still raise -- callers up the stack expect an exception -- while the
    thing being asserted stays the envelope, reachable as ``.envelope`` and published
    by the dispatchers as ``TransportResult.wire_error_envelope``.
    """

    def __init__(self, envelope: dict) -> None:
        self.envelope = envelope
        errors = envelope.get("errors") or [{}]
        code = errors[0].get("code") if isinstance(errors[0], dict) else None
        super().__init__(f"wire error {code or '(no code)'}")


def _mcp_wire_envelope(exc: Exception) -> dict | None:
    """The two-layer envelope inside a FastMCP ``ToolError``, or ``None``.

    The MCP boundary translator raises ``AdCPToolError`` (single-arg JSON envelope)
    so FastMCP serializes ``str(exc)`` as the JSON-encoded envelope. This parses that
    JSON and RETURNS IT. It does not rebuild an exception: the envelope is what the
    buyer received, and it already carries code, message, recovery, suggestion, field
    and details.

    Falls back to the legacy tuple-string shape for any plain ``ToolError`` raised
    outside the boundary translator; anything else carries no envelope.
    """
    import ast as _ast
    import json

    from fastmcp.exceptions import ToolError

    if not isinstance(exc, ToolError):
        return None

    error_str = str(exc)

    try:
        parsed = json.loads(error_str)
        if isinstance(parsed, dict):
            envelope = _wire_envelope(parsed)
            if envelope is not None:
                return envelope
    except (json.JSONDecodeError, TypeError):
        pass

    # Legacy shape (test fixtures that mock ToolError directly):
    # tuple-stringified `('CODE', 'message', 'recovery', '{"details": ...}')`.
    try:
        tup = _ast.literal_eval(error_str)
        if isinstance(tup, tuple) and len(tup) >= 2:
            entry: dict = {"code": str(tup[0])}
            if len(tup) > 3 and tup[3] is not None:
                try:
                    extra = json.loads(str(tup[3]))
                    if isinstance(extra, dict):
                        if extra.get("details") is not None:
                            entry["details"] = extra["details"]
                        if extra.get("field") is not None:
                            entry["field"] = extra["field"]
                except (json.JSONDecodeError, TypeError):
                    pass
            return {"adcp_error": dict(entry), "errors": [entry]}
    except (ValueError, SyntaxError):
        pass

    return None


def _wire_envelope(envelope: dict) -> dict | None:
    """Normalise a captured error body into the two-layer envelope shape, or ``None``.

    Accepts what ``to_wire(AdcpErrorResponse.of(exc))`` produces
    (``{"adcp_error": {...}, "errors": [...]}``) and the legacy flat shape
    (``{"error_code": ..., "recovery": ...}``), and RETURNS THE ENVELOPE.

    It used to return a reconstructed ``AdCPSalesAgentError`` built from those bytes, with a
    hand-maintained code-to-class map. That map covered 20 of 43 classes and was
    silent about the rest, so type identity was already lost for most codes; its own
    docstring conceded the reconstruction was "lossy by construction"; and it was the
    only place outside production that called an ``AdCPSalesAgentError`` constructor, which made
    it the only place that could drift from a signature change -- and it did, twice,
    the second time costing 469 tests.

    Arming a fault still needs a real typed exception; the adapter genuinely raises
    one. ASSERTING an outcome never does: the envelope IS what the buyer received.
    """
    if not isinstance(envelope, dict):
        return None
    if isinstance(envelope.get("errors"), list) and envelope["errors"]:
        return envelope
    if isinstance(envelope.get("adcp_error"), dict):
        entry = dict(envelope["adcp_error"])
        return {**envelope, "errors": [entry]}
    # Legacy flat shape from tests that predate the envelope.
    code = envelope.get("error_code") or envelope.get("code")
    if not code:
        return None
    entry = {"code": code}
    for key in ("message", "recovery", "suggestion", "field", "details"):
        if envelope.get(key) is not None:
            entry[key] = envelope[key]
    return {"adcp_error": dict(entry), "errors": [entry]}


def _a2a_wire_envelope(exc: Exception) -> dict | None:
    """The two-layer envelope inside an a2a ``A2AError``'s ``data``, or ``None``.

    The A2A dispatcher wraps an ``AdCPSalesAgentError`` into a failed Task whose artifact
    carries the envelope; a JSON-RPC-level ``A2AError`` carries it in ``data``.

    Returns the ENVELOPE. The three-way fallback ladder that used to sit here --
    ``InvalidRequestError`` -> AdCPAuthenticationError, ``InvalidParamsError`` ->
    AdCPValidationError, ``InternalError`` -> RuntimeError -- is gone with it: those
    were hand-maintained guesses at what the wire meant, and a guess is not evidence
    of what the buyer received.
    """
    from a2a.utils.errors import A2AError

    if not isinstance(exc, A2AError):
        return None
    data = getattr(exc, "data", None)
    return _wire_envelope(data) if isinstance(data, dict) else None


def _a2a_send_message_configuration(spec: dict[str, Any]) -> Any:
    """Build the A2A ``SendMessageConfiguration`` carrying a protocol-level push config.

    ``message/send`` registers a webhook one level ABOVE the AdCP tool
    parameters: ``params.configuration.task_push_notification_config``
    (``src/a2a_server/adcp_a2a_server.py`` — ``on_message_send`` reads it before
    any skill routing happens). It is therefore not reachable by putting a
    ``push_notification_config`` in the skill parameters, and it exists on no
    other transport — MCP and REST have no equivalent protocol envelope.

    *spec* is the plain dict a step writes (``{"url": ..., "authentication":
    {"scheme": ..., "credentials": ...}}``). Note the SINGULAR ``scheme``: the
    A2A wire type is the protobuf ``AuthenticationInfo``, not the AdCP
    ``Authentication`` object with its ``schemes`` array. Absent credentials are
    sent as the protobuf default (empty string) rather than omitted, because
    that is what a buyer's client actually puts on the wire for an unset
    protobuf string — the field cannot be "missing" in proto3.
    """
    from a2a.types import AuthenticationInfo, SendMessageConfiguration, TaskPushNotificationConfig

    fields: dict[str, Any] = {"url": spec["url"]}
    authentication = spec.get("authentication")
    if authentication is not None:
        fields["authentication"] = AuthenticationInfo(
            scheme=authentication.get("scheme") or "",
            credentials=authentication.get("credentials") or "",
        )
    return SendMessageConfiguration(task_push_notification_config=TaskPushNotificationConfig(**fields))


def _a2a_call_context(credential: dict[str, str]) -> Any:
    """The ``ServerCallContext`` an in-process A2A dispatch presents *credential* on.

    Carries the request headers where the SDK's own ``DefaultServerCallContextBuilder``
    places them, ``state["headers"]``, so ``AdCPRequestHandler`` reads them off its call
    context and the real resolver parses the credential exactly as it does for a request
    over HTTP. Shared by the harness's A2A leg and the raw-wire A2A sender.
    """
    from a2a.server.routes.common import ServerCallContext

    return ServerCallContext(state={"headers": dict(credential)})


def _addressed_tenant(credential: dict[str, str]) -> str | None:
    """The tenant_id a credential addresses through ``x-adcp-tenant``, or ``None``."""
    for name, value in credential.items():
        if name.lower() == "x-adcp-tenant":
            return value
    return None


class _TestClock:
    """Minimal clock for BDD relative date-token resolution.

    The media-buy Given steps resolve Gherkin tokens (``{now}``,
    ``{30 days from now}``, ``{1 day ago}``) against ``ctx["env"].clock`` using
    the ``now_iso`` / ``future_iso`` / ``past_iso`` interface. Emits the
    ``YYYY-MM-DDTHH:MM:SSZ`` shape AdCP request validators accept.
    """

    @staticmethod
    def _iso(dt: Any) -> str:
        # The ONE place all three accessors below format, so minting here records
        # every clock-derived timestamp this mixin hands a scenario. They reach
        # dispatched payloads as start_time/end_time, and a value read off the clock
        # differs on every run — ``compare_payloads`` interns what the mint record
        # names and diffs everything else verbatim (tests/factories/mint.py).
        from tests.factories.mint import mint

        return mint(dt.strftime("%Y-%m-%dT%H:%M:%SZ"))

    def now_iso(self) -> str:
        from datetime import UTC, datetime

        return self._iso(datetime.now(UTC))

    def future_iso(self, days: int) -> str:
        from datetime import UTC, datetime, timedelta

        return self._iso(datetime.now(UTC) + timedelta(days=days))

    def past_iso(self, days: int) -> str:
        from datetime import UTC, datetime, timedelta

        return self._iso(datetime.now(UTC) - timedelta(days=days))


def _e2e_external_seams_exercised(self: IntegrationEnv) -> bool:
    """Whether the SERVER did the work, read off the audit trail it wrote.

    ``log_operation`` writes one ``audit_logs`` row per tool call and the database is the
    audit authority (src/core/audit_logger.py), so that row is the live-stack counterpart
    of the in-process ``audit_logger`` mock the other branch counts. Same claim on both
    branches: the seller recorded this operation as it served it.

    NOT VACUOUS, and that rests on a fact rather than on hope: ``_reset_e2e_db``
    (tests/bdd/conftest.py) empties every data table BEFORE each e2e scenario builds its
    env, so ``audit_logs`` starts this scenario empty, and the Givens write through
    factories, which audit nothing. A row for this tenant therefore came from the request
    under test. A response the server never actually served leaves the table empty and
    this returns False.
    """
    return bool(self.get_audit_logs())


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

        @pytest.mark.parametrize("transport", [Transport.A2A, Transport.MCP, Transport.REST])
        def test_something(self, integration_db, transport):
            with CreativeSyncEnv() as env:
                result = env.call_via(transport, creatives=[...])
                assert result.is_success

    Attributes:
        mock: dict[str, MagicMock]  -- active mocks keyed by short name
        identity: ResolvedIdentity  -- the caller ``call_impl`` hands the implementation

    THE HARNESS PRESENTS A CREDENTIAL, NEVER AN IDENTITY. ``credential()`` builds the
    headers dict a buyer would send; every wire leg injects that dict where its transport
    reads headers, and the real resolver answers. There is no token-less identity and no
    resolver patch: a scenario that needs a particular caller mints that caller's principal
    and presents its token.
    """

    EXTERNAL_PATCHES: dict[str, str] = {}
    ASYNC_PATCHES: set[str] = set()  # Names that need AsyncMock (for async functions)
    MODULE: str = ""  # Convenience for unit envs building patch paths
    REST_ENDPOINT: str = ""  # Override in subclass for REST dispatch
    # The tool/skill this env dispatches. Declaring these is what lets the base
    # own call_mcp/call_a2a instead of every env re-implementing the same
    # one-line delegation. REST_METHOD's de-facto
    # contract lives at dispatchers.py's getattr(env, "REST_METHOD", "post").
    MCP_TOOL: str = ""
    A2A_SKILL: str = ""
    # The parser the base delegation feeds wire dicts to. Declared per env
    # because envs parse into their LOCAL response subclass, which is not always
    # the tool's pinned SDK model — defaulting to the pinned model would quietly
    # change what call_mcp/call_a2a return for every converted env. Envs that
    # select a parser from request CONTENT override response_parser() instead.
    RESPONSE_MODEL: Any = None
    use_real_db: bool = False

    def __init__(
        self,
        principal_id: str = "test_principal",
        tenant_id: str = "test_tenant",
        database_url: str | None = None,
        e2e_config: E2EConfig | None = None,
        **tenant_overrides: Any,
    ) -> None:
        self._principal_id = principal_id
        self._tenant_id = tenant_id
        # E2E mode: bind factories to the live server's DB so the HTTP-reached
        # server sees Given-step data. Explicit database_url wins; else the
        # e2e_config's postgres_url. None => normal cached/integration engine.
        self._database_url = database_url or (e2e_config.postgres_url if e2e_config else None)
        self.e2e_config: E2EConfig | None = e2e_config
        self._e2e_engine: Any = None
        self._tenant_overrides = tenant_overrides
        self.mock: dict[str, MagicMock] = {}
        self._enter_cleanups: list[tuple[str, Callable[[], None]]] = []
        self._session: Session | None = None
        # Unit mode has no Principal row to read a token from, so the env holds the
        # principal the factory BUILT for it, keyed by the (principal, tenant) pair so a
        # ``switch_principal`` mints a different token.
        self._unit_principals: dict[tuple[str, str], Any] = {}
        self._rest_client: Any = None  # Lazy-created TestClient
        self.clock = _TestClock()  # BDD steps may use env.clock for date tokens
        # Raw A2A Task returned by the last _run_a2a_handler call. The submitted
        # (manual-approval) contract lives on the Task itself — state=SUBMITTED
        # with NO artifacts — and the synthesized submitted wire above cannot
        # prove artifact absence, so guards assert on this captured Task.
        self._last_a2a_task: Any = None

    # -- Transport mode -----------------------------------------------------

    @property
    def is_e2e(self) -> bool:
        """True when this env dispatches over the live HTTP server (e2e mode).

        Keys on ``e2e_config`` — the same signal ``conftest`` uses to thread the
        live-stack config and ``RestE2EDispatcher`` uses to select HTTP
        dispatch. A bare ``database_url`` rebinds factories to another DB but is
        NOT e2e mode (no server-surface realization needed). Mock-setup methods
        dispatch on this via :func:`tests.harness._realize.realize_e2e`.
        """
        return self.e2e_config is not None

    @realize_e2e(
        e2e_unsupported(
            "no server fault-injection surface for a genuinely untyped exception on a live "
            "remote process (same structural limitation prkv.8's own e2e-verify atom hit)"
        )
    )
    def inject_untyped_exception(self, exception: Exception) -> None:
        """Make the skill's business logic raise *exception* directly (prkv.18).

        Substitutes the implementation at its REGISTRY ROW, so every transport reaches the
        raising stand-in. It used to patch a dotted path to the ``_impl`` function, declared
        per env as ``IMPL_TARGET``; that stopped working the day the transports began
        dispatching through ``TOOLS``, because the row holds the function OBJECT and a
        module attribute is no longer what anything calls. The env already names its tool
        (``MCP_TOOL``), so the second declaration is gone with the mechanism that needed it.

        Registers the patcher with the same ``_guard`` cleanup registry ``EXTERNAL_PATCHES``
        uses, so both release paths (a normal ``__exit__`` and a failed ``__enter__``) stop
        it and this needs no new cleanup path.

        Skill-agnostic by design: any env that declares ``MCP_TOOL`` gets this capability for
        free, rather than each domain mixin hand-rolling its own untyped-exception injector.

        For a genuinely untyped exception, the REST boundary's catch-all
        handler is reachable only through Starlette's ``ServerErrorMiddleware``
        (see ``prkv.8``), which always re-raises after building its response —
        the default ``TestClient(app)`` (``raise_server_exceptions=True``)
        would surface that re-raise as a test error instead of the wire
        response. Setting the INSTANCE attribute here (not a class-level
        default on ``IntegrationEnv``) scopes the opt-out to exactly this
        call, on this env instance — ``get_rest_client()`` is lazy, so a
        Given-time set is honored at dispatch time.
        """
        from dataclasses import replace

        from src.core.tools.registry import _TOOLS

        if not self.MCP_TOOL:
            raise ValueError(
                f"{type(self).__name__} declares no MCP_TOOL — set it to the tool's registry "
                "name before calling inject_untyped_exception()"
            )
        # A REAL async function, annotated like the implementation it replaces -- not an
        # AsyncMock. ToolSpec.__post_init__ derives the row's credential policy from the
        # impl's `identity` annotation, so `replace(row, impl=AsyncMock())` raised
        # "TypeError: <AsyncMock ...> is not a module, class, method, or function" out of
        # get_type_hints before the scenario could dispatch anything. The annotations are
        # the CLASS OBJECTS taken off the original, so get_type_hints returns them without
        # re-resolving a string in this module's namespace, and the substituted row keeps
        # the tool's own DTO and identity type.
        spec = _TOOLS[self.MCP_TOOL]
        declared = get_type_hints(spec.impl)

        async def raising(*, req: Any, identity: Any) -> Any:
            raise exception

        raising.__annotations__ = {"req": declared["req"], "identity": declared["identity"]}
        patcher = patch.dict(_TOOLS, {self.MCP_TOOL: replace(spec, impl=raising)})
        patcher.start()
        self.mock["_untyped_exception"] = raising
        self._guard("patch:_untyped_exception", patcher.stop)
        self.REST_RAISE_SERVER_EXCEPTIONS = False

    # -- Credential (one method, all legs) -----------------------------------

    def credential(self, **overrides: Any) -> dict[str, str]:
        """The headers this env's buyer presents: THE one way a wire leg authenticates.

        ``credential_headers(token=<the env principal's token>, tenant=self._tenant_id)``
        with *overrides* applied through the same two keywords.

        - ``env.credential()``: the env's principal, valid token.
        - ``env.credential(token=INVALID_TOKEN)``: presented and rejected.
        - ``env.credential(token=None)``: nothing presented, tenant still addressed.
        - ``{}``: no headers at all, which is what ``credential={}`` on a dispatch sends.

        The token is read at DISPATCH time. In integration and e2e mode it comes from the
        Principal row, so a row committed by a later Given is seen; no row means
        ``token=None``, which the resolver answers AUTH_MISSING on a protected tool. In unit
        mode it comes from the principal the factory built for this env, and the
        substitutes ``__enter__`` installs make the resolver accept exactly that token.

        ``x-adcp-tenant`` carries the tenant_id on every leg. ``_detect_tenant`` tries it as a
        subdomain and then takes it as the literal id, so it resolves either way.
        """
        values: dict[str, Any] = {"tenant": self._tenant_id, "host": self._tenant_virtual_host()}
        if "token" not in overrides:
            values["token"] = self._principal_token()
        unknown = set(overrides) - {"token", "tenant", "host"}
        if unknown:
            raise TypeError(f"credential() takes token, tenant and host, not {sorted(unknown)}")
        values.update(overrides)
        return credential_headers(**values)

    def _tenant_virtual_host(self) -> str | None:
        """The host this env's tenant answers on, READ off the row.

        Read rather than derived: ``virtual_host`` is its own column with its own sequence
        (tests/factories/CLAUDE.md — deriving one independent column from another invents a
        shape constraint on the source), so the only truthful answer is what was persisted.

        ``None`` in unit mode and when no row exists, and ``credential_headers`` then falls
        back to ``x-adcp-tenant``: unit mode substitutes the resolver's database reads
        outright, so there is no Host lookup for a Host to satisfy.
        """
        if not self.use_real_db or not self._session:
            return None
        from sqlalchemy import select

        from src.core.database.models import Tenant

        self._commit_factory_data()
        return self._session.scalars(select(Tenant.virtual_host).filter_by(tenant_id=self._tenant_id)).first()

    def _principal_token(self) -> str | None:
        """The token the env principal presents, or ``None`` when no such principal exists.

        The row holds only the hash, so the plaintext is DERIVED, the way the factory
        derived it (``plaintext_token_for``); in DB mode the row must exist for the resolver
        to find it, which is why the factory data is committed first.
        """
        from tests.factories.principal import plaintext_token_for

        if self.use_real_db:
            if not self._session:
                return None
            from sqlalchemy import select

            from src.core.database.models import Principal

            self._commit_factory_data()
            row = self._session.scalars(
                select(Principal.principal_id).filter_by(
                    principal_id=self._principal_id,
                    tenant_id=self._tenant_id,
                )
            ).first()
            return plaintext_token_for(row) if row else None
        return plaintext_token_for(self._unit_principal().principal_id)

    def _unit_principal(self) -> Any:
        """The Principal the factory BUILT for this env (unit mode: no row, no session)."""
        from tests.factories.principal import PrincipalFactory

        key = (self._principal_id, self._tenant_id)
        if key not in self._unit_principals:
            self._unit_principals[key] = PrincipalFactory.build(
                principal_id=self._principal_id, tenant_id=self._tenant_id
            )
        return self._unit_principals[key]

    def _install_unit_resolver_substitutes(self) -> None:
        """Unit mode: substitute the resolver's three database reads, at their defining modules.

        The resolver's own logic -- Bearer parse, tenant detection, the AUTH_MISSING /
        AUTH_INVALID split, ``require_valid_token`` -- runs unmodified. Only the DATA is
        substituted, here, once. Each target is the module that DEFINES the function, which
        is what a function-local ``from x import y`` in production reads at call time:

        - ``src.core.config_loader.tenant_id_for`` returns ``None``: no host rows, so the
          ``x-adcp-tenant`` hint falls through to the literal id, as production does for an
          unknown subdomain. Read by ``_detect_tenant``.
        - ``src.core.auth_utils.get_principal_from_token`` answers the env principal for the
          env principal's token, scoped to the env's tenant (or unscoped), and nothing for
          anything else. Read by ``_resolve_identity``.
        - ``src.core.config_loader.get_tenant_by_id`` serves ``TenantFactory.make_tenant``
          with this env's overrides, so ``TenantContext.load`` builds the same tenant the
          env used to hand over pre-built. Read by the resolver.
        - ``src.core.resolved_identity._load_account`` answers an active schema Account
          named by the request's reference (``tests.helpers.capture_wrapper_req.stub_account_for``),
          so a request that names an account resolves without a database and the identity
          the resolver builds still carries one.

        Installed BEFORE ``EXTERNAL_PATCHES`` so an env that patches one of these itself
        keeps its own answer. Not entered into ``self.mock``: that dict is the env's own
        declared collaborators, the ones ``_configure_mocks`` wires; these are the harness's
        stand-ins for the database and no test configures them. Released through the same
        ``_guard`` registry as every patch, under ``resolver:`` labels.
        """
        from tests.factories import TenantFactory
        from tests.helpers.capture_wrapper_req import stub_account_for

        def _tenant_by_id(tenant_id: str) -> dict[str, Any]:
            return TenantFactory.make_tenant(tenant_id=tenant_id, **self._tenant_overrides)

        def _principal_from_token(token: str, tenant_id: str) -> Any:
            # Scoped to the tenant addressed, as the real lookup is.
            from src.core.credentials import hash_token
            from src.core.schemas import Principal as SchemaPrincipal

            principal = self._unit_principal()
            if hash_token(token) == principal.token_hash and tenant_id == self._tenant_id:
                return SchemaPrincipal.from_row(principal)
            return None

        for name, target, substitute in (
            ("tenant_id_for", "src.core.config_loader.tenant_id_for", lambda **_kw: None),
            ("get_principal_from_token", "src.core.auth_utils.get_principal_from_token", _principal_from_token),
            ("get_tenant_by_id", "src.core.config_loader.get_tenant_by_id", _tenant_by_id),
            ("_load_account", "src.core.resolved_identity._load_account", stub_account_for),
        ):
            patcher = patch(target, side_effect=substitute)
            patcher.start()
            self._guard(f"resolver:{name}", patcher.stop)

    def switch_principal(self, principal_id: str) -> None:
        """Re-point the env at *principal_id*.

        Public accessor for the principal-switch mutation (mirrors ``get_session()``):
        step functions must not reach into the private ``_principal_id``. The next
        ``credential()`` reads the new principal's token at dispatch time.
        """
        self._principal_id = principal_id

    def switch_tenant(self, tenant_id: str) -> None:
        """Re-point the env at *tenant_id*.

        Sibling of ``switch_principal``: step functions that seed a scenario into its own
        fresh tenant (isolation in the shared e2e_rest live DB) must not reach into the
        private ``_tenant_id``.
        """
        self._tenant_id = tenant_id

    @property
    def identity(self) -> ResolvedIdentity | PublicIdentity:
        """The caller ``call_impl`` hands the implementation. FOR ``call_impl`` ONLY.

        A direct ``_impl`` call takes an identity by definition; a wire leg has no
        parameter to receive one, it presents ``credential()`` and the resolver builds the
        identity. An env with a principal builds the ``ResolvedIdentity`` a protected tool
        takes; an env constructed with ``principal_id=None`` (a public tool's anonymous
        caller) builds a ``PublicIdentity``. Supports direct override via
        ``env._identity = ...`` for integration tests that create tenants in the DB and
        need a specific tenant context.
        """
        direct = self.__dict__.get("_identity")
        if direct is not None:
            return direct
        from tests.harness._identity import make_identity

        return make_identity(
            principal_id=self._principal_id,
            tenant_id=self._tenant_id,
            **self._tenant_overrides,
        )

    # -- Transport dispatch -------------------------------------------------

    def call_via(self, transport: Transport, **kwargs: Any) -> TransportResult:
        """Dispatch through *transport* and return normalized TransportResult.

        Presents this env's credential unless the caller passed ``credential=``
        (``{}`` sends no headers at all). Routes to the appropriate dispatcher.
        """
        from tests.harness.dispatchers import DISPATCHERS

        kwargs.setdefault("credential", self.credential())

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

    def response_parser(self, tool: str) -> Any:
        """The callable that turns a wire dict into this env's response object.

        An INSTANCE hook rather than a class attribute: two envs select the
        parser from request CONTENT (create_media_buy / update_media_buy), which
        a class attribute cannot express because it cannot bind ``self``.
        Receives ``**data`` — the shape ``_run_a2a_handler`` / ``_run_mcp_client``
        already call.

        Defaults to the tool's pinned response model. An env whose tool has no
        pinned model (create_media_buy, update_media_buy, sync_creatives,
        list_authorized_properties, sync_accounts, update_performance_index)
        MUST override this, or delivery would return payload=None on a
        SUCCESSFUL dispatch.
        """
        from tests.harness.spec_models import spec_response_model

        model = self.RESPONSE_MODEL or spec_response_model(tool)
        if model is None:
            raise NotImplementedError(
                f"{type(self).__name__} dispatches {tool!r}, which has no pinned response model; "
                "override response_parser() to name the parser explicitly"
            )
        return model

    def deliver_a2a(self, **kwargs: Any) -> DeliverResult:
        """Dispatch through the real A2A pipeline, returning payload AND wire.

        THE override point for A2A. The dispatchers call this and read both
        fields off the return value, so an env that needs custom routing,
        kwargs shaping or parser selection overrides HERE — at the frame that
        already owns those concerns — rather than re-implementing dispatch.
        """
        if not self.A2A_SKILL:
            raise NotImplementedError(
                f"{type(self).__name__} declares no A2A_SKILL and does not override deliver_a2a(). "
                "Set A2A_SKILL to enable Transport.A2A dispatch."
            )
        from tests.harness.transport import Transport

        return self._deliver_via_client(Transport.A2A, self.A2A_SKILL, kwargs)

    def call_a2a(self, **kwargs: Any) -> Any:
        """The parsed A2A payload. Defined ONCE; never override — override
        :meth:`deliver_a2a` instead, so the wire survives the call."""
        return self.deliver_a2a(**kwargs).payload

    @property
    def last_a2a_task(self) -> Any:
        """Raw A2A Task from the last ``_run_a2a_handler`` dispatch (or None).

        Public accessor for Task-level contract assertions — e.g. the submitted
        (manual-approval) contract, where state=TASK_STATE_SUBMITTED with NO
        artifacts IS the wire and the parsed response is a harness synthesis
        that cannot prove artifact absence.
        """
        return self._last_a2a_task

    def deliver_mcp(self, **kwargs: Any) -> DeliverResult:
        """Dispatch through the real FastMCP Client pipeline, returning payload AND wire.

        THE override point for MCP — see :meth:`deliver_a2a`.

        Note on enum coercion: FastMCP auto-coerces string values to enums when
        calling tools through the MCP protocol, so envs dispatching here need no
        manual coercion.
        """
        if not self.MCP_TOOL:
            raise NotImplementedError(
                f"{type(self).__name__} declares no MCP_TOOL and does not override deliver_mcp(). "
                "Set MCP_TOOL to enable Transport.MCP dispatch."
            )
        from tests.harness.transport import Transport

        return self._deliver_via_client(Transport.MCP, self.MCP_TOOL, kwargs)

    def _deliver_via_client(self, transport: Any, tool: str, kwargs: dict[str, Any]) -> DeliverResult:
        """Dispatch through THE one client core, then parse with this env's parser.

        This is The harness's single-dispatch invariant, made literal: ``AdCPTestClient`` is the
        implementation the env dispatch methods DELEGATE TO, not a peer beside
        them. Routing here means address resolution, request wrapping, delivery
        and error unwrapping have exactly one implementation for both the client
        and every env.

        The payload is re-parsed with this env's own ``response_parser`` rather
        than kept as the core's pinned-model parse: envs return their LOCAL
        response subclass, and ~34 call sites outside tests/harness depend on
        that type. The core still owns the DISPATCH; the env owns only how its
        own wire is typed.

        Errors are re-RAISED rather than returned, because the dispatchers'
        contract is that deliver_* raises and they translate — the core folds
        errors into a TransportResult, so unfolding it here keeps the exception
        (and the wire envelope stashed on it) flowing to the same handler.
        """
        from tests.harness.client import _dispatch_core
        from tests.harness.transport import NO_IDENTITY_OVERRIDE

        payload = dict(kwargs)
        credential = payload.pop("credential", NO_IDENTITY_OVERRIDE)
        result = _dispatch_core(self, transport, tool, payload, credential)
        if result.error is not None:
            raise result.error
        wire = result.wire_response
        if wire is None:
            return DeliverResult(payload=result.payload, wire_response=None)
        # The captured wire is deliberately UNSTRIPPED so envelope assertions can
        # see message/success; the response model has not declared them, so they
        # come off before validation.
        #
        # Through ``revive`` when the parser is a response model, because a SERVED document
        # carries the context the boundary stamped and the constructor refuses that field --
        # ``AdcpResponse`` makes ``_boundary._served`` the only thing that can put one on a
        # response, so ``parser(**wire)`` raised a ValidationError in the TEST process and the
        # scenario reported "no response arrived" for a request the seller answered fine.
        # ``revive`` is the reader-side door: it takes the field off the document, validates
        # the rest through the same refusing constructor, and re-attaches the value.
        parser = self.response_parser(tool)
        revive = getattr(parser, "revive", None)
        return DeliverResult(payload=revive(wire) if revive is not None else parser(**wire), wire_response=wire)

    def call_mcp(self, **kwargs: Any) -> Any:
        """The parsed MCP payload. Defined ONCE; never override — override
        :meth:`deliver_mcp` instead, so the wire survives the call."""
        return self.deliver_mcp(**kwargs).payload

    def _run_a2a_handler(
        self,
        skill_name: str,
        response_cls: type,
        **kwargs: Any,
    ) -> Any:
        """A2A dispatch via real AdCPRequestHandler — exercises full A2A pipeline.

        Dispatches through the real AdCPRequestHandler.on_message_send(), which
        exercises: message parsing → skill routing → ``serve`` → ``to_wire`` →
        Task/Artifact framing.

        The credential is presented where this transport reads headers: on the call
        context, the way the SDK's own context builder places the request headers. The
        handler reads only that; the real resolver answers.

        Args:
            skill_name: A2A skill name (e.g., "get_products").
            response_cls: Pydantic model class to parse artifact data into.
            **kwargs: Skill parameters. ``credential`` is popped and presented on the
                call context; ``a2a_push_notification_config`` is popped and sent as
                the protocol-level ``SendMessageConfiguration`` (see
                :func:`_a2a_send_message_configuration`) rather than as a skill
                parameter; remaining kwargs become skill parameters.
        """
        import asyncio

        from a2a.types import SendMessageRequest, Task

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
        from tests.harness.transport import NO_IDENTITY_OVERRIDE
        from tests.utils.a2a_helpers import create_a2a_message_with_skill, extract_data_from_artifact

        self._commit_factory_data()

        credential = kwargs.pop("credential", NO_IDENTITY_OVERRIDE)
        if credential is NO_IDENTITY_OVERRIDE:
            credential = self.credential()
        # Pop the protocol-level push config — it belongs on SendMessageRequest.
        # configuration, one level above the skill parameters (see
        # _a2a_send_message_configuration).
        protocol_push_config = kwargs.pop("a2a_push_notification_config", None)
        server_context = _a2a_call_context(credential)
        self._seed_ambient_tenant(credential)

        # Unpack req object into flat parameters if present.
        # A2A skills accept a flat parameter dict, not a request model.
        req = kwargs.pop("req", None)
        if req is not None and hasattr(req, "model_dump"):
            req_fields = req.model_dump(mode="json", exclude_none=True)
            parameters = {**req_fields, **kwargs}
        else:
            parameters = dict(kwargs)

        handler = AdCPRequestHandler()

        message = create_a2a_message_with_skill(skill_name=skill_name, parameters=parameters)
        if protocol_push_config is None:
            params = SendMessageRequest(message=message)
        else:
            params = SendMessageRequest(
                message=message,
                configuration=_a2a_send_message_configuration(protocol_push_config),
            )

        async def _call():
            return await handler.on_message_send(params, server_context)

        try:
            task_result = asyncio.run(_call())
        except Exception as exc:
            # The ORIGINAL exception propagates. It used to be translated into a
            # reconstructed AdCPSalesAgentError so callers could catch domain exceptions; the
            # dispatcher now reads the envelope off the A2AError instead
            # , and a genuine in-process production error --
            # which is what the IMPL path raises -- is unaffected either way.
            envelope = _a2a_wire_envelope(exc)
            if envelope is not None:
                raise WireError(envelope) from exc
            raise

        # Parse Task.artifacts[0] into response_cls
        if not isinstance(task_result, Task):
            raise TypeError(f"Expected Task, got {type(task_result).__name__}: {task_result}")

        # Expose the raw Task so tests can pin Task-level contract facts
        # (state, artifact absence) that the parsed response cannot prove.
        self._last_a2a_task = task_result

        # AdCP-domain errors now surface as a failed Task with the two-layer
        # envelope in the artifact DataPart. Reconstruct the AdCPSalesAgentError so
        # callers can catch domain exceptions instead of getting
        # a pydantic ValidationError from trying to parse the envelope as a
        # success response.
        from a2a.types import TaskState

        from src.core.errors.codes import AppErrorCode

        # Raised a few lines below and never imported -- the A2A-task-failed branch would
        # have died with a NameError instead of the error it means to raise.
        from src.core.exceptions import AdCPSalesAgentError

        if task_result.status.state == TaskState.TASK_STATE_FAILED:
            if task_result.artifacts:
                envelope = _wire_envelope(extract_data_from_artifact(task_result.artifacts[0]))
                if envelope is not None:
                    raise WireError(envelope)
            # A failed task with no envelope artifact: nothing was caught, so there is
            # no exception to hand to ``internal_detail``; the code is the diagnosis.
            raise AdCPSalesAgentError(error_code=AppErrorCode.INTERNAL_ERROR)

        if task_result.status.state == TaskState.TASK_STATE_SUBMITTED:
            # Async manual-approval path: the server returns a submitted Task with NO
            # artifacts (adcp_a2a_server.py:683) — the submitted envelope is conveyed by
            # the Task state + id, not an artifact union. Reconstruct the submitted wire
            # (protocol status="submitted" + the task_id the buyer polls) so success-path
            # grading sees the real A2A wire.
            submitted_wire = {"status": "submitted", "task_id": task_result.id}
            return DeliverResult(payload=response_cls(**submitted_wire), wire_response=dict(submitted_wire))

        if not task_result.artifacts:
            raise ValueError(f"Task has no artifacts. Status: {task_result.status}")
        artifact_data = extract_data_from_artifact(task_result.artifacts[0])
        # Surface the full, unstripped artifact DataPart as the real A2A wire for
        # success-path assertions. Captured BEFORE stripping so siblings that need
        # the top-level envelope fields (message/success) still see them.
        wire_response = dict(artifact_data)
        # Strip protocol fields the A2A wire adds in _dispatch_skill → to_wire (message, success).
        # These are populated by the protocol layer per the pin's Protocol
        # Envelope branch (see tests/helpers/adcp_schema_validator.py) — not
        # declared on the Pydantic response model — and cause ValidationError
        # under extra="forbid" in non-production mode.
        return DeliverResult(payload=response_cls(**artifact_data), wire_response=wire_response)

    def _run_mcp_client(
        self,
        tool_name: str,
        response_cls: type,
        **kwargs: Any,
    ) -> Any:
        """MCP dispatch via in-memory Client — exercises full FastMCP pipeline.

        Uses FastMCP's in-memory transport (FastMCPTransport) to go through the
        complete server path: middleware chain → TypeAdapter → tool function.

        The credential is presented where this transport reads headers:
        ``get_http_headers`` is patched to return it, so the full chain runs on every
        dispatch -- header extraction, tenant detection, token-to-principal lookup,
        ResolvedIdentity built by the resolver.

        Args:
            tool_name: MCP tool name (e.g., "get_products").
            response_cls: Pydantic model class to parse structured_content into.
            **kwargs: Tool arguments. ``credential`` is popped and presented as the
                request headers; ``req`` is popped and its fields unpacked into the
                arguments dict.
        """
        import asyncio
        from unittest.mock import patch

        from fastmcp import Client

        from src.core.main import mcp
        from tests.harness.transport import NO_IDENTITY_OVERRIDE

        self._commit_factory_data()

        credential = kwargs.pop("credential", NO_IDENTITY_OVERRIDE)
        if credential is NO_IDENTITY_OVERRIDE:
            credential = self.credential()

        # Unpack req object into flat arguments if present.
        # MCP tools accept individual params, not a request model.
        req = kwargs.pop("req", None)
        if req is not None and hasattr(req, "model_dump"):
            # Narrowed to the tool's own parameters -- the same "DTO fields INTERSECT
            # implementation arguments" rule production uses. The DTO is a SUPERSET of what
            # a given tool implements (GetMediaBuysRequest declares include_history,
            # pagination and more that get_media_buys does not take), so dumping it whole
            # sends arguments the tool never advertised. In dev, extra="forbid" turns that
            # into a VALIDATION_ERROR and the scenario fails for a reason it never intended
            # to test; in production it would be silently ignored, which is worse -- the
            # harness would be grading a request the buyer could not actually make.

            req_fields = req.model_dump(exclude_none=True)
            # No narrowing. This filtered req fields down to a hand-written wrapper's
            # parameter list -- "accepted = DTO fields INTERSECT wrapper parameters" -- which
            # is exactly the intersection the registry removed. The DTO IS the accepted shape
            # on every transport now, so a field the request carries is a field the tool
            # takes, and filtering could only drop one.
            # kwargs override req fields (explicit > implicit)
            arguments = {**req_fields, **kwargs}
        else:
            arguments = dict(kwargs)

        # ONE patch, at the source. src/core/main.py imports get_http_headers from
        # fastmcp.server.dependencies inside the call, so patching the DEFINING module is
        # what a function-local import actually sees, and it covers any further importer
        # for free. Patching an importing module instead named nothing once already.
        async def _call():
            with patch("fastmcp.server.dependencies.get_http_headers", return_value=dict(credential)) as patched:
                async with Client(mcp) as client:
                    result = await client.call_tool(tool_name, arguments)
                    assert patched.called, (
                        f"Auth chain not exercised for {tool_name} — get_http_headers was never called"
                    )
                    return DeliverResult(
                        payload=response_cls(**result.structured_content),
                        wire_response=result.structured_content,
                    )

        try:
            return asyncio.run(_call())
        except Exception as exc:
            envelope = _mcp_wire_envelope(exc)
            if envelope is not None:
                raise WireError(envelope) from exc
            raise

    def _pop_credential(self, kwargs: dict[str, Any]) -> dict[str, str]:
        """Pop ``credential`` from dispatch kwargs, defaulting to this env's credential.

        ``{}`` is a caller's explicit "no headers at all" and is returned as such; only
        an ABSENT keyword falls back to ``credential()``.
        """
        from tests.harness.transport import NO_IDENTITY_OVERRIDE

        credential = kwargs.pop("credential", NO_IDENTITY_OVERRIDE)
        return self.credential() if credential is NO_IDENTITY_OVERRIDE else dict(credential)

    def _run_rest_request(self, endpoint: str, **kwargs: Any) -> Any:
        """Shared REST dispatch: pop the credential -> commit -> build body -> POST.

        Symmetric with ``_run_mcp_client``. The credential rides the request as HTTP
        headers, where the production middleware reads it, so in-process REST runs the
        same chain as A2A, MCP and e2e_rest. There is no dependency override: a REST route
        has no identity dependency, so nothing can express an identity the wire never
        carried (GH #1886 was that seam disagreeing with the wire).

        Envs whose route is not a body-carrying POST override this method and reuse
        ``_pop_credential`` / ``get_rest_client``.
        """
        credential = self._pop_credential(kwargs)
        self._commit_factory_data()
        client = self.get_rest_client()
        body = self.build_rest_body(**kwargs)
        return client.post(endpoint, json=body, headers=credential)

    def call_rest(self, **kwargs: Any) -> Any:
        """Call the REST endpoint and parse the response.

        Symmetric with ``call_impl``, ``call_a2a``, ``call_mcp``.
        Presents the credential, POSTs, parses response.
        Raises on HTTP errors (dispatcher catches and wraps in TransportResult).
        """
        endpoint = self.REST_ENDPOINT  # type: ignore[attr-defined]
        response = self._run_rest_request(endpoint, **kwargs)

        if response.status_code >= 400:
            envelope = self.parse_rest_error_envelope(response.status_code, response.json())
            if envelope is not None:
                raise WireError(envelope)
            raise AssertionError(
                f"REST returned HTTP {response.status_code} with a body carrying no AdCP error code: "
                f"{response.text[:400]}"
            )

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
        """Parse a REST body into this env's ``RESPONSE_MODEL``, through ``revive``.

        Implemented here rather than refused here. This used to raise
        NotImplementedError, and nine envs answered it with one line of their own --
        ``SomeResponse(**data)`` -- which is the substituted-variable duplication the DRY
        rule forbids, and which every one of them got WRONG in the same way: a served
        document carries the ``context`` the boundary stamped, ``AdcpResponse`` refuses
        that field on construction so the boundary is the only thing that can put one
        there, and so every REST dispatch of a context-carrying request raised in the TEST
        process. The scenario then reported "no response arrived" for a request the seller
        had answered correctly. ``revive`` is the reader-side door for exactly that, and
        ``MediaBuyCreateEnv`` was already using it for the branch-resolution half of the
        same problem.

        An env whose tool has no single pinned response model declares no
        ``RESPONSE_MODEL`` and overrides this; the refusal below is what it used to be.

        ``revive`` only when the model HAS one. Some envs name the LIBRARY response type
        rather than a local subclass of ``AdcpResponse`` — ``CapabilitiesEnv`` does — and a
        library model carries no context refusal to work around, so plain construction is
        correct for it. Refusing instead cost 97 UC-010 failures in one run: measured as
        NEW between two remote runs, every one of them this frame raising
        NotImplementedError. The client-side twin (tests/harness/client.py
        ``_parse_pinned_response``) already had this fallback; the two now agree.
        """
        model = self.RESPONSE_MODEL
        if model is None:
            raise NotImplementedError(
                f"{type(self).__name__} declares no RESPONSE_MODEL and does not override "
                "parse_rest_response(). Do one or the other to enable Transport.REST."
            )
        revive = getattr(model, "revive", None)
        return cast("BaseModel", revive(data) if revive is not None else model(**data))

    def parse_rest_error_envelope(self, status_code: int, data: dict[str, Any]) -> dict[str, Any] | None:
        """The two-layer envelope from a REST error body, or ``None``.

        Shares ``_wire_envelope`` with the A2A and MCP paths so all three agree on
        what an error body means.

        The ``STATUS_TO_ERROR`` map that used to sit here -- 400 -> AdCPValidationError,
        404 -> AdCPNotFoundError, and five more -- is DELETED. It was the same design
        mistake as the code-to-class map in a second spelling: an HTTP status guessed
        back into an AdCP class. A status is not a code, and a guess is not evidence of
        what the buyer received. A body with no recoverable code
        yields ``None``, and the dispatcher reports the raw HTTP failure instead.
        """
        return _wire_envelope(data)

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

    def _seed_identity(self) -> None:
        """Seed the tenant + principal this env PRESENTS, so the seller it names exists.

        Discovery scenarios (list_creative_formats, get_products) never run a Given step
        that creates a tenant, and this used to run in e2e mode only, on the reasoning that
        in-process "they don't need one (identity is a mock)". That reasoning holds for a
        mocked resolver and not for a real database: a request naming a tenant no row
        matches is REFUSED (CONFIGURATION_ERROR), because a deployment that cannot tell
        which seller a request is for has no rule to apply. So every real-database env
        seeds, and an env addressing a phantom tenant is no longer a thing a test can do
        by accident.

        Delegates to the idempotent ``setup_default_data`` (get-or-create) so there is ONE
        seeding path and an env that calls it itself does not double-create. Seeds the SAME
        ``tenant_id`` / ``principal_id`` the env presents, so the token ``credential()``
        later reads is the seeded row's.
        """
        if not self._session:
            return
        # Only IntegrationEnv exposes setup_default_data; e2e mode is always
        # an IntegrationEnv (use_real_db=True), so this is the seeding path.
        setup = getattr(self, "setup_default_data", None)
        if setup is not None:
            setup()
            self._session.commit()

    def _seed_ambient_tenant(self, credential: dict[str, str]) -> None:
        """In-process A2A preamble: audit-log tenant row and the ambient tenant ContextVar.

        Both are keyed on the tenant the credential ADDRESSES (``x-adcp-tenant``), which is
        what the resolver will read; a credential addressing no tenant seeds nothing, so a
        scenario whose subject is tenant resolution gets the resolver's answer and not the
        harness's.

        The real A2A handler writes audit logs that need the tenant FK, so the row is
        created if absent (integration mode only). The ContextVar seed is the ambient
        channel salesagent-02rgd Phase 2 removes; until then it stays for the readers that
        have not moved to ``identity.tenant``. Not identity resolution: the resolver still
        detects the tenant from the headers itself.
        """
        tenant_id = _addressed_tenant(credential)
        if not tenant_id:
            return
        if self.use_real_db:
            self._ensure_tenant_for_audit(tenant_id)

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
        # The nested-env guard runs BEFORE the try: it must not unwind the OUTER
        # env's factories. Everything that ACQUIRES anything runs inside.
        if self.use_real_db:
            from tests.factories import ALL_FACTORIES

            for f in ALL_FACTORIES:
                assert f._meta.sqlalchemy_session is None, (
                    f"Factory {getattr(f, '__name__', type(f).__name__)} session already bound — "
                    "nested IntegrationEnv contexts are not supported"
                )

        try:
            # 0. Subclass setup that must precede the database and the mocks.
            self._enter_pre()

            # 1. Database setup (integration mode only). INSIDE the try: the
            #    engine and the session are resources, and a SASession(bind=...)
            #    failure used to leak the engine's connection pool.
            if self.use_real_db:
                from sqlalchemy.orm import Session as SASession

                from src.core.database.database_session import get_engine
                from tests.factories import ALL_FACTORIES

                # E2E mode connects directly to the specified database (the live
                # server's Postgres via e2e_config.postgres_url) instead of the
                # cached engine, so factory writes land in the DB the HTTP
                # server reads.
                if self._database_url:
                    from sqlalchemy import create_engine

                    from src.core.database.database_session import _pydantic_json_serializer

                    self._e2e_engine = create_engine(
                        self._database_url, echo=False, json_serializer=_pydantic_json_serializer
                    )
                    self._guard("db_engine", self._dispose_engine)
                    engine = self._e2e_engine
                else:
                    engine = get_engine()

                self._session = SASession(bind=engine)
                self._guard("db_session", self._close_session)

                for f in ALL_FACTORIES:
                    f._meta.sqlalchemy_session = self._session
                self._guard("db_factories", self._unbind_factories)

            # 2. Start patches. Unit mode first substitutes the resolver's three database
            #    reads, so the real chain runs over the in-process wire with no database.
            if not self.use_real_db:
                self._install_unit_resolver_substitutes()
            for name, target in self.EXTERNAL_PATCHES.items():
                if name in self.ASYNC_PATCHES:
                    patcher = patch(target, new_callable=AsyncMock)
                else:
                    patcher = patch(target)
                self.mock[name] = patcher.start()
                self._guard(f"patch:{name}", patcher.stop)

            self._configure_mocks()

            # 3. E2E discovery-path seeding: the live server authenticates against its own
            #    DB, so seed tenant/principal even for scenarios that never run a
            #    tenant-creating Given step. Idempotent.
            #
            #    SCOPED TO E2E ON PURPOSE. Seeding for every real-database env creates the
            #    default principal in EVERY test, and the factory derives a principal's
            #    token from its principal_id alone while `principals.token_hash` is
            #    globally unique -- so a test that then creates the same principal_id under
            #    its own tenant violates that constraint. Measured: 449 failures, all
            #    `duplicate key ... principals_token_hash_key`. A test that dispatches
            #    without creating a tenant calls `setup_default_data()` itself.
            if self.use_real_db and self.is_e2e:
                self._seed_identity()

            # 4. Subclass setup that needs the entered base and configured mocks.
            self._enter_post()
        except BaseException:
            self._unwind_partial_enter()
            raise

        return self

    # -- Subclass setup hooks ----------------------------------------------
    #
    # A subclass extends entry through these, never by overriding __enter__.
    # The reason is structural, not stylistic: a cooperative
    # ``super().__enter__()`` chain places a subclass's own setup OUTSIDE this
    # method's try by construction, whichever side of the super() call it sits
    # on. Every resource a hook acquires must be registered with :meth:`_guard`
    # on the line it is acquired.
    #
    # ``tests/harness/test_harness_base.py::test_harness_envs_define_no_enter_exit``
    # enforces that: __enter__/__exit__ (and their async twins) may be defined
    # only on BaseTestEnv and on AdminAccountEnv, which is not a BaseTestEnv.

    def _enter_pre(self) -> None:
        """Setup that must run before the database binding and the mocks.

        Overridden by e.g. ``LocalOriginMixin``, whose TLS origin must exist
        before ``_configure_mocks`` runs — ``CircuitBreakerEnv._configure_mocks``
        programs ``self.origin``.
        """

    def _enter_post(self) -> None:
        """Setup that needs the entered base and the configured mocks.

        Overridden by e.g. ``CircuitBreakerEnv``, which attaches a log handler
        once the env is otherwise live.
        """

    # -- The one cleanup registry ------------------------------------------

    def _guard(self, label: str, cleanup: Callable[[], None]) -> None:
        """Register *cleanup* to run on BOTH release paths, newest first.

        Call it on the line the resource is acquired. Anything acquired without
        a matching _guard survives a failed __enter__ for the rest of the
        process: Python does not call __exit__ when __enter__ raises, and the
        factory binding is GLOBAL. Measured before this registry existed: two
        real setup failures produced 350 further errors in one bdd_e2e run, and
        the two causes were indistinguishable in the report.
        """
        self._enter_cleanups.append((label, cleanup))

    def _release_entered(self, errors: list[Exception] | None) -> None:
        """Run every registered cleanup in REVERSE registration order, then clear.

        LIFO is deliberate and is a change from the pre-registry teardown, which
        released the database BEFORE the patches. Releasing in reverse
        acquisition order is the property that makes a partially-entered env
        safe, and no teardown here depends on a patch still being active.

        *errors* collects failures when the caller wants them (``__exit__``,
        which raises them as a group). ``None`` means best-effort and silent —
        the partial-enter path, where the caller is already raising and a
        cleanup detail must not replace the real cause.
        """
        for _label, cleanup in reversed(self._enter_cleanups):
            try:
                cleanup()
            except Exception as e:
                if errors is not None:
                    errors.append(e)
        self._enter_cleanups.clear()

    # Three cleanups, not one, and each registered on the line its resource is
    # acquired. A single "db" cleanup registered after all three acquisitions
    # left an already-created engine undisposed when SASession(bind=engine)
    # raised — exactly the pool leak of GH #1430, still open. Splitting also
    # fixes the second half: each runs in its own _release_entered try, so a
    # failure is COLLECTED into __exit__'s error list rather than swallowed by a
    # suppress() that head did not have. Registration order engine -> session ->
    # factories means LIFO release is factories -> session -> engine, which is
    # exactly the order the pre-registry __exit__ used.

    def _unbind_factories(self) -> None:
        from tests.factories import ALL_FACTORIES

        for f in ALL_FACTORIES:
            f._meta.sqlalchemy_session = None

    def _close_session(self) -> None:
        if self._session is not None:
            session, self._session = self._session, None
            session.close()

    def _dispose_engine(self) -> None:
        """Closing the session alone leaves its pool's connections open, and
        ~300 e2e envs per run accumulate toward the server's max_connections
        (GH #1430)."""
        if getattr(self, "_e2e_engine", None) is not None:
            engine, self._e2e_engine = self._e2e_engine, None
            engine.dispose()

    def _unwind_partial_enter(self) -> None:
        """Release whatever ``__enter__`` had acquired before it failed.

        Best-effort and SILENT by design, unlike ``__exit__``: the caller is
        already raising, and an error raised from here would replace the real
        cause with a cleanup detail. That is why ``_release_entered`` takes
        ``None`` on this path and an error list on the other.

        The GLOBAL state — the factory session binding — is released
        unconditionally at the end even if a registered cleanup misbehaved,
        because a leaked binding fails every later scenario on the worker.
        """
        self._release_entered(None)
        self.mock.clear()

        if self.use_real_db:
            with suppress(Exception):
                from tests.factories import ALL_FACTORIES

                for f in ALL_FACTORIES:
                    f._meta.sqlalchemy_session = None

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

        # 2. Release everything __enter__ registered, newest first. This covers
        #    the database (factory unbind / session close / engine dispose) and
        #    every patch, plus whatever the subclass hooks acquired.
        self._release_entered(errors)
        self.mock.clear()

        if errors:
            if len(errors) == 1:
                raise errors[0]
            raise ExceptionGroup("Multiple teardown errors", errors)
        return False

    @property
    @realize_e2e(_e2e_external_seams_exercised)
    def external_seams_exercised(self) -> bool:
        """Whether production actually ran through its external seams for this call.

        The question a sandbox scenario asks after the response is back: did the seller
        really do the work, or skip every outside call and hand back a stub? A response
        alone cannot answer it, which is why BR-RULE-209's "no real ad platform API
        calls" Then asks this as well as reading ``sandbox`` off the wire.

        In process the patched seam IS the observable: every external integration point
        is a mock, and one of them being called is production having reached it.

        Over e2e_rest those mocks live in the WRONG PROCESS -- the work happens inside the
        Docker server, nothing here is patched, and ``any(mock.called)`` is False for
        every scenario. That is the shape 97608a6fa fixed for UC-006's four mock-reading
        Thens: the env owns the answer and each branch reads the observable that exists in
        its own world. Here the live one is the audit row the server writes
        (:func:`_e2e_external_seams_exercised`).

        DECLARED ON ``BaseTestEnv``, NOT ``IntegrationEnv``, and that placement is the
        fix for a near-miss rather than a preference. NOTE: the caller that motivated it,
        the generic ``then_no_real_api_calls`` step, is DELETED -- its obligation has no
        wire observable and production violates it (see that step's removal note in
        tests/bdd/steps/generic/then_success.py), so this property currently has no
        caller. It is kept here, at the base, because the original reasoning holds for
        any future one: Five env classes in
        ``tests/harness/`` extend ``BaseTestEnv`` directly (the ``*_unit.py`` variants of
        ProductEnv / DeliveryPollEnv / WebhookEnv / CircuitBreakerEnv, plus
        MediaBuyUpdateEnv), and on ``IntegrationEnv`` this property was an
        ``AttributeError`` waiting for the first route that sent one of them through the
        step. Here every env has it.

        The e2e branch needs ``get_audit_logs``, which only ``IntegrationEnv`` can offer,
        and that asymmetry is sound: ``is_e2e`` keys on ``e2e_config``, a unit-mode env is
        built without one, so the branch that needs a session is unreachable from the envs
        that have none. If that ever stops being true the AttributeError is the right,
        loud answer.
        """
        return any(mock.called for mock in self.mock.values())


class IntegrationEnv(BaseTestEnv):
    """Integration test environment — real database, only mocks external services.

    Requires ``integration_db`` pytest fixture.
    Supports REST dispatch via FastAPI TestClient.
    """

    use_real_db = True
    #: TestClient(app, raise_server_exceptions=...) for get_rest_client(). True
    #: preserves every existing REST test's behavior unchanged — provably a
    #: no-op for every typed error path (AdCPSalesAgentError/ValueError/
    #: RequestValidationError/PermissionError/ToolError each have their own
    #: @app.exception_handler and never reach ServerErrorMiddleware, the only
    #: place this flag matters). inject_untyped_exception() sets this to False
    #: as an INSTANCE attribute (not by overriding this class default) so the
    #: opt-out is scoped to exactly the scenario that calls it.
    REST_RAISE_SERVER_EXCEPTIONS: bool = True

    def setup_default_data(self, **tenant_kwargs: Any) -> tuple[Any, Any]:
        """Get-or-create default tenant + principal via factories.

        Must be called inside the ``with env:`` block (factories are bound
        to the session during ``__enter__``).

        Returns (tenant, principal) ORM instances. Uses self._tenant_id
        and self._principal_id from constructor. Idempotent: reuses existing
        rows rather than re-creating, so it is safe to call after the e2e
        ``__enter__`` auto-seed (``_seed_identity``) already created them.

        Extra ``tenant_kwargs`` are tenant policy columns the live e2e_rest
        server reads from the shared DB (e.g. ``human_review_required``).
        Forwarded to ``TenantFactory`` on the create path; APPLIED to the
        existing row on the get path — the __enter__ auto-seed creates the
        tenant with model defaults, so the kwargs must win over those defaults
        regardless of which call created the row.
        """
        from sqlalchemy import select

        from src.core.database.models import Principal, Tenant
        from tests.factories import PrincipalFactory, TenantFactory

        tenant = self._session.scalars(select(Tenant).filter_by(tenant_id=self._tenant_id)).first()
        if tenant is None:
            tenant = TenantFactory(tenant_id=self._tenant_id, **tenant_kwargs)
        elif tenant_kwargs:
            for column, value in tenant_kwargs.items():
                setattr(tenant, column, value)
            self._commit_factory_data()

        principal = self._session.scalars(
            select(Principal).filter_by(tenant_id=self._tenant_id, principal_id=self._principal_id)
        ).first()
        if principal is None:
            principal = PrincipalFactory(tenant=tenant, principal_id=self._principal_id)

        # NO account is seeded here, deliberately. Seeding one for every tenant made
        # UC-011's account-LISTING scenarios wrong -- "0 accounts visible" saw one -- which
        # is the cost of a default that is invisible at the call site. The tools that
        # REQUIRE an account seed it where they build the request instead: see
        # MediaBuyCreateEnv._ensure_required_request_fields / _seed_named_account, the BDD
        # request defaults, and MediaBuyFactory.
        return tenant, principal

    def setup_default_account(self, principal_id: str | None = None) -> Any:
        """Get-or-create the default Account (plus this principal's access to it).

        AdCP 3.1.1 makes ``account`` REQUIRED on several requests (sync-creatives-request
        and update-media-buy-request both list it in /required), so a scenario that does
        not seed one cannot build a valid request at all -- it fails on a missing field
        before reaching the behaviour it means to grade.

        Must be called inside the ``with env:`` block, and it calls
        ``setup_default_data`` first: the Account row carries a tenant_id FK, so seeding
        it against a tenant that does not exist yet is the FK violation this method
        exists to make unreachable.

        Idempotent, like ``setup_default_data`` -- reuses an existing row so repeated
        Given steps do not collide.
        """
        tenant, principal = self.setup_default_data()
        # Access is granted to the principal that will actually SEND the request, which is
        # not always the env's default: a cross-principal isolation test drives a second
        # principal, and an account its principal cannot reach comes back as
        # AdCPAuthorizationError rather than the behaviour under test.
        grantee = principal_id or principal.principal_id
        return self._seed_default_account(tenant, grantee)

    def _seed_default_account(self, tenant: Any, grantee: str) -> Any:
        """The body of ``setup_default_account``, callable from ``setup_default_data`` too.

        Split out so the tenant seeder can seed an account without calling
        ``setup_default_account``, which starts by calling the tenant seeder -- the two
        would otherwise recurse.
        """
        from sqlalchemy import select

        from src.core.database.models import Account, AgentAccountAccess
        from tests.factories.account import AccountFactory, AgentAccountAccessFactory

        account = self._session.scalars(select(Account).filter_by(tenant_id=self._tenant_id)).first()
        if account is None:
            # tenant_id, never tenant= -- Account.tenant is a real relationship, so handing
            # it a SubFactory's throwaway Tenant makes SQLAlchemy re-sync tenant_id FROM
            # that object at flush and silently relocate the row (see AccountFactory.Meta).
            # A DETERMINISTIC id, not the factory Sequence: tests name this account by
            # literal (``{"account_id": "acct_test"}``) in ~120 request constructions, and a
            # sequence id would parse in all of them and resolve in none.
            account = AccountFactory(tenant_id=tenant.tenant_id, account_id=DEFAULT_TEST_ACCOUNT_ID)

        # Only a principal that EXISTS can be granted access: agent_account_access carries
        # an FK to principals, and several scenarios drive a deliberately unknown identity
        # (tenant-not-found, unauthenticated) whose principal has no row. For those the
        # grant is skipped -- the account still exists so the request is well-formed, and
        # the scenario reaches the auth rejection it is actually about.
        from src.core.database.models import Principal

        grantee_exists = (
            self._session.scalars(select(Principal).filter_by(tenant_id=self._tenant_id, principal_id=grantee)).first()
            is not None
        )
        access = self._session.scalars(
            select(AgentAccountAccess).filter_by(
                tenant_id=self._tenant_id,
                principal_id=grantee,
                account_id=account.account_id,
            )
        ).first()
        if access is None and grantee_exists:
            AgentAccountAccessFactory(
                tenant_id=tenant.tenant_id,
                principal_id=grantee,
                account_id=account.account_id,
            )
        self._commit_factory_data()
        return account

    def default_account_reference(self) -> Any:
        """The seeded account as the AccountReference a request field wants.

        core/account-ref.json is a oneOf: {account_id} or {brand, operator, sandbox?}.
        The account_id form is the one a seeded row can satisfy exactly, so steps get
        that rather than reconstructing a brand/operator pair the DB may not agree with.
        """
        from adcp.types import AccountReference

        return AccountReference(root={"account_id": self.setup_default_account().account_id})

    # Seeding a NAMED account reference lives here rather than on one env: any env whose
    # tool carries an ``account`` needs it, and update_media_buy needed it the moment the
    # boundary started RESOLVING the reference instead of accepting and dropping it.
    def _seed_named_account_ref(self, account: Any) -> None:
        """Seed the row behind an account reference, when it is the suite's default.

        Takes the reference in either spelling -- the wire dict a per-field caller passes,
        or the typed AccountReference on a built request -- because both paths reach the
        same boundary lookup.
        """
        root = getattr(account, "root", account)
        account_id = root.get("account_id") if isinstance(root, dict) else getattr(root, "account_id", None)
        if account_id == DEFAULT_TEST_ACCOUNT_ID:
            self.setup_default_account()

    def _seed_named_account(self, req: Any) -> None:
        """Seed the account a caller-BUILT request names, when it is the suite's default.

        A test that hands ``req=`` built its request outside this env, so
        ``_ensure_required_request_fields`` never ran and nothing created the row the
        transport boundary is about to resolve. Only DEFAULT_TEST_ACCOUNT_ID is seeded: a
        test naming its own account is describing a specific account state (missing,
        suspended, foreign) and manufacturing a row for it would erase the case.
        """
        account = getattr(req, "account", None)
        if account is not None:
            self._seed_named_account_ref(account)

    def configure_tenant_field(self, field: str, value: Any) -> None:
        """Write a tenant-level config field for both caller paths.

        Updates the in-memory tenant overrides (the ``identity`` a ``call_impl`` hands
        over, and the unit-mode ``get_tenant_by_id`` substitute) AND the DB Tenant row
        when the column exists (the wire legs' resolver reads the DB via config_loader).
        """
        self._tenant_overrides[field] = value

        if self._session:
            from src.core.database.models import Tenant

            tenant = self._session.get(Tenant, self._tenant_id)
            if tenant is not None and hasattr(tenant, field):
                setattr(tenant, field, value)
                self._session.commit()

    # -- Public query API (step functions must use these, not env._session) ----

    def get_session(self) -> Session:
        """Return the env-bound SQLAlchemy session for read-back assertions.

        Public accessor so step functions never reach into the private
        ``_session`` attribute. Only valid inside the ``with env:`` block.
        """
        if self._session is None:
            raise RuntimeError(
                f"{type(self).__name__}.get_session() called without an active session — "
                "use it inside a 'with env:' block (integration mode)."
            )
        return self._session

    def query(self, model: type, **filters: Any) -> list:
        """Return all rows of ``model`` matching ``filters`` via the bound session."""
        from sqlalchemy import select

        return list(self.get_session().scalars(select(model).filter_by(**filters)).all())

    def get_one(self, model: type, **filters: Any) -> Any:
        """Return the first row of ``model`` matching ``filters``, or ``None``."""
        from sqlalchemy import select

        return self.get_session().scalars(select(model).filter_by(**filters)).first()

    def get_workflow_steps(self) -> list:
        """Return WorkflowStep rows scoped to this env's tenant.

        WorkflowStep has no tenant_id column; tenant scoping is via its Context
        relationship, so this joins WorkflowStep -> Context and filters on
        ``Context.tenant_id``.
        """
        from sqlalchemy import select

        from src.core.database.models import Context, WorkflowStep

        stmt = select(WorkflowStep).join(WorkflowStep.context).where(Context.tenant_id == self._tenant_id)
        return list(self.get_session().scalars(stmt).all())

    def get_audit_logs(self, operation_substring: str | None = None) -> list:
        """``AuditLog`` rows this tenant accumulated, oldest first.

        The audit trail is a DATABASE table by design -- "the database is the audit
        authority" (src/core/audit_logger.py) and the files beside it are a backup -- so
        the rows are readable on every transport, including the one where the audit
        logger itself runs in another process.

        Three step helpers in ``tests/bdd/steps/_outcome_helpers.py`` already CALLED
        ``env.get_audit_logs`` on their e2e branch (``assert_audit_logged``,
        ``assert_audit_approval_logged``, ``assert_audit_adapter_logged``) and no such
        method existed: those branches raised ``AttributeError``, unnoticed because no
        e2e_rest node has reached one of them yet. This is the method they were written
        against, not a second spelling of it.

        ``expire_all`` first: over e2e the server committed through its OWN session, so a
        row written after this session last read is otherwise served from the identity
        map -- the same reason ``creative_sync``'s and ``webhook_registration``'s
        read-backs expire.

        ``operation_substring`` matches against ``AuditLog.operation``, which production
        stores adapter-prefixed (``AdCP.list_creatives``), so a caller naming the bare
        tool name still matches.
        """
        from sqlalchemy import select

        from src.core.database.models import AuditLog

        session = self.get_session()
        session.expire_all()
        stmt = select(AuditLog).filter_by(tenant_id=self._tenant_id).order_by(AuditLog.timestamp)
        rows = list(session.scalars(stmt).all())
        if operation_substring is None:
            return rows
        return [row for row in rows if operation_substring in (row.operation or "")]

    def get_rest_client(self) -> Any:
        """Return the FastAPI TestClient. NO auth dependency override.

        There is nothing to override. The identity is resolved once, inside
        ``invoke_tool``, from the credential on the request, so a REST route has no
        identity dependency. With an override in place every REST request ran as a
        pre-built identity handed in by the test, so the header -> ``_detect_tenant`` ->
        tenant-scoped principal lookup chain never executed and a scenario could not tell a
        correct resolution from a broken one. The request carries ``credential()`` as its
        headers and the server resolves it, the same way MCP and A2A do.
        """
        if self._rest_client is None:
            from starlette.testclient import TestClient

            from src.app import app

            self._rest_client = TestClient(app, raise_server_exceptions=self.REST_RAISE_SERVER_EXCEPTIONS)

        return self._rest_client


class BareIntegrationEnv(IntegrationEnv):
    """Integration env with no external patches — for repository-level tests.

    Repository tests exercise the data layer directly: they need the real
    database session and factory binding ``IntegrationEnv`` provides, but none
    of the adapter/notifier mocks. ``get_session()`` commits any pending
    factory data and exposes the session for direct repository construction.
    """

    EXTERNAL_PATCHES: dict[str, str] = {}

    def get_session(self) -> Any:
        """Commit pending factory data and expose the session."""
        self._commit_factory_data()
        return self._session
