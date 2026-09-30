"""The one place an operator-configured MCP tool is called.

Four registry methods used to spell the same ladder: dial through
:func:`~src.core.utils.mcp_client.call_mcp_tool`, extract the payload, and map
BOTH failure vocabularies onto AdCP errors -- each constructing the same
``OperatorEndpoint`` label twice, once per branch. Four copies is four chances to
forget an branch, and the copies had already drifted in what they passed
(``auth``/``auth_header`` on two of them, a literal ``30`` timeout on the other
two).

Lives in its own module rather than in ``mcp_client``: ``mcp_tool_payload``
already imports ``MCPCompatibilityError`` from ``mcp_client``, so siting this
function there and calling ``extract_tool_payload`` from it would close an
import cycle. This module imports both and nothing imports it back.

NOT for counterparty (buyer-supplied) URLs. Those dial through the egress seam's
``asend`` with a ``CounterpartyUrl`` provenance and have their own single-branch
mapping; :func:`raise_mapped_mcp_error` asserts operator provenance and would
fail on them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.core.exceptions import AdCPConfigurationError, AdCPSalesAgentError
from src.core.helpers.mcp_tool_payload import extract_tool_payload
from src.core.helpers.outbound_error_mapping import raise_mapped_mcp_error, raise_mapped_outbound_error
from src.core.security.outbound_http import OperatorEndpoint, OutboundError
from src.core.utils.mcp_client import (
    MCPCompatibilityError,
    MCPConnectionError,
    MCPSigningError,
    SignMcpAttempt,
    call_mcp_tool,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """The outcome of an operator's "test connection" probe, for either registry.

    Replaces a ``dict[str, Any]`` whose keys each admin route read with
    ``.get(key, default)``. Two states were expressible through that dict and
    are not expressible here: a SUCCESS carrying no count (the route defaulted
    it to ``0``, indistinguishable from an agent that genuinely answered with
    zero), and a FAILURE whose sentence arrived under a different key than the
    one the route read -- the failure paths returned ``error`` while the success
    path returned ``message``, so an explanation reached the operator only
    because each route happened to read both.

    ``message`` is always present and always the operator-facing sentence,
    whichever way the probe went. ``count`` is ``None`` when the probe failed --
    an absence, not a zero. ``samples`` is empty unless the probe has examples
    to show.

    One type for both probes: each blueprint projects it onto the JSON key names
    its own template already contracts on (``signal_count`` / ``format_count``),
    so the wire shape is unchanged while the thing being projected is typed.
    """

    ok: bool
    message: str
    count: int | None = None
    samples: tuple[str, ...] = field(default_factory=tuple)


def _operator_cause(exc: Exception) -> str:
    """The most specific operator-readable cause *exc* carries.

    ONE extraction for every one of :func:`probe_failure`'s branches. They ask the
    same question -- "how do I get the operator-facing cause out of this exception?"
    -- and answering it once per branch is how the configuration branch came to be
    fixed while the unreachable branch stayed mute, which is a worse state than
    either consistent one because it reads as deliberate. The signing branch is
    the third asker, and it gets the same answer for free.

    ``internal_detail`` first. Post-ADR-010 an ``AdCPSalesAgentError``'s
    ``message`` is a read-only property over ``CODE_TABLE`` -- a function of the
    CODE, not of the raise site -- because it is the text that reaches a BUYER
    over the wire, where AdCP 3.1.1 ``transport-errors.mdx`` § Security
    Considerations forbids deployment specifics. So ``message`` reads
    "Configuration error" for a handshake refusal, an egress refusal and an
    unparseable answer alike, and "Service temporarily unavailable" for an
    unreachable endpoint, a rate-limited one and an undelivered request alike.
    The raise sites' own diagnostics moved to ``internal_detail`` for that
    reason, and this surface -- the admin "test connection" dialog, read by the
    tenant operator who configured the agent -- is exactly where they belong.

    ``str(exc)`` second. For an ``AdCPSalesAgentError`` that is its ``message``
    (``__str__`` returns the property), so a typed error carrying no detail still
    renders its table sentence. For an UNTYPED exception -- one the seam did not
    classify, which reaches this module's caller because an operator probe
    reports every failure rather than 500ing -- it is the exception's own text,
    which may be third-party. That is deliberate and unchanged: the reader is an
    authenticated tenant operator on their own deployment's admin route, not a
    buyer whose error responses flow through LLM context, and the unanticipated
    failure is the one where a raw diagnostic is worth the most. Nothing else
    reads this value (see :class:`ProbeResult`).

    The type name last, so a cause-less exception (``asyncio.TimeoutError()``
    stringifies to ``""``) renders a sentence rather than a blank one.
    """
    detail = exc.internal_detail if isinstance(exc, AdCPSalesAgentError) else None
    if detail is not None and (text := str(detail).strip()):
        return text
    return str(exc).strip() or type(exc).__name__


def probe_failure(exc: Exception, *, logger: logging.Logger) -> ProbeResult:
    """The operator-facing sentence for a probe that did not connect.

    Both registries' probes report failure the same way, and they must: the
    operator is reading one dialog with one set of levers to check, and a
    difference in wording between the creative button and the signals button
    would be a difference with no cause behind it. One home for the sentence is
    what keeps them from drifting apart the way the env readers and the refusal
    table already did elsewhere in this seam.

    ``AdCPConfigurationError`` covers everything the operator can fix by
    repointing or re-crediting the deployment: the guarded MCP seam rejecting us
    during the handshake, egress policy refusing the configured endpoint before
    the dial, and an endpoint answering with nothing parseable. The seam does
    not distinguish "bad auth" from "bad request", so the advice names every
    lever rather than presuming credentials -- an egress refusal has nothing to
    do with them, and :func:`_operator_cause` says which one it was.

    A SIGNING failure is an ``AdCPConfigurationError`` too -- same wire code,
    same terminal recovery -- but NONE of those levers is its cause, so it gets
    its own sentence rather than the general one. It is told apart by its
    ``__cause__``, which :func:`call_operator_mcp_tool` chains from the one
    ``MCPSigningError`` branch a few lines below; the two functions are in one
    module precisely so that chaining discipline is checkable at a glance. The
    levers it names are the ones the posture gate actually reads
    (``request_signer_for_tenant`` in ``src/core/helpers/adapter_helpers.py``,
    the one gate both registries call): an ACTIVE request-signing key whose
    private half this deployment can decrypt. The gate's other half --
    ``origin_is_publishable`` -- is deliberately NOT named: a non-publishable
    origin resolves to ``sign=None`` and dials unsigned, so it cannot be the
    cause of a request that failed WHILE signing.

    ALL THREE branches read the cause the same way, through that one helper.
    Only the ADVICE differs, and only because the two configuration branches are
    the ones where the operator's levers are known to be the subject: every other
    failure -- an unreachable endpoint, a rate-limited one, an unclassified
    exception the seam did not wrap -- gets the cause with no advice attached,
    because none of the levers is known to be the cause. The last branch used to
    interpolate ``str(exc)``, which was the authored sentence when it was written
    and became the generic table text under ADR-010; it went mute for exactly the
    same reason the configuration branch did, and is fixed the same way rather
    than half-fixed.
    """
    cause = _operator_cause(exc)
    if isinstance(exc, MCPSigningError):
        logger.error("Connection test failed (signing): %s", cause)
        return ProbeResult(
            ok=False,
            message=(
                f"Connection failed: {cause.rstrip('.')}. This agent's tenant is configured to "
                f"sign its outbound calls, so check that its request-signing key is still ACTIVE and "
                f"that this deployment can decrypt the key's private half. The agent URL, its "
                f"credentials and the egress policy are not implicated -- nothing was sent."
            ),
        )
    if isinstance(exc, AdCPConfigurationError):
        logger.error("Connection test failed (configuration): %s", cause)
        return ProbeResult(
            ok=False,
            message=(
                f"Connection failed: {cause.rstrip('.')}. Check the agent URL, its credentials "
                f"and auth header, and whether this deployment's egress policy allows the address."
            ),
        )
    logger.error("Connection test failed: %s", exc, exc_info=True)
    return ProbeResult(ok=False, message=f"Connection failed: {cause}")


async def call_operator_mcp_tool(
    agent_url: str,
    tool: str,
    arguments: dict[str, Any],
    *,
    label: str,
    auth: dict[str, Any] | None = None,
    auth_header: str | None = None,
    timeout: int = 30,
    sign: SignMcpAttempt | None = None,
) -> dict[str, Any]:
    """Call *tool* on an operator-configured MCP agent and return its payload.

    Owns the dial, the payload extraction and BOTH error mappings, so a caller
    has one call to make and no branches to remember. ``label`` is the operator-facing
    name that rides out in a refusal message; it is built into an
    ``OperatorEndpoint`` ONCE here rather than once per except branch.

    ``agent_url`` is passed EXACTLY as the caller supplies it. The creative
    registry resolves a connection alias before calling; the signals registry
    does not. Resolving it here would silently give the signals path aliasing it
    does not have today.

    ``sign`` is forwarded VERBATIM, ``None`` included. WHETHER a dial may carry
    an RFC 9421 signature is the caller's decision -- it depends on the tenant's
    signing posture and on the operation name a conformant verifier will check
    the signature against -- so this function neither resolves a signer nor
    supplies a default. It is a passthrough with an error branch, not a policy.
    """
    provenance = OperatorEndpoint(label)
    try:
        result = await call_mcp_tool(
            agent_url=agent_url,
            tool=tool,
            arguments=arguments,
            auth=auth,
            auth_header=auth_header,
            timeout=timeout,
            sign=sign,
        )
        return extract_tool_payload(result)
    except OutboundError as exc:
        raise_mapped_outbound_error(exc, provenance=provenance, logger=logger)
    except MCPSigningError as exc:
        # A THIRD branch, not a fold into the one below, because a signing failure is
        # not a delivery failure: the seam raised inside its own httpx request hook,
        # before any socket was written, precisely so the request would NOT go out in
        # the clear. Routing it through ``raise_mapped_mcp_error`` would classify it
        # by an HTTP status it does not have and report "unreachable" for an endpoint
        # that was never dialled -- telling the operator to check the URL, the
        # credentials and the egress policy while the actual cause sat in their key
        # material. Neither neighbour's ``except`` names ``MCPSigningError``, so
        # without this branch it escapes both of them and surfaces raw.
        #
        # CONFIGURATION_ERROR / terminal is the right class for the same reason a
        # refused operator endpoint is: the address AND the key are this
        # deployment's, the buyer has no lever, and per the pinned enum they "MUST
        # NOT auto-retry" -- which also matches the seam, whose retry loop
        # deliberately does not retry an unsignable request.
        #
        # RE-RAISED, not re-wrapped. ``MCPSigningError`` already IS an
        # ``AdCPConfigurationError`` (src/core/utils/mcp_client.py), so wrapping it in a
        # fresh one bought nothing and cost the two things the operator actually reads:
        # the typed ``details`` the signer attached, and the type itself. The wrap also
        # forced ``probe_failure`` to discriminate on ``exc.__cause__``, which is a
        # weaker test than the type it was reconstructing -- any AdCPConfigurationError
        # that happened to be raised ``from`` a signing error would have matched it.
        #
        # CONFIGURATION_ERROR / terminal is already the right class, for the same reason
        # a refused operator endpoint is: the address AND the key are this deployment's,
        # the buyer has no lever, and per the pinned enum they "MUST NOT auto-retry" --
        # which also matches the seam, whose retry loop deliberately does not retry an
        # unsignable request. The buyer-facing sentence is CODE_TABLE's property; the
        # signer's own diagnostic rides ``internal_detail`` (server-log only, and what
        # :func:`_operator_cause` shows the tenant operator); the role name goes to the
        # log line here, where the original exception is.
        logger.error("Could not sign the dial to %s, so nothing was sent: %s", provenance.name, exc)
        raise
    except (MCPConnectionError, MCPCompatibilityError) as exc:
        raise_mapped_mcp_error(exc, provenance=provenance, logger=logger)
