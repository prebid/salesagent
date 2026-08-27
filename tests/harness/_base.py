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

import os
import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Self
from unittest.mock import AsyncMock, MagicMock, patch

from tests.harness._realize import realize_e2e

#: Where an IN-PROCESS outbound webhook is addressed. A fixed public URL: the POST
#: is intercepted before it leaves the process, so the address only has to survive
#: production's SSRF gate. The e2e answer is a capture-origin key — see
#: :meth:`BaseTestEnv.webhook_destination`.
IN_PROCESS_WEBHOOK_URL = "https://buyer.example.com/webhook"

# The MCP transport boots the real FastMCP app lifespan, which starts the
# background schedulers. Those run a batch immediately on the *real* wall clock
# and rewrite media-buy status rows — silently mutating data a test just seeded
# (e.g. promoting a seeded pending_start buy to active). Suppress them for all
# harness-driven tests; setdefault so an explicit override still wins.
# (src.core.main._background_schedulers_enabled reads this at lifespan runtime.)
os.environ.setdefault("ADCP_RUN_BACKGROUND_SCHEDULERS", "false")

if TYPE_CHECKING:
    from pydantic import BaseModel
    from sqlalchemy.orm import Session

    from src.core.resolved_identity import ResolvedIdentity
    from tests.harness.transport import E2EConfig, Transport, TransportResult
    from tests.helpers.signing import SignatureRealization


def _adcp_error_from_code(
    error_code: str,
    message: str,
    recovery: str | None = None,
    details: dict | None = None,
    suggestion: str | None = None,
    field: str | None = None,
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
        AdCPAuthRequiredError,
        AdCPBudgetExhaustedError,
        AdCPBudgetTooLowError,
        AdCPCapabilityNotSupportedError,
        AdCPConflictError,
        AdCPError,
        AdCPIdempotencyConflictError,
        AdCPIdempotencyExpiredError,
        AdCPMediaBuyNotFoundError,
        AdCPNotFoundError,
        AdCPPackageNotFoundError,
        AdCPRateLimitError,
        AdCPServiceUnavailableError,
        AdCPValidationError,
    )

    # Read class-level identity from the _default_error_code ClassVar slot
    # (option-A refactor per salesagent-fnk9). error_code is an instance
    # attribute set in __init__; reading it off the class would return the
    # descriptor, not the wire code string.
    _CODE_TO_CLASS: dict[str, type[AdCPError]] = {
        cls._default_error_code: cls
        for cls in (
            AdCPValidationError,
            AdCPAuthenticationError,
            AdCPAuthRequiredError,
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
            # Substrate subclasses with production raise sites — the harness
            # reconstructs the specific subclass after a roundtrip (preserves
            # type for isinstance() checks in tests). Codes with only
            # advisory-on-success Pattern A construction (BUDGET_EXCEEDED,
            # CREATIVE_REJECTED, PRODUCT_UNAVAILABLE) round-trip via the
            # base AdCPError fallback below and don't need a dedicated class.
            AdCPMediaBuyNotFoundError,
            AdCPPackageNotFoundError,
            AdCPBudgetTooLowError,
            AdCPCapabilityNotSupportedError,
            AdCPIdempotencyConflictError,
            AdCPIdempotencyExpiredError,
        )
    }
    # AUTH_MISSING -> AdCPAuthRequiredError and AUTH_INVALID -> AdCPAuthenticationError
    # are unambiguous per v3.1.1 error-code.json (salesagent-mkso) — each class's
    # own _default_error_code disambiguates them via the dict comprehension above.
    # AUTH_REQUIRED (deprecated alias) is no longer emitted by any subclass
    # (salesagent-otc5 migrated AdCPAuthorizationError to PERMISSION_DENIED and
    # split the former tenant-axis raises across AUTH_MISSING/AUTH_INVALID).
    # PERMISSION_DENIED is not in the class list above, so it still falls
    # through to the base AdCPError below — matching prior behavior for
    # AdCPAuthorizationError (no test currently needs isinstance() on it via
    # wire reconstruction).
    from src.core.exceptions import INTERNAL_CODES

    assert error_code not in INTERNAL_CODES, (
        f"INTERNAL code {error_code!r} reached harness reconstruction — production wire leaked an internal-only code"
    )
    exc_cls = _CODE_TO_CLASS.get(error_code, AdCPError)
    reconstructed = exc_cls(
        message=message,
        details=details,
        recovery=recovery or "terminal",
        suggestion=suggestion,
        field=field,
    )
    if exc_cls is AdCPError:
        reconstructed.error_code = error_code
    return reconstructed


def _unwrap_mcp_tool_error(exc: Exception) -> Exception:
    """Translate FastMCP ToolError back to the corresponding AdCPError.

    The MCP boundary translator raises ``AdCPToolError`` (single-arg JSON
    envelope) so FastMCP serializes ``str(exc)`` as the JSON-encoded two-layer
    error envelope. This unwrapper parses that JSON and reconstructs the
    matching AdCPError subclass.

    Falls back to legacy tuple-string parsing for any plain ``ToolError`` that
    might be raised by code paths outside the MCP boundary translator (these
    are rare and shrink over time per the architecture cleanup).

    If the exception is not a ToolError or can't be parsed, returns it unchanged.
    """
    import ast
    import json

    from fastmcp.exceptions import ToolError

    if not isinstance(exc, ToolError):
        return exc

    error_str = str(exc)

    # New shape: single-arg JSON envelope — delegate to shared helper.
    try:
        envelope = json.loads(error_str)
        if isinstance(envelope, dict):
            reconstructed = _envelope_to_adcp_error(envelope)
            if reconstructed is not None:
                return reconstructed
    except (json.JSONDecodeError, TypeError):
        pass

    # Legacy shape (test fixtures that mock ToolError directly):
    # tuple-stringified `('CODE', 'message', 'recovery', '{"details": ...}')`.
    # Deprecated: legacy ToolError tuple-string parsing — remove when no test fixtures depend on raw ToolError raises.
    try:
        parsed = ast.literal_eval(error_str)
        if isinstance(parsed, tuple) and len(parsed) >= 2:
            error_code = str(parsed[0])
            message = str(parsed[1])
            recovery = str(parsed[2]) if len(parsed) > 2 else None

            # 4th element is a JSON-serialized extra blob that may contain
            # "details", "suggestion", and "field" as separate top-level keys
            # (packed by tool_error_logging._translate_to_tool_error).
            details = None
            suggestion = None
            field = None
            if len(parsed) > 3 and parsed[3] is not None:
                try:
                    extra = json.loads(str(parsed[3]))
                    if isinstance(extra, dict):
                        details = extra.get("details")
                        suggestion = extra.get("suggestion")
                        field = extra.get("field")
                except (json.JSONDecodeError, TypeError):
                    pass

            return _adcp_error_from_code(error_code, message, recovery, details, suggestion, field)
    except (ValueError, SyntaxError):
        pass

    # Fallback: try extract_error_info (handles ToolError("message") single-arg form)
    from src.core.tool_error_logging import extract_error_info

    error_code, message, recovery = extract_error_info(exc)
    if error_code != "TOOL_ERROR":
        return _adcp_error_from_code(error_code, message, recovery)

    return exc


def _envelope_to_adcp_error(envelope: dict, fallback_message: str = "") -> Exception | None:
    """Reconstruct an AdCPError subclass from a two-layer envelope dict.

    Accepts the envelope shape produced by ``build_two_layer_error_envelope``:
    ``{"adcp_error": {code, message, recovery, details, ...}, "errors": [...], ...}``.
    Also accepts the legacy flat shape ``{"error_code": ..., "recovery": ...}``
    for tests that predate the envelope.

    Single source of truth for envelope→exception reconstruction — called by
    ``_unwrap_a2a_server_error`` (A2AError.data path) and
    ``BaseTestEnv.parse_rest_error`` (REST response body path). Returns the
    reconstructed ``AdCPError`` subclass, or ``None`` if no ``error_code`` can
    be extracted (caller picks a fallback).
    """
    if not isinstance(envelope, dict):
        return None
    error_code: str | None = None
    message = fallback_message
    recovery: str | None = None
    details: dict | None = None
    suggestion: str | None = None
    field: str | None = None
    adcp_err = envelope.get("adcp_error")
    if isinstance(adcp_err, dict):
        error_code = adcp_err.get("code")
        message = adcp_err.get("message", message) or message
        recovery = adcp_err.get("recovery")
        details = adcp_err.get("details")
        suggestion = adcp_err.get("suggestion")
        field = adcp_err.get("field")
    errors = envelope.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        first = errors[0]
        error_code = error_code or first.get("code")
        message = first.get("message", message) or message
        recovery = recovery or first.get("recovery")
        details = details or first.get("details")
        suggestion = suggestion or first.get("suggestion")
        field = field or first.get("field")
    if not error_code:
        return None
    reconstructed = _adcp_error_from_code(error_code, message, recovery, details, suggestion, field)
    if reconstructed is not None:
        # Stash the REAL wire envelope on the reconstructed exception so the
        # A2A/REST dispatchers can capture the actual wire bytes (artifact
        # DataPart for A2A, HTTP body for REST) rather than re-synthesizing
        # via build_two_layer_error_envelope — re-synthesis would just
        # regenerate from the lossy reconstructed exception. Read by
        # ``A2ADispatcher.dispatch`` via ``getattr(exc, '_wire_error_envelope', None)``.
        reconstructed._wire_error_envelope = envelope  # type: ignore[attr-defined]
    return reconstructed


def _unwrap_a2a_server_error(exc: Exception) -> Exception:
    """Translate a2a A2AError back to the corresponding AdCPError.

    The A2A dispatcher wraps AdCPError into a failed Task whose artifact
    carries the two-layer envelope. If the exception is a JSON-RPC-level
    A2AError (e.g., from the dispatcher's own catch-all), the ``data``
    field carries the envelope.

    If the exception is not an A2AError or lacks enough info, returns it unchanged.
    """
    from a2a.types import InternalError, InvalidParamsError, InvalidRequestError
    from a2a.utils.errors import A2AError

    if not isinstance(exc, A2AError):
        return exc

    # a2a-sdk 1.0: the exception itself carries message/data (no .error wrapper)
    message = getattr(exc, "message", str(exc))
    data = getattr(exc, "data", None) or {}

    reconstructed = _envelope_to_adcp_error(data, fallback_message=message) if isinstance(data, dict) else None
    if reconstructed is not None:
        return reconstructed

    from src.core.exceptions import (
        AdCPAuthenticationError,
        AdCPValidationError,
    )

    if isinstance(exc, InvalidRequestError):
        return AdCPAuthenticationError(message)
    if isinstance(exc, InvalidParamsError):
        return AdCPValidationError(message)
    if isinstance(exc, InternalError):
        return RuntimeError(message)
    return exc


# ---------------------------------------------------------------------------
# The JSON-RPC wire, for the A2A and MCP legs that go over real HTTP
# (salesagent-n78j0.1.1). Kept module-level and transport-named so the env keeps
# ONE seam per leg — build the DTO, hand it to ``wire_request``, POST it.
# ---------------------------------------------------------------------------

#: The A2A JSON-RPC route on ``src.app.app``. NO trailing slash: ``/a2a/`` is a
#: 307 redirect, and httpx replays a redirect with the ORIGINAL signature, which
#: then covers the wrong ``@target-uri`` and is refused as
#: ``request_signature_invalid`` — a fixture bug wearing a verifier bug's clothes.
_A2A_PATH = "/a2a"

#: The MCP streamable-HTTP endpoint. WITH the trailing slash, same reason: the
#: mount answers ``/mcp`` with a 307 to ``/mcp/``.
_MCP_PATH = "/mcp/"

#: MCP streamable HTTP requires the client to accept BOTH renderings; the server
#: refuses the POST outright ("Not Acceptable") when either is missing.
_MCP_ACCEPT = "application/json, text/event-stream"

#: WHERE the webhook credentials of an OPERATION request sit on each transport —
#: the location ``call_via``'s own dispatch already puts them, named so a refusal
#: (or an acceptance) can be attributed to a place rather than to "the request".
#: Keyed by ``Transport.value`` because ``Transport`` is a TYPE_CHECKING-only
#: import here. Each entry is where PRODUCTION reads the config on that transport,
#: which is genuinely not the same place — see ``_a2a_message_send_body``.
_OPERATION_CREDENTIAL_LOCATION: dict[str, str] = {
    "a2a": "message/send params.configuration.task_push_notification_config",
    "mcp": "the tools/call arguments' push_notification_config",
    "rest": "the AdCP request body's push_notification_config",
    "e2e_rest": "the AdCP request body's push_notification_config",
}

#: The label for a transport whose operation-payload location is not tabulated
#: above. Deliberately vague: a location that has not been stated is not one a
#: failure message may name precisely.
_UNSTATED_CREDENTIAL_LOCATION = "the operation request's own payload"

#: Where ``declare_request_signing`` parks the signature-failure counts it saw
#: BEFORE this seller had a posture. ``BaseTestEnv.signature_failures`` subtracts
#: them, which is what makes its answer a claim about THIS env's requests: the
#: failure counter carries no run-identifying label, so nothing else can scope it.
_SIGNATURE_FAILURE_WINDOW = "_signature_failure_window"


def _by_signature_code(samples: dict[tuple[tuple[str, str], ...], float]) -> dict[str, float]:
    """Counter samples keyed by their ``code`` label, SUMMED rather than overwritten.

    One code appears under several label sets — the same rule refuses several
    operations — so keeping the last one seen would silently drop every earlier
    sample and report a delta of zero for a mechanism that ran.

    Shared by both branches of ``_signature_failure_counts``: the in-process registry
    read and the scraped exposition return the same shape by design
    (:func:`tests.helpers.signing.scraped_counter_samples`), and folding them the same
    way is what keeps the two branches answering the same question.
    """
    out: dict[str, float] = {}
    for labels, value in samples.items():
        code = dict(labels).get("code", "")
        out[code] = out.get(code, 0.0) + value
    return out


#: The A2A JSON-RPC method a buyer registers webhook credentials with WITHOUT
#: invoking any skill — the SECOND credential location this transport carries.
#: Served by the pinned a2a SDK's v0.3 compat adapter
#: (``a2a/compat/v0_3/jsonrpc_adapter.py`` ``METHOD_TO_MODEL``) and answered by
#: OUR OWN handler, ``AdCPRequestHandler.on_create_task_push_notification_config``
#: (``src/a2a_server/adcp_a2a_server.py``), which persists the credentials it is
#: given. Not a hypothetical surface: it is live, implemented and reachable.
_A2A_PUSH_CONFIG_SET = "tasks/pushNotificationConfig/set"

#: How a failure names the location above.
_A2A_PUSH_CONFIG_SET_LOCATION = f"the {_A2A_PUSH_CONFIG_SET} params"


def _a2a_message_send_body(
    skill_name: str, parameters: dict[str, Any], push_notification_config: Any = None
) -> dict[str, Any]:
    """The ``message/send`` JSON-RPC envelope naming *skill_name* explicitly.

    Built from the a2a-sdk's own v0.3 request model — the SAME model the server's
    compat adapter validates the body against
    (``a2a/compat/v0_3/jsonrpc_adapter.py`` ``METHOD_TO_MODEL``) — rather than a
    hand-rolled dict, so a field the SDK renames or requires cannot silently
    diverge here. Explicit-skill invocation is the ``data`` part shape
    ``{"skill": ..., "parameters": ...}`` (``src/a2a_server/adcp_a2a_server.py``),
    which is also what ``src/core/signing/operations.py`` names the operation from.

    A ``push_notification_config`` travels in the A2A PROTOCOL ENVELOPE, not in the
    skill parameters, because that is where PRODUCTION reads it on this transport:
    ``on_message_send`` takes it from
    ``params.configuration.task_push_notification_config``
    (``src/a2a_server/adcp_a2a_server.py:657-659``) and threads it into the skill
    handler. Putting it in the parameters instead would be the harness choosing a
    different registration channel than the one an A2A buyer uses — and would make
    a webhook-registration scenario grade a payload shape nobody sends.
    """
    import uuid

    from a2a.compat.v0_3 import types as v03

    configuration = None
    if push_notification_config is not None:
        configuration = v03.MessageSendConfiguration(
            push_notification_config=_a2a_push_notification_config(push_notification_config)
        )

    request = v03.SendMessageRequest(
        id=str(uuid.uuid4()),
        params=v03.MessageSendParams(
            message=v03.Message(
                message_id=str(uuid.uuid4()),
                role=v03.Role.user,
                parts=[v03.Part(root=v03.DataPart(data={"skill": skill_name, "parameters": parameters}))],
            ),
            configuration=configuration,
        ),
    )
    return request.model_dump(mode="json", by_alias=True, exclude_none=True)


def _a2a_push_notification_config(config: Any) -> Any:
    """An AdCP ``push_notification_config`` as the A2A protocol layer carries it.

    The two vocabularies name the same thing differently and the translation is
    the transport's, not the scenario's: AdCP's ``authentication`` is
    ``{scheme, credentials}`` (``core/push-notification-config.json``) while A2A's
    ``PushNotificationAuthenticationInfo`` is ``{schemes: [...], credentials}``.
    Built from the SDK's own v0.3 models so a renamed field fails here rather than
    silently dropping the credential — which, for the scenario that exists to prove
    a credential-carrying registration is refused, would be a false green.
    """
    from a2a.compat.v0_3 import types as v03

    raw = config.model_dump(mode="json", exclude_none=True) if hasattr(config, "model_dump") else dict(config)
    authentication = raw.get("authentication")
    info = None
    if authentication is not None:
        scheme = authentication.get("scheme")
        info = v03.PushNotificationAuthenticationInfo(
            schemes=[scheme] if scheme else list(authentication.get("schemes") or []),
            credentials=authentication.get("credentials"),
        )
    return v03.PushNotificationConfig(
        url=raw.get("url"),
        id=raw.get("id"),
        token=raw.get("token"),
        authentication=info,
    )


def _a2a_push_config_set_body(config: Any, *, task_id: str) -> dict[str, Any]:
    """The ``tasks/pushNotificationConfig/set`` JSON-RPC envelope for *config*.

    A2A's SECOND credential location, and the one nothing graded before
    ``salesagent-jj90f``: a buyer registers a webhook and its credentials here
    WITHOUT invoking any skill, so the registration never appears in a
    ``message/send`` envelope or in any tool's arguments.

    Built from the SDK's own ``SetTaskPushNotificationConfigRequest`` — the SAME
    model the server's compat adapter validates the body against — for the reason
    ``_a2a_message_send_body`` gives: a field the SDK renames must fail HERE, not
    silently drop the credential and leave a credential-registration scenario
    passing because it registered nothing.

    *task_id* is required by the model but NOT by the seller: our handler upserts
    a config for whatever id it is handed (``task_id or "*"``), which is precisely
    why this is a registration channel of its own rather than a rider on an
    existing task.
    """
    from a2a.compat.v0_3 import types as v03

    request = v03.SetTaskPushNotificationConfigRequest(
        id=str(uuid.uuid4()),
        params=v03.TaskPushNotificationConfig(
            task_id=task_id,
            push_notification_config=_a2a_push_notification_config(config),
        ),
    )
    return request.model_dump(mode="json", by_alias=True, exclude_none=True)


def _a2a_jsonrpc_result(response: Any) -> dict[str, Any]:
    """The ``result`` of an ``/a2a`` answer, or its ``error`` raised as an AdCPError.

    One reader for every method this harness POSTs to ``/a2a``: a refusal that
    never produced an envelope surfaces as ``WireRefusal`` (carrying the response,
    so a bodyless 401's ``WWW-Authenticate`` survives), a JSON-RPC error surfaces
    as a typed ``AdCPError``, and anything else is the result object. Shared so a
    second method cannot grow a second, differently-wrong way to read the same
    wire.
    """
    envelope = _jsonrpc_body(response, surface=_A2A_PATH)
    if "error" in envelope:
        from src.core.exceptions import AdCPError

        raise AdCPError(f"A2A JSON-RPC error: {envelope['error']}")
    return envelope.get("result") or {}


def _a2a_first_data_part(artifact: dict[str, Any]) -> dict[str, Any] | None:
    """The first ``data`` part of an artifact as the ``/a2a`` HTTP wire renders it."""
    for part in artifact.get("parts") or []:
        if isinstance(part, dict) and isinstance(part.get("data"), dict):
            return dict(part["data"])
    return None


def _mcp_jsonrpc_request(request_id: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """One MCP JSON-RPC request frame, built from the mcp SDK's own models.

    The envelope shape is the SDK's ``JSONRPCRequest`` rather than a literal
    dict, so a field the protocol renames cannot silently diverge in the harness
    while the server keeps validating against the model.
    """
    from mcp import types as mcp_types

    frame = mcp_types.JSONRPCRequest(jsonrpc="2.0", id=request_id, method=method, params=params)
    return frame.model_dump(by_alias=True, mode="json", exclude_none=True)


def _mcp_error_to_exception(payload: dict[str, Any]) -> Exception:
    """The exception an MCP error frame stands for, in the shape the unwrapper reads.

    ``_unwrap_mcp_tool_error`` reconstructs the typed ``AdCPError`` from the JSON
    envelope FastMCP puts in ``str(ToolError)``. Over HTTP that envelope arrives
    as the ``isError`` result's text content (or as a JSON-RPC ``error.message``),
    so it is re-wrapped in a ``ToolError`` here and handed to the SAME unwrapper
    the in-memory leg uses — one reconstruction, not two.
    """
    from fastmcp.exceptions import ToolError

    for item in payload.get("content") or []:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            return ToolError(item["text"])
    return ToolError(str(payload.get("message") or payload))


class WireRefusal(AssertionError):
    """An HTTP refusal that arrived BEFORE any JSON-RPC envelope existed.

    Carries the response, because on the ``/a2a`` and ``/mcp`` legs the only
    evidence of WHICH refusal happened can live outside the body: the ASGI
    signature verifier answers a BODYLESS 401 whose sole signal is
    ``WWW-Authenticate: Signature error="<code>"``. Raising a bare
    ``AssertionError`` here discarded that header, so an unsigned refusal on those
    two legs could be observed only as "some 4xx" — the exact status-shaped
    vacuity ``assert_signature_challenge`` exists to remove (salesagent-n78j0.1.2),
    and the reason the REST leg already had ``_non_json_error_result``.
    """

    def __init__(self, message: str, response: Any) -> None:
        super().__init__(message)
        self.response = response


def _jsonrpc_body(response: Any, *, surface: str) -> dict[str, Any]:
    """One JSON-RPC envelope out of an HTTP response, SSE-framed or not.

    MCP's streamable HTTP answers a POST with ``text/event-stream`` (FastMCP's
    ``http_app`` does not enable JSON responses), so the envelope arrives as an
    SSE ``data:`` line; ``/a2a`` answers with plain JSON. One reader for both,
    because the CALLER only ever wants the envelope and a per-leg copy of this
    framing would be two ways to mis-read the same wire.
    """
    import json as _json

    if response.status_code >= 400 or not response.content:
        raise WireRefusal(f"{surface} returned HTTP {response.status_code}: {response.text[:800]!r}", response)

    if "text/event-stream" not in response.headers.get("content-type", ""):
        return response.json()

    for line in response.text.splitlines():
        if not line.startswith("data:"):
            continue
        envelope = _json.loads(line[len("data:") :].strip())
        if isinstance(envelope, dict) and ("result" in envelope or "error" in envelope):
            return envelope
    raise AssertionError(f"{surface} SSE response carried no JSON-RPC envelope: {response.text[:800]!r}")


class _TestClock:
    """Minimal clock for BDD relative date-token resolution.

    The media-buy Given steps resolve Gherkin tokens (``{now}``,
    ``{30 days from now}``, ``{1 day ago}``) against ``ctx["env"].clock`` using
    the ``now_iso`` / ``future_iso`` / ``past_iso`` interface. Emits the
    ``YYYY-MM-DDTHH:MM:SSZ`` shape AdCP request validators accept.
    """

    @staticmethod
    def _iso(dt: Any) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    def now_iso(self) -> str:
        from datetime import UTC, datetime

        return self._iso(datetime.now(UTC))

    def future_iso(self, days: int) -> str:
        from datetime import UTC, datetime, timedelta

        return self._iso(datetime.now(UTC) + timedelta(days=days))

    def past_iso(self, days: int) -> str:
        from datetime import UTC, datetime, timedelta

        return self._iso(datetime.now(UTC) - timedelta(days=days))


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
        # E2E mode: bind factories to the live server's DB so the HTTP-reached
        # server sees Given-step data. Explicit database_url wins; else the
        # e2e_config's postgres_url. None => normal cached/integration engine.
        self._database_url = database_url or (e2e_config.postgres_url if e2e_config else None)
        self.e2e_config: E2EConfig | None = e2e_config
        self._e2e_engine: Any = None
        self._tenant_overrides = tenant_overrides
        self.mock: dict[str, MagicMock] = {}
        self._patchers: list[Any] = []
        self._session: Session | None = None
        self._identity_cache: dict[str, ResolvedIdentity] = {}
        self._rest_client: Any = None  # Lazy-created TestClient
        self.clock = _TestClock()  # BDD steps may use env.clock for date tokens
        # Real serialized success-path wire, stashed by _run_a2a_handler /
        # _run_mcp_client (the only paths that capture it) and read by the
        # A2A/MCP dispatchers. None unless such a path ran — REST builds its
        # own from the HTTP body; legacy/_raw paths and IMPL leave it None.
        self._last_wire_response: dict[str, Any] | None = None
        # Raw A2A Task returned by the last _run_a2a_handler call. The submitted
        # (manual-approval) contract lives on the Task itself — state=SUBMITTED
        # with NO artifacts — and the synthesized submitted wire above cannot
        # prove artifact absence, so guards assert on this captured Task.
        self._last_a2a_task: Any = None
        # WHICH signature realization the dispatch currently in flight carries —
        # not whether it asked for one. It holds one of False / True /
        # ``"malformed"`` / ``"tampered"``
        # (:data:`tests.helpers.signing.SIGNATURE_REALIZATIONS`), and two GRADED
        # frames read the realization back out of it verbatim (the A2A frame and
        # ``_mcp_post``). Anything that narrows it to a bool here hands those
        # frames a well-formed signature for ``"malformed"``, because
        # ``bool("malformed")`` is True.
        #
        # It travels on the env rather than as a kwarg because A2A and MCP dispatch
        # through env-owned ``call_a2a``/``call_mcp`` OVERRIDES whose signatures
        # the harness does not control — a ``signed=`` kwarg would have to be
        # forwarded by every subclass or it would arrive as a skill parameter.
        # ``call_via`` sets it for exactly one dispatch, next to the wire capture
        # those same two methods already stash here. REST needs none of this: its
        # dispatcher calls ``_run_rest_request`` directly and passes ``signed=``.
        self._signed_dispatch: SignatureRealization = False

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
            principal_id = self._principal_id
            if self.use_real_db:
                self._commit_factory_data()
                # _resolve_auth_token() returns None for two different reasons:
                # (a) a real DB lookup ran and found no matching Principal row, or
                # (b) self._session isn't bound yet (env constructed/used outside
                # its `with` context). Only (a) is a genuine "principal doesn't
                # exist" signal — gate on self._session directly so a session-
                # timing case doesn't get misread as a missing-principal one.
                if self._session:
                    auth_token = self._resolve_auth_token()
                    if auth_token is None:
                        # No Principal row for this principal_id+tenant_id (never
                        # created, or deleted after "authenticating") — mirror
                        # production's resolve_identity() (src/core/resolved_identity.py:
                        # 168-172), which nulls principal_id on a failed token->principal
                        # lookup, so in-process transports agree with e2e_rest's real DB
                        # lookup instead of diverging on the deleted-principal case
                        # (salesagent-z9e0).
                        principal_id = None

            self._identity_cache[protocol] = PrincipalFactory.make_identity(
                principal_id=principal_id,
                tenant_id=self._tenant_id,
                protocol=protocol,
                dry_run=self._dry_run,
                auth_token=auth_token,
                **self._tenant_overrides,
            )
        return self._identity_cache[protocol]

    def invalid_token_identity(self) -> ResolvedIdentity:
        """An identity carrying a token that matches no Principal row.

        Per-transport behavior is production's: A2A rejects a presented-but-
        invalid credential; MCP/REST treat it as absent (auth-optional tool).
        """
        from tests.harness._identity import make_identity

        return make_identity(
            principal_id=None,
            tenant_id=self._tenant_id,
            auth_token="invalid-token-harness",
            **self._tenant_overrides,
        )

    def anonymous_identity(self) -> ResolvedIdentity:
        """Tenant-resolvable identity with NO credential and NO principal.

        Models the production no-auth discovery call where the tenant still
        resolves (Host header / subdomain) — distinct from identity=None,
        which is the no-tenant case.
        """
        from tests.harness._identity import make_identity

        return make_identity(
            principal_id=None,
            tenant_id=self._tenant_id,
            auth_token=None,
            **self._tenant_overrides,
        )

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

    def switch_principal(self, principal_id: str) -> None:
        """Re-point the env at *principal_id*, clearing cached identity.

        Public accessor for the principal-switch mutation (mirrors
        ``get_session()``): step functions must not reach into the private
        ``_identity_cache`` / ``_principal_id``. Clearing the cache forces the
        next ``identity`` / ``identity_for`` access to re-resolve from scratch —
        picking up a principal row committed after the env was created (in
        integration mode this re-runs the auth-token lookup).
        """
        self._identity_cache.clear()
        self._principal_id = principal_id

    def switch_tenant(self, tenant_id: str) -> None:
        """Re-point the env at *tenant_id*, clearing cached identity.

        Sibling of ``switch_principal``: step functions that seed a scenario
        into its own fresh tenant (isolation in the shared e2e_rest live DB)
        must not reach into the private ``_identity_cache`` / ``_tenant_id``.
        Clearing the cache forces the next identity build to resolve the auth
        token against the new tenant's principal rows.
        """
        self._identity_cache.clear()
        self._tenant_id = tenant_id

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

    def call_via(self, transport: Transport, *, signed: SignatureRealization = False, **kwargs: Any) -> TransportResult:
        """Dispatch through *transport* and return normalized TransportResult.

        Injects the correct identity for the transport into kwargs (unless
        the caller explicitly provides one). Routes to the appropriate
        dispatcher.

        ``signed=True`` asks for a genuinely signed request — a real RFC 9421
        signature over the exact bytes this call puts on the wire, under a key
        the counterparty's own trust root publishes. The DISPATCHER realizes it,
        because what "signed" means is transport-specific; callers (and every
        BDD step) stay transport-blind. A transport that cannot yet sign REFUSES
        rather than silently sending an unsigned request — see
        ``tests/harness/signing_capability.py``.

        ``signed`` also takes a FAILURE realization — ``"malformed"`` or
        ``"tampered"`` (:data:`tests.helpers.signing.SIGNATURE_REALIZATIONS`) — so a
        scenario can send a signature the verifier must refuse without knowing which
        bytes any transport puts on the wire. Anything else RAISES here
        (:func:`tests.helpers.signing.realization`) rather than falling through to a
        correct signature: this is the earliest seam every dispatch crosses, so a
        typo is caught before it can be graded as an acceptance.

        WHICH FRAME CARRIES THE REALIZATION is the rule the legs below obey, and it
        is not "all of them": a HANDSHAKE frame the harness sends to make the
        operation reachable at all (MCP's ``initialize`` /
        ``notifications/initialized``) signs CORRECTLY whenever signing is on,
        because a session that cannot be established has graded nothing. Every frame
        the SCENARIO put under test carries the realization — including A2A's second
        credential location (``credential_registrations``), which is a credential
        registration in its own right and the epic's headline bypass surface, not a
        harness enabling frame. Locked by
        ``tests/integration/test_harness_signed_dispatch.py``.
        """
        from tests.harness.dispatchers import DISPATCHERS
        from tests.helpers.signing import realization

        signed = realization(signed)

        # Inject transport-correct identity
        kwargs.setdefault("identity", self.identity_for(transport))

        dispatcher = DISPATCHERS[transport]
        # Reset success-path wire capture; _run_a2a_handler / _run_mcp_client
        # set it fresh on success so A2A/MCP dispatchers can surface real wire.
        self._last_wire_response = None
        self._signed_dispatch = signed
        return dispatcher.dispatch(self, signed=signed, **kwargs)

    # -- Outbound webhooks: where they are addressed ------------------------
    #
    # The destination lives here rather than on a domain mixin because every env
    # that REGISTERS a reporting webhook needs it (media-buy create as much as
    # delivery), while only the delivery envs read the deliveries back. Which
    # address a transport gets is realized once, here, so no step definition ever
    # has to ask which transport it is running on (salesagent-n78j0.1.4).

    def _realize_e2e_webhook_destination(self) -> str:
        """E2E: a per-env key on the TLS capture origin the SERVER can actually reach.

        ``https://webhooks.adcp-e2e.dev:8443`` is a real HTTPS origin on a non-private
        address, so production's UNPATCHED SSRF gate accepts it on its own terms. The
        key is this env's alone, which is what lets scenarios share one receiver
        without reading each other's captures.

        This is also the ONE place the key becomes an address something else can be
        given, so it is where :attr:`webhook_capture_key_was_handed_out` is recorded —
        see that property for why the recording cannot live on the minting path.
        """
        from tests.e2e._webhook_capture import delivery_url

        url = delivery_url(self.webhook_capture_key)
        self.__dict__["_webhook_capture_key_handed_out"] = True
        return url

    @property
    def webhook_capture_key(self) -> str:
        """This env's own key on the shared TLS capture receiver.

        Minted LAZILY, which is a hazard as much as a convenience: a READER that
        touches this property brings a key into existence that nothing was ever given.
        Ask :attr:`webhook_capture_key_was_handed_out` before treating an empty
        capture list as an answer.
        """
        if self.__dict__.get("_webhook_capture_key") is None:
            self.__dict__["_webhook_capture_key"] = f"harness-{uuid.uuid4().hex}"
        return str(self.__dict__["_webhook_capture_key"])

    @property
    def webhook_capture_key_was_handed_out(self) -> bool:
        """Was this env's capture key ever given to anything as a DESTINATION?

        The distinction the capture receiver cannot make for us. Its store
        (``tests/e2e/webhook_capture_service.py`` ``_CaptureStore.get``) SYNTHESIZES
        ``{"received": [], "received_raw": []}`` for any key it has never seen, so
        "registered, and correctly received nothing" and "nobody was ever told this
        address" are the same 200/empty answer server-side. That is right for the
        service — it learns of a key only when a delivery arrives, so it has no notion
        of registration to report — and it is why the distinction has to be made HERE,
        where the fact actually exists.

        Recorded on the HANDING-OUT path (:meth:`_realize_e2e_webhook_destination`),
        never on the minting path: :attr:`webhook_capture_key` mints on demand, so a
        flag set there would only ever say "a key exists" — which a vacuous reader
        makes true by reading. The address being ISSUED is the fact; a key existing is
        not (salesagent-n78j0.1.4).
        """
        return bool(self.__dict__.get("_webhook_capture_key_handed_out"))

    @realize_e2e(_realize_e2e_webhook_destination)
    def webhook_destination(self) -> str:
        """Where this env's outbound webhook deliveries are addressed.

        In process a fixed public URL: the POST is intercepted before it leaves, so
        the address only has to survive production's SSRF gate.
        """
        return IN_PROCESS_WEBHOOK_URL

    # -- Inbound webhook credentials: WHERE a transport carries them ---------
    #
    # The counterpart of the block above, and the harder half: a buyer HANDS the
    # seller webhook credentials in a transport-specific place, and on at least one
    # transport there is MORE THAN ONE such place. A step that asked which transport
    # it was on could not state that; this is the one layer that may
    # (salesagent-jj90f).

    def credential_registrations(
        self,
        transport: Transport,
        config: Any,
        operation_result: TransportResult,
        *,
        signed: SignatureRealization = False,
    ) -> tuple[tuple[str, TransportResult], ...]:
        """Every place *transport* let this buyer hand the seller webhook credentials.

        Returns ``((location, result), ...)``, ONE ENTRY PER LOCATION, so a caller
        grades the seller's answer at each of them and names the location that
        answered wrongly. The first entry is always *operation_result* — the
        dispatch the caller already made — labelled with where THAT transport
        carries the config. Any further entry is a channel this transport offers
        that no operation dispatch touches; the env sends it here.

        WHY THIS EXISTS AT ALL, and why one entry is not enough. A2A has TWO
        credential locations and they are not variants of each other:

        1. ``params.configuration.task_push_notification_config`` on
           ``message/send`` — a webhook registered ALONGSIDE a skill invocation,
           read by ``adcp_a2a_server.on_message_send``;
        2. the ``tasks/pushNotificationConfig/set`` params — a webhook registered
           on its OWN JSON-RPC method with no skill in sight, read by
           ``adcp_a2a_server.on_create_task_push_notification_config``, which
           persists the credentials and returns a config id.

        Grading only (1) leaves (2) an unexercised bypass; MOVING the grading from
        (1) to (2) trades one bypass for the other and un-grades the first. Both,
        or the claim "this seller refuses unsigned credential registrations" is
        false about a surface nothing looked at.

        MCP and REST return a single entry because they genuinely have one such
        place, not because the others were not looked for. If a transport grows a
        second, it is added HERE — the scenario text does not change, because the
        scenario's claim ("a registration carrying credentials is refused unless
        signed") never mentioned a location in the first place.

        *config* is the ``push_notification_config`` the operation dispatch
        carried; ``None`` means the caller registered no credentials, so there is
        nothing to send anywhere else and only the operation entry comes back.
        *signed* must match the operation dispatch's own, or the extra locations
        would be a different experiment from the one the scenario set up. That
        includes a FAILURE realization: this frame is a credential registration the
        SCENARIO put under test, not a harness enabling frame, so a ``"malformed"``
        or ``"tampered"`` realization reaches it VERBATIM. Signing it correctly
        because it is "not the operation frame" would sign the one surface a
        credential-location scenario most needs to grade with a failure realization
        — the A2A credential-location bypass ``_refuse_signed_impl``'s own docstring
        cites as the reason refusing beats ignoring.
        """
        location = _OPERATION_CREDENTIAL_LOCATION.get(transport.value, _UNSTATED_CREDENTIAL_LOCATION)
        registrations: list[tuple[str, TransportResult]] = [(location, operation_result)]
        if config is not None and transport.value == "a2a":
            registrations.append(
                (_A2A_PUSH_CONFIG_SET_LOCATION, self._a2a_credential_registration(config, signed=signed))
            )
        return tuple(registrations)

    # -- Request signing ----------------------------------------------------

    def _realize_e2e_request_signing(self) -> Any:
        """Establish the counterparty against a verifier in ANOTHER process.

        The in-process branch below seeds the middleware's process-global
        ``AGENT_RESOLUTION_CACHE`` — a patch the live server container cannot
        see. Over e2e the same intent has to travel by the real mechanism: the
        key is PUBLISHED on the counterparty origin, the ``agent_url`` that names
        it is recorded on a Principal row in the SERVER's database, and the
        server resolves the two by its own brand.json walk. See
        ``tests/harness/signing_capability.py``.
        """
        from tests.harness.signing_capability import build_e2e_signing_capability

        if self.__dict__.get("_signing") is None:
            self.__dict__["_signing"] = build_e2e_signing_capability(self)
        return self.__dict__["_signing"]

    @realize_e2e(_realize_e2e_request_signing)
    def enable_request_signing(self) -> Any:
        """Mint this env's counterparty signing key and publish its trust root.

        Idempotent: the capability is built once per env. Call from a Given (or
        a fixture) that establishes "this buyer signs"; the transport is chosen
        later, at ``call_via``.
        """
        from tests.harness.signing_capability import build_signing_capability

        if self.__dict__.get("_signing") is None:
            self.__dict__["_signing"] = build_signing_capability(self)
        return self.__dict__["_signing"]

    def declare_request_signing(
        self,
        *,
        required_for: Sequence[str] = (),
        bucket: str | None = None,
        operations: Sequence[str] = (),
    ) -> None:
        """Store this seller's REAL ``request_signing`` declaration on its tenant.

        The Given side of every enforcement scenario: production then does the rest
        for real — ``CapabilityDeclarations.from_tenant`` parses and relation-checks
        the document, ``posture_for_tenant`` reads it, ``bucket_for`` applies the
        ``required_for > warn_for > supported_for`` precedence.

        THREE SHAPES, and the third is the one that must not be collapsed into the
        others:

        * ``bucket="required" | "warn" | "supported"`` with *operations* — delegates to
          :func:`~tests.helpers.signing.bucketed_declaration`, which names those
          operations in the bucket AND in ``supported_for`` (an operation cannot be
          required without being supported) and so leaves every other operation in
          ``none``. ``bucket="narrowed_none"`` and ``bucket="unsupported"`` are the two
          halves of ``none``, delegating to
          :func:`~tests.helpers.signing.narrowed_none` /
          :func:`~tests.helpers.signing.unsupported`; they take no operations because
          what they mean is "this surface is in no bucket";
        * ``required_for=("op",)`` — today's behaviour, unchanged;
        * NO ARGUMENT — ``{"supported": true, "required_for": []}``, and it is
          deliberately NOT ``bucket="supported"`` with no operations. That would write
          ``supported_for: []``, and a null ``supported_for`` is not an empty one: null
          means "verify wherever a signature appears" (every operation ``supported``),
          ``[]`` means every operation ``none``. Delegating would silently move
          ``tests/bdd/steps/domain/signing_enforcement.py``'s no-argument caller — the
          pinned vector 027 shape — into the bucket where nothing is verified, and its
          scenario would pass having graded a pass-through.

        The two ways of naming a bucket are mutually exclusive rather than merged: a
        call passing both would have to pick a precedence, and a silent precedence over
        two declarations is how a scenario ends up grading a posture it did not
        declare.

        The two pre-existing shapes, in the detail the third must not erase:

        * ``required_for=("op",)`` narrows ``supported_for`` to the same names
          (:func:`~tests.helpers.signing.bucketed_declaration`), so every OTHER
          operation lands in ``none`` and the composition rule
          (security.mdx :1268-1269) is what refuses an unsigned, uncredentialed call;
        * ``required_for=()`` writes ``{"supported": true, "required_for": []}`` —
          the pinned conformance vector's own ``verifier_capability``
          (``request-signing/negative/027-webhook-registration-authentication-unsigned.json``).
          ``supported_for`` is left UNSET rather than empty, which is not the same
          thing: null means "verifies wherever a signature appears" and puts every
          operation in the ``supported`` bucket, whereas ``[]`` would put them all in
          ``none`` and disable the escalation this shape exists to reach. That vector
          deliberately keeps the operation OUT of ``required_for`` so the refusal can
          only come from the payload escalation (:1462-1465), never from the
          composition rule.

        Written through the env's OWN session, which in e2e mode is bound to the LIVE
        server's database — the one the verifier reads. ``declared_posture``'s
        ``TenantConfigUoW`` writer cannot serve both: from the runner it opens its own
        engine against ``DATABASE_URL`` (the suite database, not the server's) and is
        empirically invisible to the live server's read. Same document either way
        (:func:`~tests.helpers.signing.posture_declaration_document`), one writer.

        No restore is registered: the in-process legs get a per-test database and the
        e2e leg's live database is truncated per scenario by the BDD conftest
        (``_reset_e2e_db``), and ``__exit__`` closes the session BEFORE it unwinds
        ``_patchers``, so a teardown-time DB write would have nothing to write through.
        """
        from src.core.database.models import Tenant
        from tests.harness.signing_capability import ensure_declarable_identity_host
        from tests.helpers.signing import (
            bucketed_declaration,
            narrowed_none,
            posture_declaration_document,
            unsupported,
        )

        assert not (bucket and required_for), (
            f"declare_request_signing() takes bucket= OR required_for=, not both "
            f"(got bucket={bucket!r}, required_for={list(required_for)!r}). Name the posture once."
        )
        assert not (operations and not bucket), (
            f"declare_request_signing(operations={list(operations)!r}) needs a bucket= to put them in; "
            "required_for= already names its own operations."
        )
        assert not (operations and bucket in ("narrowed_none", "unsupported")), (
            f"declare_request_signing(bucket={bucket!r}, operations={list(operations)!r}) cannot place those "
            f"operations: both halves of the none bucket are fixed declarations that name no operation of the "
            f"caller's, so {bucket!r} would DROP them silently. narrowed_none() narrows around a hardcoded "
            "decoy (create_media_buy); if that decoy IS your surface under test, narrow around a different real "
            'operation with bucket="supported", operations=(<other operation>,) — which is the same declaration '
            "with a decoy you chose. Dropping them would put your surface in the OPPOSITE bucket and grade the "
            "wrong arm green."
        )
        if bucket == "narrowed_none":
            declaration = narrowed_none()
        elif bucket == "unsupported":
            declaration = unsupported()
        elif bucket is not None:
            declaration = bucketed_declaration(bucket, *operations)
        elif required_for:
            declaration = bucketed_declaration("required", *required_for)
        else:
            declaration = {"supported": True, "required_for": []}
        session = self._session
        assert session is not None, "declare_request_signing() must be called inside the env's `with` block"
        ensure_declarable_identity_host(self)
        tenant = session.get(Tenant, self._tenant_id)
        assert tenant is not None, (
            f"declare_request_signing() needs tenant {self._tenant_id!r} to exist before a posture can be "
            "stored on it — seed it (env.setup_default_data(), or the Given that authenticates the buyer) first"
        )
        tenant.capability_declarations = posture_declaration_document(tenant, declaration)
        session.commit()
        # OPENS THE FAILURE WINDOW (:meth:`signature_failures`). Here rather than at env
        # entry because this is the last moment before a scenario can dispatch, and the
        # counter it reads is process-global with no run-identifying label: anything the
        # previous leg recorded is already in the baseline, and only what THIS seller
        # refuses lands after it.
        self.__dict__[_SIGNATURE_FAILURE_WINDOW] = self._signature_failure_counts()

    def _realize_e2e_signature_verifications(self) -> int:
        """The same claim and the SAME EVENT, read across a process boundary.

        Both legs now count ``adcp_request_signature_verified_total``; only the
        reach differs. The in-process branch reads it off the registry it shares
        with the middleware under test, and this fork scrapes it over HTTP because
        the live server's verifier increments a counter in another container, where
        an in-process read would report 0 for a request that WAS verified — the
        silent false negative this fork exists to prevent. See
        :func:`tests.helpers.signing.scraped_verified_count` for why the counter is
        a sound positive oracle and why an empty scrape fails loudly.
        """
        from tests.helpers.signing import scraped_verified_count

        assert self.e2e_config is not None, "signature_verifications()'s e2e branch needs env.e2e_config"
        return int(scraped_verified_count(self.e2e_config.base_url, self.signing.key_id))

    @realize_e2e(_realize_e2e_signature_verifications)
    def signature_verifications(self) -> int:
        """How many requests the seller's verifier ACCEPTED under this env's key.

        The positive oracle, and deliberately not a status code: a 200 is equally
        true of a middleware that never looked, and under ``required_for`` an
        unsigned request carrying a valid bearer is a spec-correct 200
        (security.mdx :1269). The counter's ``keyid`` label is what says the
        signature this env produced was actually verified — production records it
        verbatim from the signer the verifier resolved, and it matches the
        CAPABILITY'S OWN kid, so the count is a claim about this env's requests and
        not about any key merely named like it.

        Counted rather than asserted here because the assertion belongs to the
        scenario: a Then that pins ``== 1`` also rules out a leg that verified twice
        (session frames graded as operations) or zero times.

        ONE EVENT SOURCE, both legs. This used to sum ``verifier_spy`` records
        in-process while the e2e leg scraped the production counter, and those are
        DIFFERENT EVENTS: the spy wraps ``verify_request_signature``, which runs
        BEFORE the Tier 3 brand-authorization check, while
        ``record_signature_verified`` fires only after Tier 3 passes
        (``request_verifier_middleware.py:565``, the one line reaching
        ``await self.app``). So three of the four legs counted an event that PRECEDES
        the acceptance decision, and a scenario grading acceptance passed on a
        request the verifier refused. Both legs now read the same production counter
        — in-process off the shared registry, e2e over HTTP — so the oracle cannot
        disagree with itself by transport.

        An ABSOLUTE read, not a delta: the counter is process-global and monotonic,
        but ``keyid`` carries this capability's ``unique_run_id()``, so the samples
        it selects are this env's own. That is the same property the e2e branch has
        always relied on.
        """
        from tests.helpers.signing import VERIFIED_METRIC, samples_with

        return int(sum(samples_with(VERIFIED_METRIC, keyid=self.signing.key_id).values()))

    def _realize_e2e_signature_failure_counts(self) -> dict[str, float]:
        """The same claim and the SAME EVENT, read across a process boundary.

        The negative twin of :meth:`_realize_e2e_signature_verifications`, forking on
        REACH for the same reason: the live server's verifier records its failures on a
        counter in ANOTHER CONTAINER, where an in-process read returns 0 for a request
        that really was checked — the silent false negative the fork exists to prevent.
        :func:`tests.helpers.signing.scraped_counter_samples` is the cross-container
        analogue of ``samples_with`` and parses the exposition the guarded scrape
        returns.
        """
        from tests.helpers.signing import FAILED_METRIC, scraped_counter_samples, scraped_metrics_text

        assert self.e2e_config is not None, "signature_failures()'s e2e branch needs env.e2e_config"
        return _by_signature_code(
            scraped_counter_samples(scraped_metrics_text(self.e2e_config.base_url), FAILED_METRIC)
        )

    @realize_e2e(_realize_e2e_signature_failure_counts)
    def _signature_failure_counts(self) -> dict[str, float]:
        """Every ``adcp_request_signature_failed_total`` sample right now, summed per code.

        THE FORK IS HERE, on the read, rather than on :meth:`signature_failures` above
        it: the measurement is a before and an after, and forking only the public method
        would leave the two ends of it free to cross different boundaries — a baseline
        off this process compared against a count off the server's, which is not a delta
        of anything.

        Per CODE rather than per label set, because the label set is
        ``(operation, keyid, code)`` and ``keyid`` is pinned to ``unresolved``
        (``src/core/metrics.py:325``): a failure is recorded before any key is resolved,
        so the code is the only label that says WHICH rule refused.
        """
        from tests.helpers.signing import FAILED_METRIC, counter_samples

        return _by_signature_code(counter_samples(FAILED_METRIC))

    def signature_failures(self, code: str) -> int:
        """How many failures carrying *code* were recorded since this seller declared its posture.

        The negative oracle, and the twin of :meth:`signature_verifications` — one
        question, one number. It is a DELTA where its twin is an absolute read, and that
        difference is forced rather than stylistic: ``record_signature_verified`` labels
        its counter with a ``keyid`` carrying this capability's ``unique_run_id()``, so
        an absolute read there selects this env's own samples, while
        ``record_signature_failed`` pins ``keyid=UNRESOLVED_KEYID``
        (``src/core/metrics.py:325``) and its label set therefore carries NO run
        identity at all. ``prometheus_client.REGISTRY`` is process-global and never
        reset, and ``tox.ini``'s ``--dist loadfile`` puts every transport leg of a
        scenario in ONE worker process, so an absolute read here is satisfied on the
        a2a and mcp legs by the rest leg's own increment — the two legs the warn
        contrast exists for. That is a cross-transport claim graded at one transport,
        which is the defect this seam was added to avoid rather than reproduce.

        THE WINDOW OPENS when the seller declares its posture
        (:meth:`declare_request_signing`), which every enforcement scenario does in a
        Given, strictly before it dispatches anything. So the number is scoped to this
        env's own requests without depending on a label production does not emit.

        SINGLE-PURPOSE, deliberately. It answers about this one counter and this one
        label; it is not a metrics accessor on the env, and widening it into one would
        put the choice of what to measure back in the step layer, where transport
        knowledge is not allowed.
        """
        baseline = self.__dict__.get(_SIGNATURE_FAILURE_WINDOW)
        assert baseline is not None, (
            "signature_failures() is a delta and no window is open: the seller has not declared a "
            "request-signing posture on this env. Call declare_request_signing() (the Given that "
            "names the bucket) BEFORE dispatching — an absolute read cannot answer this, because "
            "the failure counter carries no run identity and another leg's increment would satisfy it."
        )
        return int(self._signature_failure_counts().get(code, 0.0) - baseline.get(code, 0.0))

    @property
    def signing(self) -> Any:
        """The env's signing capability, or a hard failure naming the fix.

        Returning None here would surface as a confusing ``AttributeError`` deep
        inside header construction; the caller's actual mistake is that nothing
        established the counterparty's key.
        """
        capability = self.__dict__.get("_signing")
        if capability is None:
            raise AssertionError(
                f"{type(self).__name__} was asked for a signed request but no signing "
                "capability exists. Call env.enable_request_signing() first (from the "
                "Given that establishes the counterparty), then dispatch with "
                "call_via(..., signed=True)."
            )
        return capability

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

    @property
    def last_a2a_task(self) -> Any:
        """Raw A2A Task from the last ``_run_a2a_handler`` dispatch (or None).

        Public accessor for Task-level contract assertions — e.g. the submitted
        (manual-approval) contract, where state=TASK_STATE_SUBMITTED with NO
        artifacts IS the wire and the parsed response is a harness synthesis
        that cannot prove artifact absence.
        """
        return self._last_a2a_task

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

        Once the env CAN sign, this defers to ``_run_a2a_over_http``: an
        ``on_message_send`` call has no wire, so ``RequestSignatureMiddleware``
        (ASGI, above the whole app) never sees it and a signature would be
        unobservable. See ``can_sign`` for why the fork is on the capability and
        not on ``signed``.

        Args:
            skill_name: A2A skill name (e.g., "get_products").
            response_cls: Pydantic model class to parse artifact data into.
            **kwargs: Skill parameters. ``identity`` is popped and used for
                the identity mock; remaining kwargs become skill parameters.
        """
        if self.can_sign:
            return self._run_a2a_over_http(skill_name, response_cls, **kwargs)

        import asyncio

        from a2a.server.routes.common import ServerCallContext
        from a2a.types import SendMessageRequest, Task

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

        parameters = self._a2a_skill_parameters(kwargs)

        handler = AdCPRequestHandler()

        # Auth strategy mirrors _run_mcp_client. When the identity carries a real
        # auth_token (integration mode), populate the AuthContext that the SDK
        # call-context builder would have built from the wire and run the REAL
        # _get_auth_token + _resolve_a2a_identity (header → token → DB lookup →
        # ResolvedIdentity). Only the transport's state injection is supplied here
        # (the in-process equivalent of MCP's get_http_headers seam) — the auth
        # chain itself is real. When no real token exists (unit mode), inject the
        # identity directly via the single mock point (unchanged behavior).
        auth_token = a2a_identity.auth_token if a2a_identity else None

        if auth_token:
            from src.core.auth_context import AUTH_CONTEXT_STATE_KEY, AuthContext

            headers = {
                "x-adcp-auth": auth_token,
                "x-adcp-tenant": a2a_identity.tenant_id or "",
            }
            server_context = ServerCallContext(
                state={AUTH_CONTEXT_STATE_KEY: AuthContext(auth_token=auth_token, headers=headers)}
            )
        else:
            # _get_auth_token must return a non-None value when identity exists,
            # otherwise the handler rejects the request before _resolve_a2a_identity
            # is called. Use auth_token from identity, falling back to a sentinel.
            handler._resolve_a2a_identity = lambda *args, **kw: a2a_identity  # type: ignore[assignment]
            handler._get_auth_token = lambda *args, **kw: (  # type: ignore[assignment]
                (a2a_identity.auth_token or "harness-test-token") if a2a_identity else None
            )
            server_context = ServerCallContext()

        # Set tenant ContextVar so production code can read it
        if a2a_identity and a2a_identity.tenant:
            from src.core.config_loader import set_current_tenant

            set_current_tenant(a2a_identity.tenant)

        message = create_a2a_message_with_skill(skill_name=skill_name, parameters=parameters)
        params = SendMessageRequest(message=message)

        async def _call():
            return await handler.on_message_send(params, server_context)

        try:
            task_result = asyncio.run(_call())
        except Exception as exc:
            # Translate A2AError back to AdCPError for callers that catch
            # domain exceptions (e.g., pytest.raises(AdCPAuthenticationError)).
            raise _unwrap_a2a_server_error(exc) from exc

        # Parse Task.artifacts[0] into response_cls
        if not isinstance(task_result, Task):
            raise TypeError(f"Expected Task, got {type(task_result).__name__}: {task_result}")

        # Expose the raw Task so tests can pin Task-level contract facts
        # (state, artifact absence) that the parsed response cannot prove.
        self._last_a2a_task = task_result

        from a2a.types import TaskState

        # Normalize the protobuf state onto the v0.3 spelling the shared outcome
        # helper reads — the HTTP leg receives that spelling straight off the wire.
        protobuf_states = {
            TaskState.TASK_STATE_FAILED: "failed",
            TaskState.TASK_STATE_SUBMITTED: "submitted",
        }

        return self._a2a_task_outcome(
            state=protobuf_states.get(task_result.status.state, ""),
            status=task_result.status,
            task_id=task_result.id,
            artifact_data=(extract_data_from_artifact(task_result.artifacts[0]) if task_result.artifacts else None),
            response_cls=response_cls,
        )

    @staticmethod
    def _a2a_skill_parameters(kwargs: dict[str, Any]) -> dict[str, Any]:
        """Flat A2A skill parameters from dispatch kwargs (``req`` unpacked).

        A2A skills accept a flat parameter dict, not a request model. Shared by
        the in-process handler leg and the HTTP leg so the two cannot send
        different parameters for the same call.
        """
        req = kwargs.pop("req", None)
        if req is not None and hasattr(req, "model_dump"):
            req_fields = req.model_dump(mode="json", exclude_none=True)
            return {**req_fields, **kwargs}
        return dict(kwargs)

    def _a2a_task_outcome(
        self,
        *,
        state: str,
        status: Any,
        task_id: str,
        artifact_data: dict[str, Any] | None,
        response_cls: type,
    ) -> Any:
        """Turn one A2A Task into a parsed response, a raise, or the submitted wire.

        Stated ONCE for both A2A legs. The in-process leg reads a protobuf
        ``Task``, the HTTP leg reads the v0.3 JSON the ``/a2a`` route serializes;
        the three OUTCOMES (failed → reconstructed ``AdCPError``, submitted →
        synthesized submitted wire, otherwise → the stripped artifact DataPart)
        are the same contract, and a second copy of them is how the two legs
        would drift into grading different things.

        *state* is the normalized A2A task state — the v0.3 spelling
        (``"failed"`` / ``"submitted"``), which the protobuf leg maps onto via
        :data:`_A2A_PROTOBUF_STATES`.
        """
        # AdCP-domain errors surface as a FAILED Task with the two-layer envelope
        # in the artifact DataPart. Reconstruct the AdCPError so callers catch
        # domain exceptions instead of a pydantic ValidationError from trying to
        # parse the envelope as a success response.
        if state == "failed":
            from src.core.exceptions import AdCPError

            if artifact_data:
                reconstructed = _envelope_to_adcp_error(artifact_data, fallback_message="A2A skill failed")
                if reconstructed is not None:
                    raise reconstructed
            raise AdCPError(f"A2A task failed: {status}")

        if state == "submitted":
            # Async manual-approval path: the server returns a submitted Task with NO
            # artifacts (adcp_a2a_server.py:683) — the submitted envelope is conveyed by
            # the Task state + id, not an artifact union. Reconstruct the submitted wire
            # (protocol status="submitted" + the task_id the buyer polls) so success-path
            # grading sees the real A2A wire.
            submitted_wire = {"status": "submitted", "task_id": task_id}
            self._last_wire_response = dict(submitted_wire)
            return response_cls(**submitted_wire)

        if artifact_data is None:
            raise ValueError(f"Task has no artifacts. Status: {status}")
        # Surface the full, unstripped artifact DataPart as the real A2A wire for
        # success-path assertions. Captured BEFORE stripping so siblings that need
        # the top-level envelope fields (message/success) still see them.
        self._last_wire_response = dict(artifact_data)
        # Strip protocol fields added by _serialize_for_a2a (message, success).
        # These are populated by the protocol layer per the pin's Protocol
        # Envelope arm (see tests/helpers/adcp_schema_validator.py) — not
        # declared on the Pydantic response model — and cause ValidationError
        # under extra="forbid" in non-production mode.
        artifact_data.pop("message", None)
        artifact_data.pop("success", None)
        return response_cls(**artifact_data)

    def _run_a2a_over_http(self, skill_name: str, response_cls: type, **kwargs: Any) -> Any:
        """A2A dispatch as a real POST to ``/a2a`` on ``src.app.app``.

        The leg the signing capability needs, and strictly MORE production than
        the in-process one: the request traverses ``UnifiedAuthMiddleware`` →
        ``RequestSignatureMiddleware`` → the SDK's JSON-RPC route → the integer-
        restoring wrapper (``src/app.py``) → the same ``AdCPRequestHandler``.
        Identity is not fabricated here — it is resolved from the real bearer
        ``wire_request`` puts on ``Authorization``, by the same
        ``AdCPCallContextBuilder`` a live buyer would meet.

        The wire method is ``message/send``, NOT the a2a-sdk 1.0 native
        ``SendMessage``. The pinned AdCP 3.1.1 capabilities schema constrains
        every ``protocol_methods_*`` bucket with
        ``pattern: ^[a-z][a-z0-9_]*/[a-z][a-z0-9_]*$``, so ``SendMessage`` is
        UNREPRESENTABLE in any posture bucket — a request naming it can never
        land anywhere but the ``none`` bucket, where the verifier is never
        called and a signing assertion would be vacuous. ``message/send`` reaches
        the same handler through the SDK's v0.3 compat adapter
        (``enable_v0_3_compat=True``, ``src/app.py``) and resolves through
        ``src/core/signing/operations.py`` to the SKILL as the operation, which
        is the namespace ``required_for`` actually grades.
        """
        from tests.harness.transport import Transport

        self._commit_factory_data()

        _NO_OVERRIDE = object()
        identity = kwargs.pop("identity", _NO_OVERRIDE)
        a2a_identity = self.identity_for(Transport.A2A) if identity is _NO_OVERRIDE else identity
        if self.use_real_db and a2a_identity and a2a_identity.tenant_id:
            self._ensure_tenant_for_audit(a2a_identity.tenant_id)

        # Lifted OUT of the skill parameters and into the protocol envelope, where
        # production reads it on this transport — see ``_a2a_message_send_body``.
        push_notification_config = kwargs.pop("push_notification_config", None)
        parameters = self._a2a_skill_parameters(kwargs)
        body = _a2a_message_send_body(skill_name, parameters, push_notification_config)
        # /a2a with NO trailing slash: src/app.py 307-redirects /a2a/, and httpx
        # would replay the pre-redirect signature against the new target-uri —
        # a genuine signature failing as request_signature_invalid.
        #
        # ``identity=None`` means "send without a credential" everywhere else in the
        # harness, so it decides ``credentialed`` here too rather than the leg
        # quietly attaching the capability's bearer to a call the caller asked to
        # make anonymous. It is the only way this leg reaches the composition rule's
        # refusal branch at all: security.mdx :1269 makes an unsigned request
        # carrying a valid bearer a spec-correct 200.
        raw, headers = self.wire_request(
            path=_A2A_PATH, body=body, signed=self._signed_dispatch, credentialed=a2a_identity is not None
        )
        response = self.get_rest_client().post(_A2A_PATH, content=raw, headers=headers)

        result = _a2a_jsonrpc_result(response)
        # v1.0 wraps the task; v0.3 returns it bare. Accept both so this parse does
        # not silently start returning {} if the compat arm is ever retired.
        task = result.get("task", result)
        artifacts = task.get("artifacts") or []
        artifact_data = _a2a_first_data_part(artifacts[0]) if artifacts else None
        return self._a2a_task_outcome(
            state=(task.get("status") or {}).get("state", ""),
            status=task.get("status"),
            task_id=task.get("id", ""),
            artifact_data=artifact_data,
            response_cls=response_cls,
        )

    def _a2a_credential_registration(self, config: Any, *, signed: SignatureRealization) -> TransportResult:
        """A2A's SECOND credential location, dispatched and wrapped like any other.

        Wrapped through ``a2a_transport_result`` — the same wrapper
        ``A2ADispatcher`` uses — so this result carries a refusal's raw HTTP
        response exactly as an operation dispatch would, and
        ``assert_signature_challenge`` can read the challenge off it. A second,
        local way of turning a POST into a ``TransportResult`` would be free to
        drop that response, and a dropped response is graded as "no evidence",
        which reads like a harness bug rather than the acceptance it would be.
        """
        from tests.harness.dispatchers import a2a_transport_result

        return a2a_transport_result(lambda: self._run_a2a_push_config_set(config, signed=signed))

    def _run_a2a_push_config_set(self, config: Any, *, signed: SignatureRealization) -> Any:
        """POST ``tasks/pushNotificationConfig/set`` to ``/a2a`` on ``src.app.app``.

        Same route, same middleware chain and the same ``wire_request`` seam as
        ``_run_a2a_over_http`` — deliberately, because the ONLY difference this
        leg is allowed to have from the ``message/send`` one is the JSON-RPC
        method and where the credentials sit inside it. Anything else (a
        different bearer, a missing tenant hint, a re-serialized body) would make
        the two locations incomparable, and the finding is precisely that the
        seller treats them differently.

        ``credentialed=True``: the buyer registering a webhook IS authenticated.
        That is not a convenience — the escalation at security.mdx @ v3.1.1
        :1462-1465 is deliberately NOT subject to the composition rule's
        bearer exemption, so an unsigned registration carrying a valid bearer is
        exactly the request that must be refused.

        Does NOT stash ``_last_wire_response``: the operation dispatch's capture
        belongs to the operation dispatch, and overwriting it here would rewrite
        the wire a success-path Then step reads.
        """
        from a2a.compat.v0_3 import types as v03

        self._commit_factory_data()
        body = _a2a_push_config_set_body(config, task_id=f"task_{uuid.uuid4().hex[:12]}")
        raw, headers = self.wire_request(path=_A2A_PATH, body=body, signed=signed, credentialed=True)
        response = self.get_rest_client().post(_A2A_PATH, content=raw, headers=headers)
        return v03.TaskPushNotificationConfig.model_validate(_a2a_jsonrpc_result(response))

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

        Once the env CAN sign, this defers to ``_run_mcp_over_http``: FastMCP's
        in-memory transport is a pair of anyio object streams with no HTTP, no
        ASGI and no headers, so ``RequestSignatureMiddleware`` — registered on
        ``src.app.app`` — is not on that path at all. See ``can_sign``.

        Args:
            tool_name: MCP tool name (e.g., "get_products").
            response_cls: Pydantic model class to parse structured_content into.
            **kwargs: Tool arguments. ``identity`` is popped and used for the
                auth mock; ``req`` is popped and its fields unpacked into the
                arguments dict.
        """
        if self.can_sign:
            return self._run_mcp_over_http(tool_name, response_cls, **kwargs)

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

        arguments = self._mcp_tool_arguments(kwargs)

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
                        self._last_wire_response = result.structured_content
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
                        self._last_wire_response = result.structured_content
                        return response_cls(**result.structured_content)

        try:
            return asyncio.run(_call())
        except Exception as exc:
            raise _unwrap_mcp_tool_error(exc) from exc

    @staticmethod
    def _mcp_tool_arguments(kwargs: dict[str, Any]) -> dict[str, Any]:
        """Flat MCP tool arguments from dispatch kwargs (``req`` unpacked).

        MCP tools accept individual params, not a request model; explicit kwargs
        win over ``req`` fields. Shared by the in-memory leg and the HTTP leg so
        one call cannot become two different tool invocations.
        """
        req = kwargs.pop("req", None)
        if req is not None and hasattr(req, "model_dump"):
            req_fields = req.model_dump(exclude_none=True)
            return {**req_fields, **kwargs}
        return dict(kwargs)

    def _run_mcp_over_http(self, tool_name: str, response_cls: type, **kwargs: Any) -> Any:
        """MCP dispatch as real streamable-HTTP POSTs to ``/mcp/`` on ``src.app.app``.

        The leg the signing capability needs. FastMCP exposes no per-request
        header seam on its in-memory transport (``Client.call_tool`` has no
        ``headers=``), and more fundamentally that transport never touches
        ``src.app.app``, where the verifier lives — so the session is driven
        directly here: ``initialize`` → ``notifications/initialized`` →
        ``tools/call``, each POSTed through the SAME ``TestClient``-on-
        ``src.app.app`` the REST leg uses, each serialized ONCE and signed over
        exactly the bytes sent.

        Only the ``tools/call`` frame is a graded operation. ``initialize`` names
        a protocol method with no ``/``, which the pinned
        ``protocol_methods_*`` pattern cannot represent, and
        ``notifications/initialized`` is not something an AdCP posture declares —
        both land in the ``none`` bucket, which
        ``RequestSignatureMiddleware`` passes through WITHOUT calling the
        verifier. Signing them anyway is correct (each gets its own fresh nonce)
        and keeps "the verifier ran exactly once per dispatch" true.

        Driving the three frames by hand rather than through FastMCP's
        ``StreamableHttpTransport`` is deliberate: that client also opens a
        long-lived ``GET`` SSE stream, and httpx's ASGI transport BUFFERS the
        whole app call before returning, so the stream would never complete.
        Hand-driving also means no frame is sent that this leg did not choose.

        REMOVAL TRIGGER — **salesagent-n78j0.12** (convention:
        ``src/core/signing/_upstream/``). This is a HAND-ROLLED PROTOCOL SEQUENCE
        standing in for an SDK transport, the shape that silently drifts from the
        SDK — the same class as adcontextprotocol/adcp#6734. It is a workaround,
        not an architecture, and without a recorded trigger it becomes permanent
        by default.

        The precise upstream inconsistency, which is what n78j0.12 exists to
        verify: the SDK's ``streamablehttp_client`` DOES expose
        ``httpx_client_factory`` — a seam whose natural case is supplying
        ``httpx.ASGITransport`` for in-process testing — but
        ``handle_get_stream`` opens the standalone ``GET`` SSE UNCONDITIONALLY
        once a session id exists, with no knob, which defeats exactly that seam.

        That is report-worthy, but it is NOT filed and must not be filed from
        here: the counterfactual is unverified. n78j0.12 runs it — suppress
        ``handle_get_stream``, drive ``streamablehttp_client`` over an
        ASGI-backed factory, and see whether initialize/initialized/tools-call
        actually completes. If it does, the ticket has proof and THAT becomes
        this method's trigger; if it does not, a second blocker exists and the
        ticket would be unactionable. Scheduled after S1 closes.

        So: delete this method and dispatch through the SDK transport when
        n78j0.12 shows the ASGI-backed factory completing the handshake — or if
        an MCP/FastMCP transport appears that speaks streamable-HTTP without a
        standalone ``GET`` stream at all.

        What must NOT happen meanwhile is this sequence quietly accreting frames
        as the MCP spec evolves: if a future SDK adds or reorders a handshake
        frame, this copy will not know, and the failure will present as a
        verifier bug rather than a stale hand-roll.
        Pinned against: ``mcp`` / ``fastmcp`` as vendored at the time of writing.

        The app LIFESPAN must be running — the ``/mcp`` mount's session manager
        is started there — so a dedicated ``TestClient`` is entered for the
        dispatch, wrapped in ``preserved_global_app_state`` because lifespan
        startup rewrites the process-global route table and shutdown does not
        undo it (``tests/helpers/app_state.py``, salesagent-66a1).
        """
        from starlette.testclient import TestClient

        from src.app import app
        from tests.harness.transport import Transport
        from tests.helpers.app_state import preserved_global_app_state

        self._commit_factory_data()

        _NO_OVERRIDE = object()
        identity = kwargs.pop("identity", _NO_OVERRIDE)
        mcp_identity = self.identity_for(Transport.MCP) if identity is _NO_OVERRIDE else identity
        if self.use_real_db and mcp_identity and mcp_identity.tenant_id:
            self._ensure_tenant_for_audit(mcp_identity.tenant_id)

        arguments = self._mcp_tool_arguments(kwargs)

        # ``identity=None`` is "send without a credential" (see ``_run_a2a_over_http``);
        # it applies to the handshake frames too, because a session opened under a
        # bearer and used without one would differ from the anonymous request under
        # test by more than the credential.
        credentialed = mcp_identity is not None
        with preserved_global_app_state(), TestClient(app) as client:
            session_id = self._mcp_open_session(client, credentialed=credentialed)
            envelope = self._mcp_post(
                client,
                _mcp_jsonrpc_request(2, "tools/call", {"name": tool_name, "arguments": arguments}),
                session_id=session_id,
                credentialed=credentialed,
            )

        if "error" in envelope:
            raise _unwrap_mcp_tool_error(_mcp_error_to_exception(envelope["error"]))
        result = envelope.get("result") or {}
        if result.get("isError"):
            raise _unwrap_mcp_tool_error(_mcp_error_to_exception(result))
        structured = result.get("structuredContent")
        if structured is None:
            raise AssertionError(f"MCP tools/call for {tool_name!r} returned no structuredContent: {result!r}")
        self._last_wire_response = structured
        return response_cls(**structured)

    def _mcp_open_session(self, client: Any, *, credentialed: bool = True) -> str:
        """Run the two handshake frames and return the server's session id.

        ``initialize`` mints the session; the server REFUSES any request before
        ``notifications/initialized`` confirms it ("Received request before
        initialization was complete"), so both are mandatory — and both are
        signed, because once the env can sign every byte it puts on this wire
        carries a signature.

        SIGNED CORRECTLY WHENEVER SIGNING IS ON AT ALL — ``bool(self._signed_dispatch)``,
        never the realization itself. These two frames are the harness's own enabling
        frames: the scenario named an OPERATION, and neither of these is it. A
        ``"malformed"`` realization here would 401 the ``initialize``, leave no
        ``mcp-session-id`` to read, and raise :class:`WireRefusal` carrying a 401 whose
        challenge code is IDENTICAL to the one the ``tools/call`` would have produced —
        so a scenario asserting "a malformed signature is refused" would PASS having
        graded a frame it never named, and the operation frame would never have been
        sent. A session that cannot be established has graded nothing. Locked by
        ``tests/integration/test_harness_signed_dispatch.py``.
        """
        from mcp import types as mcp_types

        handshake_signed = bool(self._signed_dispatch)
        response = self._mcp_send(
            client,
            _mcp_jsonrpc_request(
                1,
                "initialize",
                {
                    "protocolVersion": mcp_types.LATEST_PROTOCOL_VERSION,
                    "capabilities": mcp_types.ClientCapabilities().model_dump(by_alias=True, exclude_none=True),
                    "clientInfo": mcp_types.Implementation(name="adcp-harness", version="1.0").model_dump(
                        by_alias=True, exclude_none=True
                    ),
                },
            ),
            credentialed=credentialed,
            signed=handshake_signed,
        )
        session_id = response.headers.get("mcp-session-id")
        if not session_id:
            # WireRefusal, not a bare AssertionError: a handshake frame REFUSED by a
            # middleware above the mount (the verifier's bodyless 401 is the one that
            # matters here) carries its reason only in the response, and dropping it
            # would report "no session id" for a refusal the caller is entitled to grade.
            raise WireRefusal(f"MCP initialize returned no mcp-session-id: {response.text[:800]!r}", response)
        # Read the envelope even though the session id is what we need: an
        # ``initialize`` that answered an ERROR still carries a session header,
        # and a handshake that failed must surface here rather than as an
        # inexplicable refusal three frames later.
        handshake = _jsonrpc_body(response, surface=_MCP_PATH)
        if "error" in handshake:
            raise AssertionError(f"MCP initialize failed: {handshake['error']!r}")

        initialized = mcp_types.JSONRPCNotification(jsonrpc="2.0", method="notifications/initialized")
        acknowledged = self._mcp_send(
            client,
            initialized.model_dump(by_alias=True, mode="json", exclude_none=True),
            session_id=session_id,
            credentialed=credentialed,
            signed=handshake_signed,
        )
        if acknowledged.status_code >= 400:
            raise WireRefusal(
                f"MCP notifications/initialized was refused with HTTP "
                f"{acknowledged.status_code}: {acknowledged.text[:800]!r}",
                acknowledged,
            )
        return session_id

    def _mcp_send(
        self,
        client: Any,
        body: dict[str, Any],
        *,
        session_id: str | None = None,
        credentialed: bool = True,
        signed: SignatureRealization,
    ) -> Any:
        """POST one MCP frame, signed-or-not by the SAME rules every other leg obeys.

        *signed* is a PARAMETER rather than a read of ``self._signed_dispatch``, and
        that is the whole graded-frame rule in one line: this method is the only seam
        the MCP session's three frames share, so a session-wide read would put the
        scenario's realization on the handshake as well as on the operation. Each
        caller states what ITS frame carries — see :meth:`_mcp_open_session` for why
        the handshake always says ``bool(...)``.
        """
        extra = {"Accept": _MCP_ACCEPT}
        if session_id:
            extra["mcp-session-id"] = session_id
        raw, headers = self.wire_request(
            path=_MCP_PATH, body=body, signed=signed, extra=extra, credentialed=credentialed
        )
        return client.post(_MCP_PATH, content=raw, headers=headers)

    def _mcp_post(
        self, client: Any, body: dict[str, Any], *, session_id: str | None = None, credentialed: bool = True
    ) -> dict[str, Any]:
        """POST one MCP frame and return its JSON-RPC envelope.

        THE GRADED FRAME: the realization reaches the wire VERBATIM here
        (``self._signed_dispatch``, not ``bool(...)``). ``_run_mcp_over_http`` sends
        the ``tools/call`` — the frame the scenario named — through this method and
        nothing else through it.
        """
        return _jsonrpc_body(
            self._mcp_send(
                client, body, session_id=session_id, credentialed=credentialed, signed=self._signed_dispatch
            ),
            surface=_MCP_PATH,
        )

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

    def _pop_rest_identity(self, kwargs: dict[str, Any]) -> Any:
        """Pop ``identity`` from REST kwargs, defaulting to the REST identity.

        Identity handling (mirrors production auth middleware):
        - identity is None → dep raises AUTH_REQUIRED (no token) with suggestion
        - identity is ResolvedIdentity → dep returns it (valid token)
        - identity absent → uses default self.identity_for(Transport.REST)
        """
        from tests.harness.transport import Transport

        _NO_OVERRIDE = object()
        identity = kwargs.pop("identity", _NO_OVERRIDE)
        if identity is _NO_OVERRIDE:
            identity = self.identity_for(Transport.REST)
        return identity

    def _prepare_rest_request(self, kwargs: dict[str, Any]) -> tuple[Any, Any]:
        """Resolve identity, commit factory data, get the client, and install auth.

        Single source of truth for the REST request preamble every dispatcher
        shares: pops ``identity`` from *kwargs* (defaulting to the REST identity),
        commits pending factory rows, creates/returns the TestClient, and installs
        the per-request auth-dep override (which must run AFTER ``get_rest_client``).
        Returns ``(client, resolved_identity)``; the caller builds the body from the
        now-identity-free *kwargs* and issues the HTTP verb.
        """
        identity = self._pop_rest_identity(kwargs)
        self._commit_factory_data()
        client = self.get_rest_client()
        self._configure_rest_auth(identity)
        return client, identity

    @staticmethod
    def _configure_rest_auth(identity: Any) -> None:
        """Install per-request FastAPI auth-dep overrides for the test app.

        Single source of truth for the REST auth contract every dispatcher needs
        (must run AFTER ``get_rest_client``). With ``identity=None`` the
        ``_require_auth_dep`` override is REMOVED so the real production
        dependency runs against the token-less request and raises the real
        ``AUTH_REQUIRED`` error — the harness must not hand-copy the production
        raise (a simulated raise drifted from production once already,
        #1417/cx41); otherwise both deps return the identity.
        """
        from src.app import app
        from src.core.auth_context import _require_auth_dep, _resolve_auth_dep

        if identity is None:
            app.dependency_overrides.pop(_require_auth_dep, None)
            app.dependency_overrides[_resolve_auth_dep] = lambda: None
        else:
            app.dependency_overrides[_require_auth_dep] = lambda: identity
            app.dependency_overrides[_resolve_auth_dep] = lambda: identity

    def _run_rest_request(self, endpoint: str, *, signed: SignatureRealization = False, **kwargs: Any) -> Any:
        """Shared REST dispatch: configure auth → build body → POST → return Response.

        Symmetric with ``_run_mcp_wrapper``. Handles the full REST lifecycle:
        1. Pop ``identity`` from kwargs and configure dep override for this request
        2. Commit factory data
        3. Build request body from remaining kwargs
        4. POST via TestClient
        5. Return raw httpx.Response

        Envs whose route is not a body-carrying POST override this method and
        reuse ``_pop_rest_identity`` / ``_configure_rest_auth`` (e.g.
        ``CapabilitiesEnv`` GETs).
        """
        client, identity = self._prepare_rest_request(kwargs)
        body = self.build_rest_body(**kwargs)

        if not self.can_sign:
            if signed:
                self.signing  # raises, naming enable_request_signing()  # noqa: B018
            return client.post(endpoint, json=body)

        # ``identity=None`` already means "send without a credential" everywhere
        # else in the harness (``_configure_rest_auth`` removes the auth dep for
        # it, and ``RestE2EDispatcher`` reads it the same way) — so it decides
        # ``credentialed`` here too, rather than the signed path quietly attaching
        # the capability's bearer to a call the caller asked to make anonymous.
        # It is the ONLY way an in-process leg reaches the verifier's refusal
        # branch at all: security.mdx :1269 makes an unsigned request carrying a
        # valid bearer a spec-correct 200.
        raw, headers = self.wire_request(path=endpoint, body=body, signed=signed, credentialed=identity is not None)
        return client.post(endpoint, content=raw, headers=headers)

    @property
    def can_sign(self) -> bool:
        """Whether ``enable_request_signing()`` established a counterparty here.

        The fork EVERY leg takes, and the reason it is a fork rather than a
        branch on ``signed``: owner decision D1's corollary. Once an env can
        sign, both its signed AND its unsigned dispatches go over the HTTP path,
        so the two differ by exactly one variable — the signature. An env that
        cannot sign keeps the historical in-process dispatch untouched.
        """
        return self.__dict__.get("_signing") is not None

    def wire_request(
        self,
        *,
        path: str,
        body: Any,
        signed: SignatureRealization,
        extra: dict[str, str] | None = None,
        origin: str | None = None,
        credentialed: bool = True,
        method: str = "POST",
    ) -> tuple[bytes, dict[str, str]]:
        """The bytes and headers ONE signed-or-unsigned request puts on the wire.

        Stated once because all four legs obey the identical rules and only the
        PATH and the DTO differ. Three of those rules were learned the hard way
        and a per-leg copy would re-learn them:

        1. **Serialize ONCE, send exactly those bytes.** httpx's ``json=``
           re-serializes with its own separators, so a signature built over a
           different rendering of the same object covers different bytes than the
           wire carries and is refused as ``request_signature_digest_mismatch`` —
           a fixture bug wearing a verifier bug's clothes, the trap
           ``tests/helpers/signing.py`` warns about in its own docstring.
        2. **Once the env CAN sign, EVERY request carries the credential and the
           tenant hint, signed or not.** Otherwise ``signed=False`` is not a
           control: with no ``x-adcp-tenant`` the middleware never resolves the
           tenant whose posture was declared, falls to the ``none`` bucket and
           answers 200 — which reads as "unsigned was accepted" when in truth the
           verifier was never engaged. Measured on the first spike run, where it
           produced exactly that false green.
        3. **One identity, on the spec-canonical header** (owner decision D3/D4), and
           ONE tenant hint — the env's own, set here so no leg can send a different one.
           ``signed_headers``/``request_headers`` put the capability's token in
           ``Authorization: Bearer`` and set no ``x-adcp-auth``: the alias appears
           nowhere in the pinned 3.1 schemas, and the pinned SDK calls Bearer "the
           spec-canonical header" with the alias "purely additive"
           (``adcp/server/auth.py:311-322``). A leg that also emitted
           ``x-adcp-auth`` would win the precedence race in
           ``resolved_identity._extract_auth_token`` and silently swap the acting
           principal — signed and unsigned would then differ by more than the
           signature.

        *extra* carries whatever else the transport's own framing requires (MCP's
        ``Accept`` negotiation and its ``mcp-session-id``, for instance). It is
        merged BEFORE signing rather than added afterwards so the header set the
        signature was computed over is byte-identical to the one sent — rule 1
        applied to headers instead of to the body.

        *origin* is the scheme+authority the signature's ``@target-uri`` covers,
        and defaults to the in-process ASGI client's ``http://testserver`` — right
        for the three in-process legs and wrong for the one that leaves the
        process. ``_verify_url`` (``request_verifier_middleware``) rebuilds the
        authority from the ``Host`` header the proxy forwards VERBATIM, so an e2e
        caller must pass the real origin INCLUDING THE PORT or the signature
        covers a different target-uri than the verifier reconstructs and is
        refused as ``request_signature_invalid`` — a fixture bug wearing a
        verifier bug's clothes.

        *credentialed* exists for exactly one case, and it is a case rule 2 does
        not cover: grading the REFUSAL. security.mdx :1269 says an unsigned
        request carrying a valid bearer that resolves to an accepted Principal
        MUST NOT be refused for the missing signature, so a credentialed unsigned
        request is a spec-correct 200 and cannot produce a
        ``request_signature_required`` challenge to read. Only an UNACCEPTABLE
        credential — here, none at all — reaches that branch. Everything else
        about the request (the path, the body, the tenant hint) stays identical,
        so the challenge is attributable to the missing credential.

        *signed* is a REALIZATION, not a flag
        (:data:`tests.helpers.signing.SIGNATURE_REALIZATIONS`). This is the one place
        any leg signs, so it is the one place the four realizations are spelled out,
        and every leg gets all four for free:

        * ``False`` — no signature headers at all;
        * ``True`` — a real signature over exactly the bytes returned;
        * ``"malformed"`` — :data:`~tests.helpers.signing.MALFORMED_SIGNATURE_HEADERS`
          and NO real signature: both headers present, neither parseable, which is the
          verifier's step-1 pre-check failure and rejects in every bucket;
        * ``"tampered"`` — a real signature over
          :func:`~tests.helpers.signing.tampered_signing_body` of those bytes, with the
          ORIGINAL bytes returned. Well-formed headers, real crypto, and a
          ``content-digest`` that covers a body the wire does not carry: a CHECKLIST
          failure rather than a header rejection, which is the distinction ``warn_for``
          turns on.

        Rule 2 above holds for all four: the credential and the tenant hint are carried
        whether or not a signature is, so a realization is the ONLY variable.
        Everything else about the request is byte-identical to the ``True`` control.

        Returns ``(raw_bytes, headers)``; the caller owns the verb and the client.
        """
        import json as _json

        from tests.helpers.signing import (
            MALFORMED_SIGNATURE_HEADERS,
            WIRE_ORIGIN,
            realization,
            request_headers,
            signed_headers,
            tampered_signing_body,
        )

        # Re-checked here rather than trusted from ``call_via``: this seam is also
        # reached directly (``_a2a_credential_registration``, the e2e dispatcher), and a
        # realization that arrived by one of those paths must not fall through to the
        # correct-signature arm below.
        signed = realization(signed)
        capability = self.signing
        token = capability.token if credentialed else None
        # ``body=None`` is a BODYLESS request (a GET with no parameters), not an
        # empty JSON document: it must sign — and send — zero bytes, or the
        # content-digest covers a ``{}`` the wire does not carry.
        raw = b"" if body is None else _json.dumps(body).encode()
        # The tenant hint names THIS env's tenant on every leg. ``request_headers``
        # otherwise injects the module-level ``sig_tenant``, which is a lie for any
        # env whose tenant comes from its own Givens — and a lie inside the signed
        # byte range, one ladder reordering away from collapsing the posture bucket
        # to ``none`` and answering an unverified pass-through with a 200. Stated
        # once here rather than per-leg, so no leg can forget it (``RestE2EDispatcher``
        # used to carry its own copy).
        merged = {"Content-Type": "application/json", "x-adcp-tenant": self._tenant_id, **(extra or {})}
        if signed is False:
            return raw, request_headers(token, merged)
        if signed == "malformed":
            # No real signature is computed at all: the shape under test is headers
            # that cannot be PARSED, and a parseable one beside them would be graded
            # instead. Merged onto the same base every other realization carries, so
            # the malformed request differs from the True control by the signature
            # headers alone.
            return raw, {**request_headers(token, merged), **MALFORMED_SIGNATURE_HEADERS}
        # ``"tampered"`` signs over a MUTATED COPY and sends the caller's own bytes;
        # ``True`` signs over exactly what it sends. One call, because everything else
        # about the two — key, kid, origin, method, header set — must be identical or
        # the mismatch would not be attributable to the body.
        signed_over = tampered_signing_body(raw) if signed == "tampered" else raw
        return raw, signed_headers(
            capability.private_key,
            token,
            # ``@method`` is inside the signature base, so a signature made as
            # POST and sent as GET is refused as request_signature_invalid.
            method=method,
            path=path,
            body=signed_over,
            extra=merged,
            key_id=capability.key_id,
            origin=origin or WIRE_ORIGIN,
        )

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

        Delegates envelope and legacy-flat parsing to the shared
        ``_envelope_to_adcp_error`` helper (same path used by the A2A
        unwrapper) so REST and A2A reconstruction stay byte-identical.
        Falls back to HTTP status mapping only when no ``error_code`` is
        recoverable from the body.
        """
        message = data.get("message", data.get("error", str(data)))
        # FastAPI request-validation failures use a {"detail": [...]} envelope
        # with no error_code; surface a readable message from the first detail.
        if "message" not in data and "error" not in data and isinstance(data.get("detail"), list) and data["detail"]:
            first = data["detail"][0]
            if isinstance(first, dict) and first.get("msg"):
                message = first["msg"]

        reconstructed = _envelope_to_adcp_error(data, fallback_message=message)
        if reconstructed is not None:
            return reconstructed

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
            422: AdCPValidationError,  # FastAPI request-validation envelope ({"detail": [...]})
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

    def _seed_e2e_identity(self) -> None:
        """Seed tenant + principal into the server DB for discovery scenarios (e2e).

        Discovery scenarios (list_creative_formats, get_products) never run a
        Given step that creates a tenant/principal — in-process they don't need
        one (identity is a mock). Over e2e the live HTTP server authenticates the
        request against its own DB, so the buyer's tenant/principal/token MUST
        exist there or auth fails before the handler runs.

        Called from ``__enter__`` in e2e mode. Delegates to the idempotent
        ``setup_default_data`` (get-or-create) so it shares ONE seeding path and
        envs that also call ``setup_default_data()`` themselves don't
        double-create. Seeds the SAME ``tenant_id`` / ``principal_id`` the env's
        identity uses, so the token ``identity_for`` later resolves matches the
        seeded row.
        """
        if not self._session:
            return
        # Only IntegrationEnv exposes setup_default_data; e2e mode is always
        # an IntegrationEnv (use_real_db=True), so this is the seeding path.
        setup = getattr(self, "setup_default_data", None)
        if setup is not None:
            setup()
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

            # E2E mode connects directly to the specified database (the live
            # server's Postgres via e2e_config.postgres_url) instead of the cached
            # engine, so factory writes land in the DB the HTTP server reads.
            if self._database_url:
                from sqlalchemy import create_engine

                from src.core.database.database_session import _pydantic_json_serializer

                self._e2e_engine = create_engine(
                    self._database_url, echo=False, json_serializer=_pydantic_json_serializer
                )
                engine = self._e2e_engine
            else:
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

        # 3. E2E discovery-path seeding: the live server authenticates against
        #    its own DB, so seed tenant/principal even for scenarios that never
        #    run a tenant-creating Given step. Idempotent; no-op in-process.
        if self.use_real_db and self.is_e2e:
            self._seed_e2e_identity()

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

            # Dispose the per-env E2E engine: each e2e-mode env creates its own
            # engine, and closing the session alone leaves its pool's connections
            # open for the rest of the worker's life. With the ledger retirement
            # (~300 more scenarios building e2e envs per run, PR #1430 review),
            # thousands of scenarios x 8 workers exhausted Postgres
            # max_connections ("sorry, too many clients").
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

    def setup_default_data(self, **tenant_kwargs: Any) -> tuple[Any, Any]:
        """Get-or-create default tenant + principal via factories.

        Must be called inside the ``with env:`` block (factories are bound
        to the session during ``__enter__``).

        Returns (tenant, principal) ORM instances. Uses self._tenant_id
        and self._principal_id from constructor. Idempotent: reuses existing
        rows rather than re-creating, so it is safe to call after the e2e
        discovery-path auto-seed (``_seed_e2e_identity``) already created them.

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
        return tenant, principal

    def configure_tenant_field(self, field: str, value: Any) -> None:
        """Write a tenant-level config field for both auth paths.

        Updates the in-memory tenant overrides (mock identity path) AND the
        DB Tenant row when the column exists (real MCP/A2A auth chain reads
        the DB via config_loader). Clears the identity cache so the next
        ``identity_for`` re-resolves with the new value.
        """
        self._tenant_overrides[field] = value
        self._identity_cache.clear()

        if self._session:
            from src.core.database.models import Tenant

            tenant = self._session.get(Tenant, self._tenant_id)
            if tenant is not None and hasattr(tenant, field):
                setattr(tenant, field, value)
                self._session.commit()

    # -- Public query API (step functions must use these, not env._session) ----

    @property
    def tenant_id(self) -> str:
        """This env's tenant id — public so tests never read ``env._tenant_id``.

        Needed by any test that has to build a tenant-scoped production object (a
        repository, a signing key) for the SAME tenant the env is driving; reaching for
        the private attribute is how a test ends up scoped to a different tenant than the
        code it is grading.
        """
        return self._tenant_id

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
