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
import uuid
from collections.abc import Callable, Sequence
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Self, cast, get_type_hints
from unittest.mock import AsyncMock, MagicMock, patch

from tests.harness._realize import e2e_unsupported, realize_e2e

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
    from tests.helpers.signing import SignatureRealization


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


def _presents_a_credential(credential: Any) -> bool:
    """Whether a caller-supplied headers mapping actually carries a CREDENTIAL.

    ``bool(credential)`` is not that question, and the difference is the whole bug this
    replaced. ``env.credential(token=None)`` -- what every "the Buyer has no authentication
    credentials" Given passes -- returns ``{"x-adcp-tenant": "..."}``: a non-empty mapping
    that presents NO credential. Truthiness read that as credentialed, so the signing legs
    attached the capability's bearer to a call the scenario had asked to make anonymous, the
    request became authenticated, and security.mdx :1269 then makes an unsigned authenticated
    request a spec-correct 200. The refusal never fired and the scenarios reported, accurately,
    that the request was ACCEPTED.

    A tenant hint addresses a seller. It does not authenticate anyone. Only the credential
    header does, which is what this reads -- case-insensitively, because the harness and
    production both spell it either way on different legs.
    """
    if not credential:
        return False
    return any(str(name).lower() == "authorization" for name in credential)


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

    *response* is the raw HTTP response this envelope arrived on, when it arrived on
    one. An envelope is not always the whole refusal: ``WWW-Authenticate: Signature
    error="<code>"`` lives in the HEADERS, so a leg that raised the envelope alone
    left ``assert_signature_challenge`` with nothing to read and it refused to grade
    (correctly -- tests/CLAUDE.md, assert on the wire, never on a reconstruction).
    ``None`` on the in-process legs, which have no response to carry.
    """

    def __init__(self, envelope: dict, response: Any = None) -> None:
        self.envelope = envelope
        self.response = response
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


def _mcp_wire_error(exc: Exception, response: Any = None) -> Exception:
    """The :class:`WireError` an MCP failure stands for, or *exc* unchanged.

    The one place the HTTP leg's two error branches (a JSON-RPC ``error`` frame and an
    ``isError`` result) turn into a raise, so neither can start grading something the
    other does not. It reads the envelope with :func:`_mcp_wire_envelope` — the SAME
    parse the in-memory leg uses — rather than rebuilding a typed production exception
    from wire bytes: what the buyer received is the envelope, and reconstruction was
    lossy for most codes.

    *response* is the raw HTTP response, carried through onto the ``WireError`` so the
    refusal's HEADERS survive the raise alongside its body — see
    :class:`WireError`. Absent on the in-memory leg, which has no response.
    """
    envelope = _mcp_wire_envelope(exc)
    return WireError(envelope, response) if envelope is not None else exc


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

#: ``/a2a`` speaks NATIVE a2a-sdk 1.0 ONLY. ``src/app.py`` builds the route with
#: ``create_jsonrpc_routes`` and deliberately does NOT pass ``enable_v0_3_compat``
#: (the comment there records why: the 0.3 adapter's catch-all rebuilt every raised
#: exception as ``CoreInternalError``, discarding the AdCP envelope an auth refusal
#: carries, so a refusal answered 200 instead of 401). Without the version header the
#: SDK's ``validate_version`` reads the request as 0.3 and the handler raises
#: ``VersionNotSupportedError``.
_A2A_VERSION_HEADER = {"A2A-Version": "1.0"}

#: The A2A task states both legs normalize onto, keyed by the PROTOBUF ENUM NAME.
#: That key is what proto JSON puts on the HTTP wire verbatim, and what
#: ``TaskState.Name()`` gives the in-process leg for the same enum — so one table
#: serves both and the two legs cannot disagree about which outcome a task had.
#: Anything absent normalizes to ``""``, which ``_a2a_task_outcome`` reads as
#: "completed, grade the artifact".
_A2A_TASK_STATES = {"TASK_STATE_FAILED": "failed", "TASK_STATE_SUBMITTED": "submitted"}

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
    "a2a": "the AdCP request body's push_notification_config (the SendMessage DataPart)",
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


def _a2a_jsonrpc_body(method: str, params: Any) -> dict[str, Any]:
    """One JSON-RPC 2.0 frame for ``/a2a``, with *params* rendered by proto JSON.

    THE producer for this leg, and the reason it takes a protobuf message rather
    than a dict: ``JsonRpcDispatcher`` ``ParseDict``s ``params`` back into the model
    ``METHOD_TO_MODEL`` names, so a field this harness spells by hand can differ
    from the one the server parses and the difference is silent. Rendering the
    SDK's own message through ``json_format`` makes the wire body what a real 1.0
    client sends, and a renamed field an error here rather than a dropped value
    there.
    """
    from google.protobuf import json_format

    return {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": method,
        "params": json_format.MessageToDict(params),
    }


def _a2a_message_send_body(skill_name: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """The native 1.0 ``SendMessage`` JSON-RPC envelope naming *skill_name* explicitly.

    ``SendMessage``, not the 0.3 ``message/send``: ``src/app.py`` builds ``/a2a``
    without ``enable_v0_3_compat``, so the 0.3 family is not served at all and a
    frame naming it is answered ``-32601 Method not found`` at HTTP 200 — no
    handler, no boundary, no ``_resolve_identity``, and therefore no verifier. A
    signing scenario reads that as a seller that declined to refuse. See
    :data:`_A2A_VERSION_HEADER` for the companion header.

    The message is built by ``create_a2a_message_with_skill`` — the SAME helper the
    in-process leg calls (``_run_a2a_handler``) — so the two legs differ by how the
    frame reaches the server and by nothing else. Explicit-skill invocation is the
    ``data`` part shape ``{"skill": ..., "parameters": ...}``
    (``src/a2a_server/adcp_a2a_server.py``), which is also what the signing layer
    names the operation from.

    A ``push_notification_config`` travels in *parameters*, with every other field of
    the AdCP request, because that is where AdCP declares it and where PRODUCTION reads
    it on this transport as on the other three: the boundary reads it off the VALIDATED
    request (``src/core/signing/webhook_credentials.py``), and on A2A the AdCP request
    is the DataPart.

    This used to lift it into ``params.configuration.task_push_notification_config`` on
    the stated grounds that ``on_message_send`` read it there. It did not — it read
    ``params.message.parts`` and nothing else, so everything sent to the envelope was
    silently dropped and every A2A webhook scenario graded a registration that never
    happened. Production now DECLINES that envelope field outright
    (``AdCPRequestHandler._refuse_envelope_push_config``), which is what the agent card's
    ``push_notifications=False`` always said: AdCP defines no protocol-envelope
    registration channel, so this transport has exactly one place to put a webhook and
    it is the same place as everywhere else.
    """
    from a2a.types.a2a_pb2 import SendMessageRequest

    from tests.utils.a2a_helpers import create_a2a_message_with_skill

    request = SendMessageRequest(message=create_a2a_message_with_skill(skill_name=skill_name, parameters=parameters))
    return _a2a_jsonrpc_body("SendMessage", request)


def _a2a_jsonrpc_result(response: Any) -> dict[str, Any]:
    """The ``result`` of an ``/a2a`` answer, or its ``error`` raised as a CARRIER.

    One reader for every method this harness POSTs to ``/a2a``. Both refusal arms
    carry *response*, and that is the whole contract of this function:

    * an HTTP refusal that never produced a JSON-RPC envelope at all surfaces as
      :class:`WireRefusal` (raised by :func:`_jsonrpc_body`);
    * a JSON-RPC ``error`` frame surfaces as :class:`WireError` when its ``data``
      holds a real two-layer AdCP envelope, and as :class:`WireRefusal` when it does
      not -- the SDK's own protocol errors (``-32601 Method not found``,
      ``PushNotificationNotSupportedError``) carry no ``adcp_error``, and
      SYNTHESIZING one would hand a scenario an envelope the seller never emitted;
    * anything else is the result object.

    It used to raise a reconstructed ``AdCPSalesAgentError`` here and DROP the
    response. That is the lossy reconstruction tests/CLAUDE.md § "Error
    Verification Policy" rules out: an error frame answered at HTTP 401 carries its
    ``WWW-Authenticate: Signature error="<code>"`` in the headers, not in the body,
    so raising past the response left ``assert_signature_challenge`` with nothing to
    read and it refused to grade -- reporting a real wire refusal as "no wire".
    """
    envelope = _jsonrpc_body(response, surface=_A2A_PATH)
    if "error" in envelope:
        error = envelope["error"]
        data = error.get("data") if isinstance(error, dict) else None
        wire = _wire_envelope(data) if isinstance(data, dict) else None
        if wire is not None:
            raise WireError(wire, response)
        raise WireRefusal(f"{_A2A_PATH} answered JSON-RPC error: {error!r}", response)
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
    """The exception an MCP error frame stands for, in the shape the envelope reader takes.

    :func:`_mcp_wire_envelope` reads the two-layer envelope out of the JSON FastMCP
    puts in ``str(ToolError)``. Over HTTP that envelope arrives as the ``isError``
    result's text content (or as a JSON-RPC ``error.message``), so it is re-wrapped in
    a ``ToolError`` here and handed to the SAME reader the in-memory leg uses — one
    parse, not two.
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


def _a2a_task_push_notification_config(spec: Any, *, task_id: str = "") -> Any:
    """An AdCP ``push_notification_config`` as the A2A protocol layer carries it.

    THE ONE translation for the A2A protocol envelope, reached through
    :func:`_a2a_send_message_configuration`. A webhook a buyer really registers with
    this seller does NOT come through here — it is a declared AdCP field and travels
    in the request like any other, on this transport as on the rest. What is left is
    the protocol channel the seller DECLINES
    (``AdCPRequestHandler._refuse_envelope_push_config``), which a test can still
    address explicitly through ``a2a_push_notification_config``.

    The two vocabularies name the same thing differently and the translation is the
    TRANSPORT's, not the scenario's. AdCP's ``Authentication`` is ``{schemes: [...],
    credentials}`` — plural, ``minItems``/``maxItems`` 1, ``additionalProperties:
    false`` (``adcp.types.Authentication``). A2A's wire type is the protobuf
    ``AuthenticationInfo``, ``{scheme, credentials}``, SINGULAR. *spec* is the AdCP
    shape a step writes; the singular spelling is produced here and nowhere else.

    Absent credentials are sent as the protobuf default (empty string) rather than
    omitted, because that is what a buyer's client actually puts on the wire for an
    unset proto3 string — the field cannot be "missing".
    """
    from a2a.types import AuthenticationInfo, TaskPushNotificationConfig

    raw = spec.model_dump(mode="json", exclude_none=True) if hasattr(spec, "model_dump") else dict(spec)
    fields: dict[str, Any] = {"url": raw["url"]}
    if task_id:
        fields["task_id"] = task_id
    authentication = raw.get("authentication")
    if authentication is not None:
        schemes = list(authentication.get("schemes") or [])
        fields["authentication"] = AuthenticationInfo(
            scheme=schemes[0] if schemes else "",
            credentials=authentication.get("credentials") or "",
        )
    return TaskPushNotificationConfig(**fields)


def _a2a_send_message_configuration(spec: Any) -> Any:
    """Build the A2A ``SendMessageConfiguration`` carrying a protocol-level push config.

    ``SendMessage`` registers a webhook one level ABOVE the AdCP tool parameters:
    ``params.configuration.task_push_notification_config``
    (``src/a2a_server/adcp_a2a_server.py`` — ``on_message_send`` reads it before any
    skill routing happens). It is therefore not reachable by putting a
    ``push_notification_config`` in the skill parameters, and it exists on no other
    transport — MCP and REST have no equivalent protocol envelope.
    """
    from a2a.types import SendMessageConfiguration

    return SendMessageConfiguration(task_push_notification_config=_a2a_task_push_notification_config(spec))


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


class _GuardedPatchers:
    """Append-only VIEW of the cleanup registry, in the ``.append(patcher)`` shape.

    Deliberately not a list. Call sites that predate the registry spell patcher
    registration as ``self._patchers.append(patcher)``
    (``tests/harness/capabilities.py``, ``tests/harness/signing_capability.py``);
    this keeps that spelling working while leaving exactly ONE teardown
    mechanism. Every append forwards straight into :meth:`BaseTestEnv._guard`,
    so a patcher registered through it is released by the SAME
    ``_release_entered`` pass as the database and the ``EXTERNAL_PATCHES``:
    reverse acquisition order, exactly once, on both release paths — including
    the ``__enter__``-failed path, where Python never calls ``__exit__``.

    Holding a private list here instead would reintroduce the split teardown the
    registry replaced. Two lists mean two unwind points, and the ORDERING
    BETWEEN them is lost: a patch taken after the DB session could no longer be
    guaranteed to stop before that session closes. Reverse acquisition order
    across all resources is the property that makes a partially-entered env
    safe, and it only holds while there is one list.
    """

    __slots__ = ("_env",)

    def __init__(self, env: BaseTestEnv) -> None:
        self._env = env

    def append(self, patcher: Any) -> None:
        """Register an ALREADY-STARTED *patcher*'s ``stop()`` with the registry.

        Mirrors the contract the call sites already rely on: they call
        ``patcher.start()`` and then append, so this registers teardown only —
        it never starts anything itself.
        """
        # `patch()` exposes `.attribute`; `patch.dict` and the ExitStack adapter
        # in signing_capability.py expose neither. The label is diagnostic only.
        label = getattr(patcher, "attribute", None) or type(patcher).__name__
        self._env._guard(f"patcher:{label}", patcher.stop)


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
        # through ``deliver_a2a``/``deliver_mcp``, whose ``**kwargs`` BECOME the
        # skill parameters / tool arguments — a ``signed=`` kwarg on that path
        # would arrive in an ``extra="forbid"`` payload rather than being
        # consumed. ``call_via`` sets it for exactly one dispatch. REST needs none
        # of this: its dispatcher calls ``_run_rest_request`` directly and passes
        # ``signed=``, which that method declares KEYWORD-ONLY so it is stripped
        # out of the kwargs ``build_rest_body`` sees. A ``_run_rest_request``
        # override that omits the declaration leaks ``signed`` into the body —
        # that defect has happened; the declaration is the contract.
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
        values: dict[str, Any] = {"tenant": self._tenant_id}
        if "token" not in overrides:
            values["token"] = self._principal_token()
        unknown = set(overrides) - {"token", "tenant"}
        if unknown:
            raise TypeError(f"credential() takes token and tenant, not {sorted(unknown)}")
        values.update(overrides)
        return credential_headers(**values)

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

    def call_via(self, transport: Transport, *, signed: SignatureRealization = False, **kwargs: Any) -> TransportResult:
        """Dispatch through *transport* and return normalized TransportResult.

        Presents this env's credential unless the caller passed ``credential=``
        (``{}`` sends no headers at all). Routes to the appropriate dispatcher.

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

        kwargs.setdefault("credential", self.credential())

        dispatcher = DISPATCHERS[transport]
        # No wire capture to reset: the success-path wire rides the RETURN VALUE
        # (``DeliverResult.wire_response``) rather than a per-env stash, so there
        # is no stale-capture window for a dispatch to open.
        #
        # ``signed`` travels BOTH ways on purpose. The REST leg takes it as an
        # explicit keyword the dispatcher forwards to ``_run_rest_request``
        # (declared keyword-only there, which is what strips it out of the kwargs
        # the body is built from); A2A/MCP read it back off the env, because their
        # dispatch goes through ``deliver_a2a``/``deliver_mcp`` whose kwargs become
        # skill parameters / tool arguments — a ``signed=`` kwarg on that path
        # would land in an ``extra="forbid"`` payload.
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

        One of TWO issuing paths, and both must record — see
        :meth:`record_capture_key_handed_out`.
        """
        from tests.e2e._webhook_capture import delivery_url

        url = delivery_url(self.webhook_capture_key)
        self.record_capture_key_handed_out()
        return url

    def record_capture_key_handed_out(self) -> None:
        """Note that this env's capture address was ISSUED to something.

        THE ONE RECORDER, called by every path that hands the address out, because
        there is more than one and a gate that reads the fact cannot tell which path
        produced it:

        * :meth:`_realize_e2e_webhook_destination` — the ``BaseTestEnv`` key
          (``webhook_capture_key``, minted lazily here);
        * ``_mixins._e2e_capture_url`` — the ``LocalOriginMixin`` key
          (``_capture_key``, registered in ``_enter_pre``), which is what every
          DELIVERY env actually issues, and which is reached through
          ``LocalOriginMixin.webhook_url``.

        The second path arrived with #1802 and did not record, while the gate that
        reads this (``_mixins._deliver_via_live_server``, deciding whether to attach
        ``MediaBuy.raw_request["reporting_webhook"]``) came from the other side of the
        same merge. Joined, the flag was permanently False for exactly the envs it
        governs: the live server was asked to report on a media buy carrying no
        reporting webhook, declined with "No reporting_webhook configured", and three
        graduated ``@T-UC-004-webhook-*`` e2e_rest legs failed as "No webhook POST was
        made" — a setup fact reading like a delivery defect.
        """
        self.__dict__["_webhook_capture_key_handed_out"] = True

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

        Recorded on every HANDING-OUT path (:meth:`record_capture_key_handed_out`,
        which lists them), never on a minting path: :attr:`webhook_capture_key` mints
        on demand, so a flag set there would only ever say "a key exists" — which a
        vacuous reader makes true by reading. The address being ISSUED is the fact; a
        key existing is not (salesagent-n78j0.1.4).
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
        carries the config. Any further entry would be a channel this transport
        offers that no operation dispatch touches; the env would send it here.

        TODAY EVERY TRANSPORT HAS EXACTLY ONE, and the A2A story is the reason this
        seam exists at all. A2A carries two protocol-level places a buyer could
        attach a webhook — ``params.configuration.task_push_notification_config`` on
        ``SendMessage``, and the standalone ``CreateTaskPushNotificationConfig``
        method — and this function used to return the second as a location of its
        own, on the stated grounds that the handler "persists the credentials and
        returns a config id".

        It does not, and has not since #1721. AdCP defines no protocol-envelope
        registration channel — a webhook is a declared field of the AdCP request —
        so this agent declines the whole A2A push-notification capability: the agent
        card advertises ``push_notifications=False`` and all five entry points refuse
        (the four ``tasks/pushNotificationConfig/*`` handlers, and
        ``on_message_send``'s envelope check, ``_refuse_envelope_push_config``).
        A place where a buyer CANNOT hand this seller credentials is not a credential
        location, and grading it for a signature challenge asks the verifier to
        enforce signing on a channel refused above it — which it never sees.

        THE LOCATION THAT MATTERS WAS THE ONE NOT BEING SENT TO. The A2A operation
        entry pointed at the protocol envelope too, so the AdCP field — the one place
        an A2A buyer really does register a webhook, and the one the :1462-1465
        escalation reads — was graded on no transport but MCP and REST. It is graded
        on all four now. That is the opposite of the coverage trade this docstring
        used to warn about: nothing moved off a surface the seller serves.

        If a transport grows a second SERVED location it is added HERE — the scenario
        text does not change, because the scenario's claim ("a registration carrying
        credentials is refused unless signed") never mentioned a location in the first
        place.

        *config* is the ``push_notification_config`` the operation dispatch carried;
        ``None`` means the caller registered no credentials. *signed* is kept in the
        signature for the same reason: an added location must be dispatched with the
        operation's OWN realization, failure realizations included, or it would be a
        different experiment from the one the scenario set up.
        """
        location = _OPERATION_CREDENTIAL_LOCATION.get(transport.value, _UNSTATED_CREDENTIAL_LOCATION)
        return ((location, operation_result),)

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
        (``_reset_e2e_db``), so a teardown-time DB write would have nothing to write
        through. (The release order is now the registry's — reverse acquisition, so
        patches taken after the session are stopped BEFORE it closes — but nothing
        here depends on that, precisely because no restore is registered.)
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

        Once the env CAN sign, this defers to ``_run_a2a_over_http``: an
        ``on_message_send`` call has no wire, so ``RequestSignatureMiddleware``
        (ASGI, above the whole app) never sees it and a signature would be
        unobservable. See ``can_sign`` for why the fork is on the capability and
        not on ``signed``.

        Args:
            skill_name: A2A skill name (e.g., "get_products").
            response_cls: Pydantic model class to parse artifact data into.
            **kwargs: Skill parameters. ``credential`` is popped and presented on the
                call context; ``a2a_push_notification_config`` is popped and sent as
                the protocol-level ``SendMessageConfiguration`` (see
                :func:`_a2a_send_message_configuration`) rather than as a skill
                parameter; remaining kwargs become skill parameters.
        """
        if self.can_sign:
            return self._run_a2a_over_http(skill_name, response_cls, **kwargs)

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

        parameters = self._a2a_skill_parameters(kwargs)

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

        from a2a.types import TaskState

        return self._a2a_task_outcome(
            # Through the SAME table the HTTP leg uses, keyed by the protobuf enum
            # NAME. The local dict this replaces was keyed by the enum VALUE and
            # mapped onto the v0.3 spelling, which was only shareable while the HTTP
            # leg spoke 0.3; it does not (``_a2a_message_send_body``).
            state=_A2A_TASK_STATES.get(TaskState.Name(task_result.status.state), ""),
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
        the three OUTCOMES (failed → :class:`WireError` carrying the envelope the
        buyer received, submitted → synthesized submitted wire, otherwise → the
        stripped artifact DataPart) are the same contract, and a second copy of
        them is how the two legs would drift into grading different things.

        *state* is the normalized A2A task state — the v0.3 spelling
        (``"failed"`` / ``"submitted"``), which the protobuf leg maps onto with
        the ``protobuf_states`` table in :meth:`_run_a2a_handler`.
        """
        # AdCP-domain errors surface as a FAILED Task with the two-layer envelope
        # in the artifact DataPart. The ENVELOPE is what is raised, not a
        # reconstructed production exception: rebuilding an AdCPSalesAgentError from
        # wire bytes graded our own class hierarchy through a lossy copy of
        # production's constructor (see :func:`_wire_envelope`), and the envelope is
        # what the buyer actually received.
        if state == "failed":
            from src.core.exceptions import AdCPInternalError

            if artifact_data:
                envelope = _wire_envelope(artifact_data)
                if envelope is not None:
                    raise WireError(envelope)
            # A failed task with no envelope artifact: nothing was caught, so there is
            # no exception to hand to ``internal_detail``; the code is the diagnosis.
            raise AdCPInternalError()

        if state == "submitted":
            # Async manual-approval path: the server returns a submitted Task with NO
            # artifacts (adcp_a2a_server.py:683) — the submitted envelope is conveyed by
            # the Task state + id, not an artifact union. Reconstruct the submitted wire
            # (protocol status="submitted" + the task_id the buyer polls) so success-path
            # grading sees the real A2A wire.
            # ``task_id`` is the parameter, not ``task_result.id``: this helper is
            # shared by BOTH A2A legs and the HTTP leg has no protobuf ``Task``
            # object to read an id off — normalizing the id at the call site is
            # what lets the two legs share one outcome contract.
            submitted_wire = {"status": "submitted", "task_id": task_id}
            return DeliverResult(payload=response_cls(**submitted_wire), wire_response=dict(submitted_wire))

        if artifact_data is None:
            raise ValueError(f"Task has no artifacts. Status: {status}")
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

    def _run_a2a_over_http(self, skill_name: str, response_cls: type, **kwargs: Any) -> Any:
        """A2A dispatch as a real POST to ``/a2a`` on ``src.app.app``.

        The leg the signing capability needs, and strictly MORE production than
        the in-process one: the request traverses ``UnifiedAuthMiddleware`` → the
        SDK's JSON-RPC route → the integer-restoring wrapper (``src/app.py``) → the
        same ``AdCPRequestHandler`` → ``invoke_tool`` → ``_resolve_identity``, where
        the verifier runs. Identity is not fabricated here — it is resolved from the
        real bearer ``wire_request`` puts on ``Authorization``, by the same
        ``AdCPCallContextBuilder`` a live buyer would meet.

        The wire method is the a2a-sdk 1.0 native ``SendMessage``, because that is
        the only family ``/a2a`` serves (``src/app.py`` passes no
        ``enable_v0_3_compat``). This leg used to send the 0.3 ``message/send`` on
        the reasoning that ``SendMessage`` is unrepresentable in a
        ``protocol_methods_*`` bucket (the pinned capabilities schema constrains
        those with ``pattern: ^[a-z][a-z0-9_]*/[a-z][a-z0-9_]*$``) and so could only
        land in ``none``, making a signing assertion vacuous. That reasoning
        described a verifier reading the JSON-RPC METHOD off an ASGI middleware, and
        #1721 has no such middleware: the verifier runs inside ``_resolve_identity``,
        which ``invoke_tool`` reaches only once ``TOOLS[tool_name]`` resolved, so the
        operation graded against the posture is the SKILL and the ``protocol_methods_*``
        namespace is never consulted at all (``verifier._bucket_for``, whose docstring
        states it structurally: no ``protocol_method`` is passed because there is no
        value at that boundary that COULD be graded against it).

        Meanwhile ``message/send`` was not merely representable but unserved: the
        dispatcher routes by method name before any version check, answers an unknown
        name ``-32601 Method not found`` at HTTP 200, and nothing downstream runs. A
        signing scenario read that as a seller that declined to refuse.
        """
        credential = self._pop_credential(kwargs)
        self._commit_factory_data()
        self._seed_ambient_tenant(credential)

        # ``push_notification_config`` is NOT lifted out: it is a declared field of the
        # AdCP request and travels in the DataPart with the rest of it, which is what the
        # in-process leg has always done and what production reads. See
        # ``_a2a_message_send_body``.
        parameters = self._a2a_skill_parameters(kwargs)
        body = _a2a_message_send_body(skill_name, parameters)
        # /a2a with NO trailing slash: src/app.py 307-redirects /a2a/, and httpx
        # would replay the pre-redirect signature against the new target-uri —
        # a genuine signature failing as request_signature_invalid.
        #
        # ``credential={}`` means "send without a credential" everywhere else in the
        # harness, so it decides ``credentialed`` here too rather than the leg
        # quietly attaching the capability's bearer to a call the caller asked to
        # make anonymous. It is the only way this leg reaches the composition rule's
        # refusal branch at all: security.mdx :1269 makes an unsigned request
        # carrying a valid bearer a spec-correct 200.
        raw, headers = self.wire_request(
            path=_A2A_PATH,
            body=body,
            # Merged BEFORE signing, like every other transport's framing headers, so
            # the header set the signature covers is the one sent (``wire_request``
            # rule 1 applied to headers).
            extra=dict(_A2A_VERSION_HEADER),
            signed=self._signed_dispatch,
            credentialed=_presents_a_credential(credential),
        )
        response = self.get_rest_client().post(_A2A_PATH, content=raw, headers=headers)

        result = _a2a_jsonrpc_result(response)
        # ``SendMessageResponse`` is a oneof: proto JSON renders the task arm as
        # ``{"task": {...}}``. Accept a bare task too, so a oneof rendered without its
        # wrapper does not silently parse as an empty task.
        task = result.get("task", result)
        artifacts = task.get("artifacts") or []
        artifact_data = _a2a_first_data_part(artifacts[0]) if artifacts else None
        return self._a2a_task_outcome(
            state=_A2A_TASK_STATES.get((task.get("status") or {}).get("state", ""), ""),
            status=task.get("status"),
            task_id=task.get("id", ""),
            artifact_data=artifact_data,
            response_cls=response_cls,
        )

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

        Once the env CAN sign, this defers to ``_run_mcp_over_http``: FastMCP's
        in-memory transport is a pair of anyio object streams with no HTTP, no
        ASGI and no headers, so ``RequestSignatureMiddleware`` — registered on
        ``src.app.app`` — is not on that path at all. See ``can_sign``.

        Args:
            tool_name: MCP tool name (e.g., "get_products").
            response_cls: Pydantic model class to parse structured_content into.
            **kwargs: Tool arguments. ``credential`` is popped and presented as the
                request headers; ``req`` is popped and its fields unpacked into the
                arguments dict.
        """
        if self.can_sign:
            return self._run_mcp_over_http(tool_name, response_cls, **kwargs)

        import asyncio
        from unittest.mock import patch

        from fastmcp import Client

        from src.core.main import mcp
        from tests.harness.transport import NO_IDENTITY_OVERRIDE

        self._commit_factory_data()

        credential = kwargs.pop("credential", NO_IDENTITY_OVERRIDE)
        if credential is NO_IDENTITY_OVERRIDE:
            credential = self.credential()

        arguments = self._mcp_tool_arguments(kwargs)

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

    @staticmethod
    def _mcp_tool_arguments(kwargs: dict[str, Any]) -> dict[str, Any]:
        """Flat MCP tool arguments from dispatch kwargs (``req`` unpacked).

        MCP tools accept individual params, not a request model; explicit kwargs
        win over ``req`` fields. Shared by the in-memory leg and the HTTP leg so
        one call cannot become two different tool invocations.

        NO NARROWING. This used to filter the DTO's fields down to a hand-written
        wrapper's parameter list -- "accepted = DTO fields INTERSECT wrapper
        parameters" -- which is exactly the intersection the registry removed. The
        DTO IS the accepted shape on every transport now, so a field the request
        carries is a field the tool takes, and filtering could only drop one.
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
        from tests.helpers.app_state import preserved_global_app_state

        credential = self._pop_credential(kwargs)
        self._commit_factory_data()
        self._seed_ambient_tenant(credential)

        arguments = self._mcp_tool_arguments(kwargs)

        # ``credential={}`` is "send without a credential" (see ``_run_a2a_over_http``);
        # it applies to the handshake frames too, because a session opened under a
        # bearer and used without one would differ from the anonymous request under
        # test by more than the credential.
        credentialed = _presents_a_credential(credential)
        with preserved_global_app_state(), TestClient(app) as client:
            session_id = self._mcp_open_session(client, credentialed=credentialed)
            envelope, response = self._mcp_post(
                client,
                _mcp_jsonrpc_request(2, "tools/call", {"name": tool_name, "arguments": arguments}),
                session_id=session_id,
                credentialed=credentialed,
            )

        # Both arms hand the RESPONSE to the error they raise. They used to raise the
        # reconstructed envelope alone, which strands the refusal's headers — and the
        # signature challenge lives there.
        if "error" in envelope:
            raise _mcp_wire_error(_mcp_error_to_exception(envelope["error"]), response)
        result = envelope.get("result") or {}
        if result.get("isError"):
            raise _mcp_wire_error(_mcp_error_to_exception(result), response)
        structured = result.get("structuredContent")
        if structured is None:
            raise AssertionError(f"MCP tools/call for {tool_name!r} returned no structuredContent: {result!r}")
        # A ``DeliverResult``, not a bare payload: this method is a drop-in
        # substitute for the in-memory leg inside ``_run_mcp_client``, whose
        # return value the dispatchers read the wire off. Returning the response
        # alone would strand the real HTTP wire on the one leg that actually has
        # one.
        return DeliverResult(payload=response_cls(**structured), wire_response=structured)

    def _mcp_open_session(self, client: Any, *, credentialed: bool = True) -> str | None:
        """Run the handshake and return the server's session id, if it mints one.

        ``initialize`` mints the session; a server that tracks one REFUSES any request
        before ``notifications/initialized`` confirms it ("Received request before
        initialization was complete"), so both frames are sent — and both are
        signed, because once the env can sign every byte it puts on this wire
        carries a signature.

        A SESSION IS OPTIONAL, and this mount has none. ``src/app.py`` builds the
        ``/mcp`` mount with ``stateless_http=True`` (#1721, "delete the MCP auth gate"):
        the SDK's session manager then makes a FRESH transport per request with
        ``mcp_session_id=None`` and a session that is already in
        ``InitializationState.Initialized`` (``mcp/server/streamable_http_manager.py``
        ``_handle_stateless_request`` -> ``ServerSession(stateless=True)``), so no
        ``mcp-session-id`` header comes back and nothing has to confirm the handshake.
        That is the wire's own declaration of which mount it is, which is why it is read
        from the response rather than from a flag the harness would have to keep in sync
        with production. The pre-merge mount was stateful, so this leg USED to require
        the header; requiring it against #1721's mount fails every signed MCP dispatch
        with "MCP initialize returned no mcp-session-id" on an initialize that answered
        a perfectly good 200.

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
        if response.status_code >= 400:
            # WireRefusal, not a bare AssertionError: a handshake frame REFUSED by a
            # middleware above the mount (the verifier's bodyless 401 is the one that
            # matters here) carries its reason only in the response, and dropping it
            # would report a handshake fault for a refusal the caller is entitled to
            # grade. It is the STATUS that says "refused", not the absent session
            # header — a stateless mount omits that header on a 200.
            raise WireRefusal(
                f"MCP initialize was refused with HTTP {response.status_code}: {response.text[:800]!r}", response
            )
        # Read the envelope even though the session id is what we need: an
        # ``initialize`` that answered an ERROR still carries a session header,
        # and a handshake that failed must surface here rather than as an
        # inexplicable refusal three frames later.
        handshake = _jsonrpc_body(response, surface=_MCP_PATH)
        if "error" in handshake:
            raise AssertionError(f"MCP initialize failed: {handshake['error']!r}")

        session_id = response.headers.get("mcp-session-id")
        if not session_id:
            # Stateless mount: there is no session to confirm, and the next frame's
            # transport is initialized before it is dispatched. Sending
            # ``notifications/initialized`` anyway would put a frame on the wire that
            # nothing reads and that the operation frame does not depend on.
            return None

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
    ) -> tuple[dict[str, Any], Any]:
        """POST one MCP frame and return ``(JSON-RPC envelope, raw response)``.

        THE GRADED FRAME: the realization reaches the wire VERBATIM here
        (``self._signed_dispatch``, not ``bool(...)``). ``_run_mcp_over_http`` sends
        the ``tools/call`` — the frame the scenario named — through this method and
        nothing else through it.

        The response travels back BESIDE the envelope rather than being dropped: an
        envelope is not always the whole refusal, because ``WWW-Authenticate:
        Signature error="<code>"`` is a HEADER. Returning the envelope alone left the
        caller nothing to attach to the error it raised.
        """
        response = self._mcp_send(
            client, body, session_id=session_id, credentialed=credentialed, signed=self._signed_dispatch
        )
        return _jsonrpc_body(response, surface=_MCP_PATH), response

    def _pop_credential(self, kwargs: dict[str, Any]) -> dict[str, str]:
        """Pop ``credential`` from dispatch kwargs, defaulting to this env's credential.

        ``{}`` is a caller's explicit "no headers at all" and is returned as such; only
        an ABSENT keyword falls back to ``credential()``.
        """
        from tests.harness.transport import NO_IDENTITY_OVERRIDE

        credential = kwargs.pop("credential", NO_IDENTITY_OVERRIDE)
        return self.credential() if credential is NO_IDENTITY_OVERRIDE else dict(credential)

    def _run_rest_request(self, endpoint: str, *, signed: SignatureRealization = False, **kwargs: Any) -> Any:
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

        if not self.can_sign:
            if signed:
                self.signing  # raises, naming enable_request_signing()  # noqa: B018
            # The credential travels as HEADERS even on the unsigned leg, and that is
            # ``credential()``'s whole job: the bearer AND ``x-adcp-tenant``, on every
            # leg. The dependency-override era posted bare here and had to bolt the
            # tenant hint back on, because a FastAPI dep runs INSIDE the app while
            # ``RequestSignatureMiddleware`` sits at the ASGI boundary OUTSIDE it — so a
            # bare post reached the verifier anonymous, ``_detect_tenant_for_posture``
            # answered ``(None, None)``, the posture fell back to ``supported=True``, and
            # a request carrying ``push_notification_config.authentication`` was refused
            # with a bodyless 401 before the ingest gate ran. The in-process MCP and A2A
            # legs never traverse the ASGI stack, so they did not see it — the four legs
            # were not running the same scenario.
            #
            # ``x-adcp-tenant`` is the load-bearing header there, not the bearer:
            # ``resolved_identity._detect_tenant`` resolves a tenant from Host -> virtual
            # host/subdomain, ``x-adcp-tenant``, ``Apx-Incoming-Host`` or a localhost
            # fallback, NEVER from the auth token, and the in-process ``TestClient`` sends
            # ``Host: testserver``, which matches no virtual host, no subdomain and is not
            # localhost. The bearer rides along because production sends it and the SIGNED
            # branch below already does (security.mdx :1269 — an unsigned request carrying
            # a valid bearer is a spec-correct 200).
            return client.post(endpoint, json=body, headers=credential)

        # ``credential={}`` already means "send without a credential" everywhere else in
        # the harness (``_pop_credential`` returns it verbatim, and ``RestE2EDispatcher``
        # reads it the same way) — so it decides ``credentialed`` here too, rather than
        # the signed path quietly attaching the capability's bearer to a call the caller
        # asked to make anonymous. It is the ONLY way an in-process leg reaches the
        # verifier's refusal branch at all: security.mdx :1269 makes an unsigned request
        # carrying a valid bearer a spec-correct 200.
        raw, headers = self.wire_request(
            path=endpoint, body=body, signed=signed, credentialed=_presents_a_credential(credential)
        )
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
        # reached directly (the e2e dispatcher), and a realization that arrived by one
        # of those paths must not fall through to the correct-signature arm below.
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
        double-create. Seeds the SAME ``tenant_id`` / ``principal_id`` the env
        presents, so the token ``credential()`` later reads is the seeded row's.
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

            # 3. E2E discovery-path seeding: the live server authenticates against
            #    its own DB, so seed tenant/principal even for scenarios that never
            #    run a tenant-creating Given step. Idempotent; no-op in-process.
            if self.use_real_db and self.is_e2e:
                self._seed_e2e_identity()

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

    @property
    def _patchers(self) -> _GuardedPatchers:
        """The registry, in the legacy ``_patchers.append(patcher)`` shape.

        A property, not an attribute, so that there is nothing to keep in sync:
        it cannot drift from ``_enter_cleanups`` and cannot be reassigned to a
        real list that would then be unwound by nobody. See
        :class:`_GuardedPatchers` for why this is a view rather than a second
        list.
        """
        return _GuardedPatchers(self)

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
