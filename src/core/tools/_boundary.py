"""The one path from a validated request to a response.

Every transport enters here. MCP, A2A and REST differ in how a request ARRIVES and how a
response is written back; between those two points there is one sequence, and this is it:
resolve the account the request names, honour the idempotency key it carries, run the
implementation.

## Why these two things live here and not in an implementation

Both are properties of the REQUEST rather than of the work. ``account`` names whose
inventory the caller is acting on; ``idempotency_key`` says "if you have already done this,
do not do it again". Neither is a step in creating a media buy or syncing a creative, and an
implementation that performs them has to be TOLD it is being called by a buyer. A value every
caller must remember to supply is a value some caller will forget, and a hash taken over
whatever each transport happened to hold makes "the same request" a per-transport answer.
Nothing internal passes through here, so an in-process call never inherits a caller's key.

## The idempotency rule, entire

Save a non-error response that carried a key. On a later request with the same key: same
payload replays it verbatim, different payload is IDEMPOTENCY_CONFLICT.

The digest is ``canonical_request_hash`` over the VALIDATED request -- ``req.model_dump``,
after the DTO has parsed it. Equivalence is therefore over the request as this seller
understands it: two spellings of one instant are one payload, and a field the pinned schema
does not define is dropped by ``extra="ignore"`` before hashing, so it cannot distinguish two
requests either. That is deliberate. What the seller deliberately discards cannot be part of
what it compares, and the alternative -- canonicalising the received bytes before validation
-- needs a capture point per transport, which is the arrangement whose failure this seam
exists to fix. On top of that the spec's closed exclusion list is stripped
(``idempotency_key``, ``context``, ``governance_context``, and
``push_notification_config.authentication.credentials``), so a key never hashes itself and a
rotated webhook credential does not turn a retry into a conflict.

Errors are never saved, and that is a property of control flow rather than a check: every
implementation RAISES on failure, and a raise never reaches the save. A returned result IS a
success.

Idempotency is scoped to (agent, account, key) per the spec, with no tool dimension.

## Failure

A failure is a RESPONSE. ``core/protocol-envelope.json`` declares ``adcp_error``, ``context``
and a required ``status`` on every response envelope, so what a transport writes for a refused
request is the same kind of object it writes for a served one. ``failure_response`` builds it
-- once, recording the fault first -- and ``_failed`` raises it as ``AdcpFailure``, the one
exception a transport catches. A transport adds only its marker: an HTTP status, a
``ToolError``, a Task state.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import typing
from collections.abc import Callable, Mapping
from typing import Any, NoReturn

from adcp.types import ContextObject
from pydantic import ValidationError

from src.core.exceptions import AdcpFailure, adcp_error_for
from src.core.idempotency_canonical import canonical_request_hash
from src.core.idempotency_replay import cache_success, lookup_cached_replay, maybe_evict_expired
from src.core.resolved_identity import PublicIdentity, TransportProtocol
from src.core.schemas._base import AdcpErrorResponse, AdcpResponse, BuyerRequest
from src.core.signing.capture import HttpExchange, SignatureSubject
from src.core.signing.webhook_credentials import log_arriving_webhook_credentials, registers_webhook_credentials
from src.core.tool_error_logging import record_boundary_error
from src.core.version_negotiation import SERVED_ADCP_VERSION, negotiate_adcp_version

logger = logging.getLogger(__name__)


def _response_model_for(impl: Callable[..., Any]) -> type[AdcpResponse] | None:
    """The model an implementation returns, read off its annotation.

    Derived rather than declared: a registry row says which DTO a tool ACCEPTS, and the
    implementation's own signature already says what it returns. Storing the response type
    a second time would be a second declaration that can disagree with the function.

    Narrowed to ``AdcpResponse``, which is what an AdCP response IS -- every pinned response
    schema composes the version and protocol envelopes at its root, and that base carries both.
    The narrowing is what lets this module ASSIGN ``adcp_version`` and ``replayed`` and CALL
    ``revive`` without probing for them; ``_register_tool`` refuses at import any tool whose
    response does not descend from it, so the annotation cannot quietly become a lie.

    ``None`` when the callable has no readable annotations, rather than a raise: the only
    consequence is that a cached envelope cannot be revived, so the request executes fresh --
    the same degradation a stale envelope already gets. Raising here would turn a callable
    ``get_type_hints`` cannot read into a failed request.
    """
    try:
        hints = typing.get_type_hints(impl)
    except Exception:
        return None
    annotation = hints.get("return")
    if isinstance(annotation, type) and issubclass(annotation, AdcpResponse):
        return annotation
    return None


def _deserializer_for(impl: Callable[..., Any]) -> Callable[[dict[str, Any]], Any | None]:
    """Turn a stored envelope back into a typed response, or None if it no longer validates.

    ``AdcpResponse.revive`` resolves the stored body to its type -- for a tool whose schema is
    a ``oneOf``, to the BRANCH the buyer originally received.

    None means "treat as a miss": a stored envelope that stopped validating -- because the
    response model changed between the deploy that wrote it and the one replaying it, inside
    the TTL window -- must fall through to fresh execution rather than fail a request.

    The spec's ``replayed`` marker is set here, which is the only place that can: the marker
    says "you are seeing a stored answer", so it is a property of the REPLAY and never of the
    stored body -- AdCP L1/security rule 4 puts it on the outgoing envelope for exactly that
    reason, and the cached body stays clean so repeated replays of one key each carry it once.

    A plain assignment, with no check that the field exists: every response model inherits
    ``AdcpResponse``, which ``_register_tool`` refuses to register a tool without.
    """
    model = _response_model_for(impl)

    def deserialize(envelope: dict[str, Any]) -> Any | None:
        if model is None:
            return None
        try:
            result = model.revive(envelope["response"])
        except Exception:
            logger.warning("Cached %s envelope failed validation — treating as a miss", model.__name__, exc_info=True)
            return None
        result.replayed = True
        return result

    return deserialize


async def _run(impl: Callable[..., Any], /, **kwargs: Any) -> Any:
    """Call an implementation, awaiting it only if it is a coroutine function.

    Six of the fourteen are plain ``def``.
    """
    result = impl(**kwargs)
    return await result if inspect.isawaitable(result) else result


def _keyed_scope(req: BuyerRequest, identity: PublicIdentity) -> tuple[str, str, str | None, str] | None:
    """``(tenant_id, principal_id, account_id, idempotency_key)`` when this request is cacheable.

    None whenever any part is absent: a request whose schema declares no key, one carrying
    none, or an identity that resolved no tenant or principal, has no (agent, account, key)
    scope to be cached under. A DTO that declares no ``idempotency_key`` cannot carry one --
    every request model is the pinned schema and nothing else.
    """
    key = req.get_idempotency_key()
    if not key:
        return None
    scope = identity.replay_scope()
    if scope is None:
        return None
    tenant_id, principal_id, account_id = scope
    return tenant_id, principal_id, account_id, key


def failure_response(
    protocol: TransportProtocol,
    operation: str,
    exc: Exception,
    *,
    echo: ContextObject | None = None,
    identity: PublicIdentity | None = None,
) -> AdcpErrorResponse:
    """Record one failure and build the response that answers it. THE one failure builder.

    A transport calls it with the three positional arguments, for a fault in its own container
    handling raised outside ``serve``: that is protocol-level knowledge and nothing more. The
    ``echo`` and the ``identity`` are the boundary's alone -- only ``serve`` holds a validated
    request and a resolved caller -- so only ``_failed`` passes them.

    The ORIGINAL exception goes to the recorder: the body carries no exception text (AdCP 3.1.1
    transport-errors.mdx, Security Considerations), so the server-side record is the sole
    answer to what broke. ``adcp_error_for`` types it -- an untyped ValueError is a
    VALIDATION_ERROR, a PermissionError a PERMISSION_DENIED, anything else an INTERNAL_ERROR --
    and that answer does not depend on which transport is asking.

    NOTHING HERE IS GUARDED, and a ``try`` around the build would be the wrong answer to a
    real hazard. ``_failed`` calls this from inside the boundary's own ``except Exception``
    blocks, and Python raises an exception thrown in a handler to that handler's CALLER -- so
    a raise here is past the catch that would have wrapped it and leaves ``serve`` as
    something no transport's ``except AdcpFailure`` sees. That happened once, for a dict
    ``details`` reaching ``details.to_wire()``. The fix is upstream and is the type system:
    ``AdCPSalesAgentError`` is generic in its detail type and each subclass binds one, so a
    raise site naming its class cannot pass anything else. The single site that could was
    annotated ``type[AdCPSalesAgentError]`` with the parameter dropped, which erases
    ``DetailsT`` to ``Any``; it is parameterized now. Catching here instead would answer
    INTERNAL_ERROR while the record said BUDGET_EXCEEDED -- making the log-versus-wire
    disagreement that was the damage into permanent behavior.
    """
    record_boundary_error(protocol, operation, exc, identity=identity)
    return _served(echo, AdcpErrorResponse.of(adcp_error_for(exc)))


def _failed(
    protocol: TransportProtocol,
    tool_name: str,
    exc: Exception,
    echo: ContextObject | None,
    identity: PublicIdentity | None = None,
) -> NoReturn:
    """Leave the boundary with the failure response for ``exc``, scoped to the caller once resolved."""
    raise AdcpFailure(failure_response(protocol, tool_name, exc, echo=echo, identity=identity)) from exc


def validated_request(tool_name: str, raw: Any, protocol: TransportProtocol) -> BuyerRequest:
    """Parse a buyer's payload into its tool's DTO. A rejection carries the buyer's context out.

    THE one validation: every transport hands its payload here, so a malformed payload is
    answered identically over MCP, A2A and REST.

    The echo is read from the RAW payload, because this is the one outcome with no ``req`` to
    read ``req.context`` off. Read by subscript and caught: a payload that is not a JSON object
    has no ``context`` to echo, which is the same answer as an object that carries none, and
    the DTO refuses the non-object shape itself. Coerced to ``ContextObject`` -- lossless, the
    model declares no properties and allows extras -- so this path carries the same type
    ``req.context`` is.
    """
    from src.core.tools.registry import TOOLS

    try:
        return TOOLS[tool_name].dto.model_validate(raw)
    except Exception as exc:
        try:
            echo = ContextObject.model_validate(raw["context"])
        except (TypeError, KeyError, IndexError, ValidationError):
            echo = None
        _failed(protocol, tool_name, exc, echo)


async def serve(
    tool_name: str,
    raw: Any,
    headers: Mapping[str, str],
    protocol: TransportProtocol,
    exchange: HttpExchange | None = None,
) -> AdcpResponse:
    """Answer one buyer payload. THE transport entry.

    Parses the payload and runs the tool. Either step can fail, and both failures leave the
    same way -- as ``AdcpFailure`` carrying the response that says so -- so a transport wraps
    ONE call in ONE ``try``. ``invoke_tool`` is the entry for a caller that already holds a
    validated request.

    ``exchange`` is the HTTP message as it arrived, recorded by ``SignedExchangeCapture``
    where its raw bytes and raw path still existed, and handed over exactly as ``headers``
    are: sourced from the transport, never re-derived here. A transport that is not serving
    an HTTP request passes none, which is a request presenting no signature.
    """
    return await invoke_tool(tool_name, validated_request(tool_name, raw, protocol), headers, protocol, exchange)


async def invoke_tool(
    tool_name: str,
    req: BuyerRequest,
    headers: Mapping[str, str],
    protocol: TransportProtocol,
    exchange: HttpExchange | None = None,
) -> AdcpResponse:
    """Run the registry's tool named ``tool_name``, for the caller the request *headers* present.

    The form every transport calls. A transport names the TOOL and hands over the request it
    validated plus the headers the request arrived with; which function runs, and whether the
    credential in those headers must verify, are the registry's answers -- not the caller's.

    IT TAKES HEADERS, NOT AN IDENTITY. Nothing here reads them: the resolver is their one
    reader, and it is handed the registry row's declaration of whether the credential must
    verify, so no transport decides it. ``protocol`` labels the observability record a
    failure writes and decides nothing; the identity does not carry it.

    The same is true of ``exchange``, the captured HTTP message an RFC 9421 signature covers.
    Nothing here reads it either. What this function DOES add is the two things only it knows
    and the capture cannot: the registry key this request dispatched on, and whether the
    VALIDATED request registers webhook credentials (security.mdx @ v3.1.1 :1462-1465). Both
    go into one :class:`~src.core.signing.verifier.SignatureSubject` beside the headers, and
    the resolver -- the one reader of a credential, signature included -- decides.
    """
    from src.core.resolved_identity import _resolve_identity
    from src.core.tools.registry import TOOLS

    spec = TOOLS[tool_name]

    # CAPTURED at entry and stamped on the way out, and that is the whole mechanism. The
    # buyer's ``context`` is opaque data this seller carries and returns: validation does not
    # touch it (``ContextObject`` declares no properties and allows extras, and
    # ``deep_strip_to_schema`` passes a free-form container through whole), so ``req.context``
    # IS what arrived. Nothing between here and the stamp may read it, pass it, or set it.
    echo = req.get_context()

    # The INBOUND half of version negotiation, FIRST and before anything reads the database.
    # A version pin is a property of the request in exactly the sense the account and the
    # idempotency key are, so it belongs at the one chokepoint rather than inside a tool;
    # AdCP 3.1.1 ``compliance/universal/error-compliance.yaml`` grades it on ``get_products``.
    # A release this build does not speak ends the exchange, so a rejected pin must not
    # resolve a credential, a tenant or an account, must not hash the request, and must not be
    # answered from the replay cache. The pins are declared fields on the request (the SDK's
    # ``AdcpVersionEnvelope``), so the validated object already carries them and nothing is
    # parsed a second time.
    #
    # This check used to sit next to the OUTBOUND stamp, which reads well -- both concern the
    # version -- and orders wrong: the stamp can only run last, so pairing them put the check
    # after identity resolution, where 18d73f887 later moved the account load. A request that
    # named an account was then answered on auth before its unsupported pin was ever read.
    # ``_failed`` without an identity, the form ``validated_request`` uses: none is resolved yet.
    try:
        negotiate_adcp_version(req.get_adcp_version(), req.get_adcp_major_version())
    except Exception as exc:
        _failed(protocol, tool_name, exc, echo)

    # In a worker thread because ``_resolve_identity`` is SYNC and hits the database
    # (tenant detection, the principal lookup, and the account lookup when the request names
    # one); psycopg2 has no async path. Its own block, so ``identity`` is bound wherever it is
    # read below.
    #
    # Naming an account is itself a claim that needs a credential: a request that carries
    # ``account`` requires a valid token even on a public tool, so the resolver refuses an
    # anonymous caller (AUTH_MISSING / AUTH_INVALID) before the access-scoped account lookup
    # could run for it (#1417). The account is resolved iff the DTO declares the field and the
    # request carries it, and the resolver builds the identity once with it inside. The flag
    # is computed here, from the row, and the resolver's overloads type the result; the
    # tenant-dependent half of the row's policy (``requires_credential(tenant)``) is handed
    # over for the resolver to ask once it holds the tenant.
    account_ref = req.get_account()

    # The escalation's input, and the seller's LOG duty on the same fact. security.mdx @
    # v3.1.1 :1464 is per REQUEST and unqualified by posture, so it is discharged HERE --
    # the one place a request has arrived and been read -- rather than inside the verifier,
    # which runs only after a bearer resolved and only while the kill switch is on. See
    # ``log_arriving_webhook_credentials``.
    registers_credentials = registers_webhook_credentials(req)
    if registers_credentials:
        log_arriving_webhook_credentials(tool_name)

    try:
        identity = await asyncio.to_thread(
            _resolve_identity,
            headers,
            require_valid_token=spec.requires_credential() or account_ref is not None,
            account_ref=account_ref,
            credential_required_for=spec.requires_credential,
            signature_subject=SignatureSubject(
                operation=tool_name,
                registers_credentials=registers_credentials,
                exchange=exchange,
            ),
        )
    except Exception as exc:
        _failed(protocol, tool_name, exc, echo)

    # EVERY exception, including the catch-all to INTERNAL_ERROR. No transport error can be
    # here: an implementation raises AdCPSalesAgentError and nothing else (ruff-boundary.toml
    # bans importing ToolError at all), and a2a's A2AError is raised by the A2A handler BEFORE
    # dispatch. Recorded HERE because this is the only place holding all three things a record
    # needs: the tool name, the resolved identity, and the exception.
    #
    # An implementation is called with the request and the caller, and nothing else. There is
    # no per-transport channel here, so no transport can hand an implementation a value the
    # others cannot.
    try:
        return _served(echo, await _invoke(tool_name, spec.impl, req, identity))
    except Exception as exc:
        _failed(protocol, tool_name, exc, echo, identity)


def _served[Served: AdcpResponse](echo: ContextObject | None, response: Served) -> Served:
    """Stamp what the BOUNDARY owns onto one response envelope: the release, and the echo.

    THE one assignment for both, on every outcome: a fresh run, a replayed one and a failure
    all pass through it, so a replay echoes the release that is serving it and the context of
    the caller being served, rather than the context of whichever request filled the cache.

    The release is an envelope field like any other -- every response model declares
    ``adcp_version``, inherited from the SDK's ``AdcpVersionEnvelope`` -- and it is set HERE
    for the same reason the account and the idempotency key are read at the boundary: it is a
    property of the seller and the call, not of the work, so an implementation neither knows
    it nor should have to. AdCP 3.1.1 ``compliance/universal/version-negotiation.yaml`` grades
    it at the envelope root with ``envelope_field_present`` and ``envelope_field_pattern``
    (advisory at 3.1, MUST at 4.0). The INBOUND half of that negotiation is a precondition
    rather than an envelope field, and runs at the top of ``invoke_tool``.
    """
    response.adcp_version = SERVED_ADCP_VERSION
    # ``object.__setattr__``, not assignment: ``AdcpResponse.__setattr__`` refuses ``context``
    # so that no code outside this function can write it. This is the one bypass, and it is
    # the reason the refusal can be unconditional everywhere else.
    object.__setattr__(response, "context", echo)
    return response


async def _invoke(
    tool_name: str,
    impl: Callable[..., Any],
    req: BuyerRequest,
    identity: PublicIdentity,
) -> AdcpResponse:
    """``invoke`` without the envelope stamp: honour the key, run it.

    The account the request named is already ON the identity: the resolver built it with
    the account inside, before this ran, so the replay scope below reads it off the type.
    """
    scope = _keyed_scope(req, identity)
    if scope is None:
        return await _run(impl, req=req, identity=identity)

    tenant_id, principal_id, account_id, key = scope
    request_hash = canonical_request_hash(req)

    replay = lookup_cached_replay(
        tenant_id=tenant_id,
        principal_id=principal_id,
        account_id=account_id,
        idempotency_key=key,
        request_hash=request_hash,
        deserialize=_deserializer_for(impl),
    )
    if replay is not None:
        return replay

    result = await _run(impl, req=req, identity=identity)
    cache_success(
        tenant_id=tenant_id,
        principal_id=principal_id,
        account_id=account_id,
        tool_name=tool_name,
        idempotency_key=key,
        response_model=result,
        # The result's OWN protocol status, not a constant. A create awaiting human approval
        # is ``submitted``, and storing it as completed would make the replay reconstruct the
        # wrong response variant -- the buyer would see a success where the original answer
        # was a pending task. A response with no status is not a task envelope; it succeeded
        # by having returned at all.
        protocol_status=result.status or "completed",
        payload_hash=request_hash,
    )
    maybe_evict_expired(tenant_id)
    return result
