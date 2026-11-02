"""Unified MCP client utility for consistent agent communication.

This module provides a single, standardized way to call MCP tools on
external agents (creative agents, signals agents, etc.).

**This seam CAN sign, per attempt, and only when a caller asks it to.**

The requirement that produced ``sign=`` is not "MCP calls should be signed": it is
that a signed agent call must not be the reason a second, un-pinned HTTP client
exists. Before this parameter the only way to attach an RFC 9421 request signature
to an outbound agent call was to build an ``adcp`` SDK client, which dials for
itself — so "sign the call" and "keep the call inside the SSRF-guarded seam" were
mutually exclusive, and one of the two always lost. ``sign=`` removes the choice:
the signature is computed INSIDE this seam, over the request httpx is about to put
on the wire, and the caller never receives a client it could point somewhere else.
It is the same injection shape :mod:`src.core.security.webhook_egress` uses on
``send``/``asend``'s ``sign=`` hook, applied to the one transport those two cannot
carry.

Why the parameter is OPT-IN and defaults to ``None`` — this is the surviving half of
the argument this paragraph used to make for signing nothing at all. An RFC 9421
signature attributes to a NAMED operation, and AdCP 3.1.1
(building/by-layer/L1/security.mdx:1043) is explicit about which names are legal:

    "Operation names in `required_for` / `supported_for` are AdCP protocol
     operation names (`create_media_buy`, `update_media_buy`, `acquire_rights`,
     etc.) — not MCP tool names, A2A skill names, or any transport-specific
     rename. Verifiers MUST NOT accept operation names that are not defined by
     the AdCP protocol spec."

The bespoke creative-agent tools that also come through here (``preview_creative``,
``build_creative``) are not AdCP protocol operations and have no entry in the
``request_signing.supported_for`` enum. So WHETHER a given dial may legally carry a
signature stays the caller's decision, made where the operation name is known; this
seam owns only the mechanics of applying one correctly. Signing every dial from in
here unconditionally would put signatures attributed to names a conformant verifier
MUST reject on the wire (salesagent-z6nr.34, #1291).

There is no silent downgrade in the other direction either. A caller that passes
``sign=`` and cannot get a signature — an unreadable (streamed) body, a signer that
raises, a signer that returns nothing to apply — gets :class:`MCPSigningError`,
raised from inside the httpx request hook BEFORE the transport is handed the
request, recovered from fastmcp's own exception wrapping and re-raised UNRETRIED.
Nothing is dialled in the clear.

Key features:
- Consistent URL handling (uses user's URL; if it fails after retries, does one
  final fallback attempt by appending "/mcp" when missing)
- Standardized auth header building
- Built-in retry logic with exponential backoff, driven by the shared
  egress Attempts machine — connect AND tool-call failures share ONE
  attempt sequence, so a transient tool-level failure is genuinely retried,
  not just classified after the fact
- Proper error handling and logging
- Testable in isolation

Usage:
    from src.core.utils.mcp_client import call_mcp_tool

    result = await call_mcp_tool(
        agent_url="https://example.com/mcp",
        tool="tool_name",
        arguments=params,
        auth={"type": "bearer", "credentials": "token123"},
        timeout=30,
    )
    payload = result.structured_content
"""

import logging
from collections.abc import Mapping
from typing import Any, NoReturn, Protocol

# All three bound PRIVATELY: a plain import would publish `mcp_client.Client`,
# `mcp_client.StreamableHttpTransport` and `mcp_client.httpx`, and importing any of
# them from here resolves to the real symbol past the gate (GH #1802). The
# underscore makes that an ImportError, so the seam cannot re-export what it was
# sanctioned to import.
import httpx as _httpx
from fastmcp.client import (
    Client as _Client,
)
from fastmcp.client.client import CallToolResult
from fastmcp.client.transports import (
    StreamableHttpTransport as _StreamableHttpTransport,
)

from src.core.errors.details import AdapterFailureDetails, ConfigurationDetails
from src.core.exceptions import AdCPAdapterError, AdCPConfigurationError
from src.core.security.egress.attempts import Attempts
from src.core.security.outbound_http import (
    _SIGNER_RESERVED_HEADERS,
    McpClientFactory,
    OutboundError,
    find_wrapped,
    guarded_client_factory,
    sleep_backoff,
    validate_url,
)

logger = logging.getLogger(__name__)


class MCPConnectionError(AdCPAdapterError):
    """Could not reach an MCP agent after every retry.

    In the AdCP hierarchy because we define it and we raise it. SERVICE_UNAVAILABLE
    is inherited from AdCPAdapterError and is honest here: the upstream did not
    answer, and a retry may well succeed.

    The agent URL and the last underlying error travel in ``details`` and
    ``internal_detail`` -- the latter is server-log only, which is what keeps an
    upstream exception's text off the buyer's wire.
    """


class MCPCompatibilityError(AdCPAdapterError):
    """An MCP agent rejected a notification its own protocol version requires.

    Same tree and same code as its sibling: the upstream was reached and answered
    in a way its protocol does not permit, which is a gateway-level fault rather
    than anything the buyer sent.
    """


class MCPSigningError(AdCPConfigurationError):
    """Raised when a caller asked for a signed dial and the request could not be signed.

    The whole point of the type. Everything else this seam raises is a delivery
    problem, which the retry loop is right to work on; this one means the request
    that was about to leave would have left UNSIGNED, and a caller that passed
    ``sign=`` has already said that is not an acceptable outcome. It is raised inside
    the httpx request hook — before ``_send_single_request``, so no socket is written
    — and re-raised out of the attempt loop unretried, because retrying an
    unsignable request three times only produces three chances to send it in the
    clear and one misleading ``MCPConnectionError`` at the end.

    In the AdCP hierarchy, and under CONFIGURATION_ERROR rather than its siblings'
    SERVICE_UNAVAILABLE, because nothing was dialled: the signing key material is
    THIS deployment's operator configuration, so a buyer has no lever and the
    ``terminal`` recovery ``AdCPConfigurationError`` carries is the honest one.
    Choosing the class IS how the raise site says terminal (ADR-010); there is no
    ``message=`` here, and no diagnostic reaches the buyer's wire — see
    :func:`_refuse_unsignable`.
    """


def _refuse_unsignable(
    diagnostic: str,
    *,
    request: _httpx.Request,
    cause: BaseException | None = None,
) -> NoReturn:
    """Log the operator-facing diagnostic, then raise the wire-safe typed refusal.

    The same two-audience split, for the same reason, as
    :func:`src.core.signing.provider._refuse`. ``AdCPSalesAgentError`` takes no
    ``message`` (ADR-010): buyer-facing text is ``CODE_TABLE``'s, and the
    diagnostics here name exactly what ``transport-errors.mdx`` § Security
    Considerations forbids on the wire — the counterparty's hostname, the failing
    signer's own exception text. Those are what an operator needs and what a buyer
    must not receive, so they go to the server log and to ``internal_detail`` (an
    exception, never serialized), while the typed ``details`` carries only the short
    safe pair: which capability was being exercised and which configured agent the
    dial was for.

    ONE helper rather than a log-then-raise pair at each of the three failure modes:
    the split has to be made the same way every time, and a second spelling of it is
    how one site ends up interpolating the signer's text into the buyer's envelope.
    """
    logger.error("refusing to dial unsigned: %s", diagnostic)
    raise MCPSigningError(
        details=ConfigurationDetails(agent_url=str(request.url), capability="request_signing"),
        internal_detail=cause,
    ) from cause


class SignMcpAttempt(Protocol):
    """Signs ONE outgoing HTTP request of an MCP session: ``(method, url, headers, body) -> headers to merge``.

    The MCP twin of :class:`src.core.security.outbound_http.SignAttempt`, and it
    differs from it in exactly one parameter, deliberately.

    ``send``/``asend`` sign a payload the CALLER serialized, so their hook mirrors
    ``adcp.webhook_auth.WebhookAuthStrategy.build_auth_headers`` and the
    ``Content-Type`` obligation lands on that caller (see
    :mod:`src.core.security.outbound_http`'s module docstring). Here there is no such
    caller: a signed MCP session is a POST per JSON-RPC message plus, at the
    transport's discretion, a GET for the event stream and a DELETE to terminate it,
    all framed by the MCP SDK and none of them visible to whoever called
    :func:`call_mcp_tool`. ``adcp.signing``'s verifier requires a ``content-type``
    that is PRESENT on the request to be COVERED by the signature, so a signer given a
    hard-coded content type would sign a header that did not ship on some of those
    messages and produce a base no verifier can rebuild. Passing the request's real
    headers is the only spelling correct for every message of a session.

    KEYWORD-ONLY and named after ``adcp.signing.sign_request``'s own first four
    parameters, for the same reason ``SignAttempt`` is named after the SDK's webhook
    strategy: so a signing caller passes a bound method with no adapter in between.
    :meth:`src.core.signing.request_signer.RequestSignerStrategy.build_signed_headers`
    is that method.
    """

    def __call__(self, *, method: str, url: str, headers: Mapping[str, str], body: bytes) -> Mapping[str, str]: ...


def _install_signing_hook(client: _httpx.AsyncClient, sign: SignMcpAttempt) -> None:
    """Make *client* sign every request it is about to send, per request.

    An httpx request event hook is the injection point because it is the LAST place
    the request object exists before ``_send_single_request`` hands it to the
    transport (httpx 0.28.1, ``_send_handling_redirects``): ``request.content`` there
    is the exact byte string that will be written to the socket and ``request.url``
    is post-parameters, so the signed bytes and the wire bytes are one object rather
    than two that agree. It is also per-REQUEST by construction — every JSON-RPC
    message of the session, and every attempt of this module's own retry loop, gets a
    fresh ``created``/``nonce`` — which is what RFC 9421 requires of a signature a
    conformant receiver must reject on replay.

    Installing on the client rather than signing in :func:`call_mcp_tool` is what
    keeps the MCP SDK's framing decisions out of this module: we never rebuild,
    re-serialize or re-frame the request, we only add headers to it.

    Everything this hook can go wrong on raises :class:`MCPSigningError` rather than
    proceeding, because every one of those failures has the same meaning — the
    request would leave unsigned.
    """

    async def _sign_hook(request: _httpx.Request) -> None:
        try:
            body = request.content
        except _httpx.RequestNotRead as exc:
            # A streaming request body cannot be signed without buffering it, and
            # buffering it here would re-frame a request the transport already
            # framed. Refuse instead: today's MCP transport always sends a
            # materialized body, so this is a "the transport changed under us"
            # alarm, not a routine path.
            _refuse_unsignable(
                f"cannot sign a streamed {request.method} to {request.url}: its body is not readable as bytes, "
                "so the signature could not cover the bytes actually transmitted",
                request=request,
                cause=exc,
            )

        try:
            signed = sign(method=request.method, url=str(request.url), headers=dict(request.headers), body=body)
        except Exception as exc:
            _refuse_unsignable(
                f"the signer refused or failed to sign a {request.method} to {request.url}: "
                f"{type(exc).__name__}: {exc}",
                request=request,
                cause=exc,
            )

        applied = 0
        for name, value in signed.items():
            if name.lower() in _SIGNER_RESERVED_HEADERS:
                # Same reserved set, and the same reason, as the egress seam's own
                # signer merge — imported rather than restated so the two cannot
                # drift: a signer that re-frames the body can desync the bytes it
                # just signed from the bytes the receiver reads.
                logger.warning("Signer returned reserved header %r; dropped (body framing is the transport's)", name)
                continue
            # pop-then-set, matching adcp.signing's own httpx hook: our value is
            # authoritative even if an earlier layer set the same header in a
            # different case.
            request.headers.pop(name, None)
            request.headers[name] = value
            applied += 1

        if applied == 0:
            # A signer that returns nothing applicable is the silent-downgrade shape
            # in its purest form: the dial would proceed, unsigned, and look
            # identical to a successful signed one. The SDK's own event hook skips
            # quietly here (it is negotiating an OPTIONAL capability); this seam
            # cannot, because reaching it means a caller explicitly asked to sign.
            _refuse_unsignable(
                f"the signer produced no headers to apply to a {request.method} to {request.url}; "
                "refusing to dial unsigned after signing was requested",
                request=request,
            )

    hooks = dict(client.event_hooks)
    hooks["request"] = [*(hooks.get("request") or []), _sign_hook]
    client.event_hooks = hooks


def _mcp_client_factory(url: str, sign: SignMcpAttempt | None) -> McpClientFactory:
    """The pinned client factory for *url*, teaching it to sign when asked.

    Wraps :func:`~src.core.security.outbound_http.guarded_client_factory` rather than
    replacing it, so every SSRF guarantee is untouched and unrestated: the resolve-once
    IP pin, ``follow_redirects=False``, ``trust_env=False``, the port and scheme policy
    and the refusal are all still decided in the egress seam, on the same ``url`` the
    transport is constructed to dial. Signing is strictly additive to the client that
    function already built — this adds a header hook and nothing else. Returned
    unwrapped when there is nothing to sign, so an unsigned dial is byte-for-byte the
    call it was before this parameter existed.
    """
    guarded = guarded_client_factory(url)
    if sign is None:
        return guarded

    def factory(
        headers: Mapping[str, str] | None = None,
        timeout: _httpx.Timeout | float | None = None,
        auth: _httpx.Auth | None = None,
        follow_redirects: bool | None = None,
    ) -> _httpx.AsyncClient:
        client = guarded(headers, timeout, auth, follow_redirects)
        _install_signing_hook(client, sign)
        return client

    return factory


def _build_auth_headers(auth: dict[str, Any] | None, auth_header: str | None = None) -> dict[str, str]:
    """Build authentication headers from auth config.

    Args:
        auth: Auth configuration dict with 'type' and 'credentials' keys
        auth_header: Optional custom header name (defaults based on auth type)

    Returns:
        Dictionary of headers to include in request

    Examples:
        >>> _build_auth_headers({"type": "bearer", "credentials": "token123"})
        {"Authorization": "Bearer token123"}

        >>> _build_auth_headers({"type": "api_key", "credentials": "key123"})
        {"x-api-key": "key123"}

        >>> _build_auth_headers({"type": "bearer", "credentials": "token"}, "X-Custom-Auth")
        {"X-Custom-Auth": "Bearer token"}
    """
    headers: dict[str, str] = {}

    if not auth:
        return headers

    auth_type = auth.get("type")
    credentials = auth.get("credentials")

    if not auth_type or not credentials:
        return headers

    # Determine header name
    if auth_header:
        header_name = auth_header
    elif auth_type == "bearer":
        header_name = "Authorization"
    elif auth_type == "api_key":
        header_name = "x-api-key"
    else:
        # Generic auth type - use x-api-key as default
        header_name = "x-api-key"

    # Format header value
    if auth_type == "bearer":
        headers[header_name] = f"Bearer {credentials}"
    else:
        # For api_key and other types, use credentials as-is
        headers[header_name] = credentials

    return headers


async def call_mcp_tool(
    agent_url: str,
    tool: str,
    arguments: dict[str, Any],
    *,
    auth: dict[str, Any] | None = None,
    auth_header: str | None = None,
    timeout: int = 30,
    max_attempts: int = 3,
    sign: SignMcpAttempt | None = None,
) -> CallToolResult:
    """Call an MCP tool with standardized connection handling and retry.

    This is the ONLY place where MCP clients should be created. This ensures
    consistent URL handling, auth, retry logic, and error handling across
    all agent communications.

    The connect AND the tool call live inside the SAME per-attempt ``try`` and
    drive the SAME :class:`~src.core.security.egress.attempts.Attempts`
    sequence, so a tool-level failure is retried exactly like a connect
    failure — the tool is re-invoked on the next attempt, not just reported
    once a connection later happens to succeed. The predecessor
    (``create_mcp_client``, an ``@asynccontextmanager``) could not offer this:
    its ``yield`` sat inside the retry ``try``, but the caller's
    ``client.call_tool(...)`` ran in the CALLER's own ``async with`` body,
    outside that ``try`` — a tool failure was thrown back into the generator
    at the yield and contextlib refused to resume it.

    Args:
        agent_url: URL of the MCP agent endpoint
                  Examples: "https://creative.adcontextprotocol.org/mcp"
                           "https://audience-agent.fly.dev/FastMCP/"
                  NOTE: Use the exact URL the user provided - no modifications!
        tool: Name of the MCP tool to call
        arguments: Arguments to pass to the tool
        auth: Optional auth configuration dict
              Format: {"type": "bearer"|"api_key", "credentials": "token_value"}
        auth_header: Optional custom auth header name
                    (defaults: "Authorization" for bearer, "x-api-key" for api_key)
        timeout: Request timeout in seconds (default: 30)
        max_attempts: Maximum attempts against the primary URL (default: 3)
        sign: Optional :class:`SignMcpAttempt`. When given, EVERY HTTP request this
            dial makes — every JSON-RPC message of the session, on every attempt — is
            signed over the exact bytes, target URI and headers that go on the wire,
            with a fresh nonce each time (RFC 9421 requires it; see
            :func:`_install_signing_hook`). Pass
            ``RequestSignerStrategy(...).build_signed_headers``; the callback shape is
            the SDK's, so no adapter is needed. Omitted, the dial is unchanged in
            every respect. Signing does NOT open a second client: the signature is
            computed inside the seam's own pinned transport, so a signed dial keeps
            resolve-once-and-pin, the redirect refusal, the port policy and the
            no-env-trust posture unchanged.

    Returns:
        The tool call's result

    Raises:
        OutboundError: If egress policy refuses the destination — either at the
            pre-check below or during the dial itself. Propagates UNRETRIED and
            with its own classification; the attempt budget does not apply to a
            destination the policy already refused.
        MCPSigningError: If ``sign`` was given and a request could not be signed.
            Propagates UNRETRIED, and is raised before the request reaches the
            transport, so nothing was sent unsigned.
        MCPConnectionError: If connection or the tool call fails after all retries
        MCPCompatibilityError: If MCP SDK version incompatibility detected

    Example:
        result = await call_mcp_tool(
            agent_url="https://creative.adcontextprotocol.org/mcp",
            tool="list_creative_formats",
            arguments={},
            auth={"type": "bearer", "credentials": "token123"},
            timeout=30,
        )
        formats = result.structured_content
    """
    # Strip trailing slashes only - preserve the actual path (no mutation besides trimming)
    agent_url = agent_url.rstrip("/")

    # Egress policy, once, BEFORE the candidate loop and outside every try.
    #
    # Position is what makes the refusal CHEAP: refused here, nothing is built and
    # no candidate is tried. It is no longer what makes it CORRECT — the in-loop
    # handler below recovers a refusal raised during the dial itself and re-raises
    # it unretried, because this pre-check and the pinned transport's own
    # resolution are two separate resolutions that can disagree.
    #
    # Validating the primary also covers the ``/mcp`` fallback below: it differs
    # only by path, and the seam's policy is about scheme and address.
    validate_url(agent_url)

    # Build auth headers
    headers = _build_auth_headers(auth, auth_header)

    # Prepare connection candidates: primary URL first, then a single '/mcp' fallback (if missing)
    primary_url = agent_url
    fallback_url = None
    if not primary_url.endswith("/mcp"):
        fallback_url = f"{primary_url}/mcp"

    candidates: list[tuple[str, int]] = [(primary_url, max_attempts)]
    if fallback_url:
        # Per requirement: try once again with '/mcp' after primary retries fail
        candidates.append((fallback_url, 1))

    last_exception: BaseException | None = None

    for current_url, candidate_attempts in candidates:
        attempts = Attempts(candidate_attempts)

        for attempt in attempts.next_attempt():
            try:
                # Create transport and client. The httpx_client_factory pins the
                # connection to current_url's validated IP and refuses redirects:
                # without it fastmcp falls back to mcp.shared._httpx_utils's
                # create_mcp_http_client, which follows redirects with no pin, so a
                # counterparty answering `302 -> http://169.254.169.254/` reaches an
                # address validate_url's pre-check never saw. Pinning current_url — the URL
                # actually dialed — also means validate-and-dial cannot diverge.
                transport = _StreamableHttpTransport(
                    url=current_url,
                    headers=headers,
                    httpx_client_factory=_mcp_client_factory(current_url, sign),
                )
                client = _Client(transport=transport)

                # Connect and call the tool inside the SAME try — a tool-level
                # failure is caught by the except below and retried on this
                # attempt sequence, exactly like a connect failure.
                async with client:
                    result = await client.call_tool(tool, arguments)

                logger.debug(f"MCP tool {tool!r} on {current_url} succeeded on attempt {attempt}")
                return result

            except Exception as e:
                # A dial-time egress refusal is TERMINAL: the attempt budget does
                # not apply to a destination the policy already refused. Let it
                # out with its own type, before any retry bookkeeping.
                #
                # It has to be recovered from the chain rather than caught by
                # type, because fastmcp re-raises it as a bare RuntimeError
                # ("Client failed to connect: ..."). An `except OutboundError`
                # branch here reads like it closes the case and catches nothing --
                # the shape this epic exists to delete -- so the unwrap is the
                # one mechanism, not a decoration in front of one.
                #
                # validate_url above the loop does NOT make this unreachable: it
                # resolves separately from guarded_client_factory's resolution
                # inside `async with client`, and the two can disagree under DNS
                # rebind, which is precisely when retrying a refusal is worst.
                refusal = find_wrapped(e, OutboundError)
                if refusal is not None:
                    raise refusal from e

                # An unsignable request is TERMINAL for the same reason a refused
                # destination is: the attempt budget cannot fix it, and spending it
                # here would only produce three more chances to leave unsigned before
                # reporting the wrong failure. Recovered from the chain, not caught by
                # type, because fastmcp re-raises a hook failure as its own
                # RuntimeError -- an `except MCPSigningError` arm would read like it
                # closes the case and catch nothing.
                unsignable = find_wrapped(e, MCPSigningError)
                if unsignable is not None:
                    raise unsignable from e

                last_exception = e
                error_msg = str(e)

                # Check for known compatibility issues
                if "notifications/initialized" in error_msg:
                    logger.warning(
                        f"MCP SDK compatibility issue with {current_url}: "
                        f"Server doesn't support 'notifications/initialized' notification. "
                        f"This is a known issue between FastMCP SDK versions."
                    )
                    raise MCPCompatibilityError(
                        details=AdapterFailureDetails(url=current_url),
                        internal_detail=e,
                    ) from e

                attempts.record_transport_failure()

                # Log and retry for this candidate
                logger.warning(
                    f"MCP connection attempt {attempt}/{candidate_attempts} failed for {current_url}: {type(e).__name__}: {e}"
                )

                if attempt < candidate_attempts:
                    # Backoff for the primary candidate only (candidate_attempts > 1).
                    # This client owns its transport for protocol reasons — a
                    # stateful MCP session over StreamableHttpTransport, which the
                    # egress seam's one-shot asend cannot carry — so it defers to
                    # the seam's BR-RULE-029 schedule (via the shared Attempts
                    # instance) instead of recomputing one.
                    await sleep_backoff(attempts)
                else:
                    # Exhausted attempts for this candidate; move to next (if any)
                    logger.error(
                        f"All {candidate_attempts} connection attempt(s) failed for {current_url}. "
                        f"Last error: {type(e).__name__}: {e}"
                    )
                    break

    # If we reach here, all candidates failed, regardless of fallback
    raise MCPConnectionError(
        details=AdapterFailureDetails(url=agent_url, max_retries=max_attempts),
        internal_detail=last_exception,
    ) from last_exception
