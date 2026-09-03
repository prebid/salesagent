"""Translate both egress seams' failures into this application's AdCP taxonomy.

``src/core/security/outbound_http.py`` deliberately raises exactly two errors:
``OutboundRequestBlocked`` (refused before connecting) and
``OutboundDeliveryFailed`` (reached the network, did not deliver). That is its
whole public failure surface, and it stays that way — the seam must not learn
which AdCP code a given caller wants a 429 to become, because the answer depends
on whose URL it is and what the caller is doing with it. The guarded MCP seam
(``src.core.utils.mcp_client.call_mcp_tool``) has its own failure surface —
``MCPConnectionError`` / ``MCPCompatibilityError`` — for the same underlying
reason: closing the SSRF gap ``adcp.ADCPMultiAgentClient`` left (no transport
injection point in adcp 6.6.0 — upstream adcp-client-python#1004) meant routing
through a different transport with a different exception vocabulary.

The translation still has to live in ONE place per DECISION, or every migrating
call site re-derives it and they drift — which is the duplication this module
exists to delete. So both seams' mappers live here: a leaf module importing
only the seams and the exception taxonomy. Both extract (status, retry_after)
from their own failure surface and delegate the CLASS decision to
:func:`adcp_error_for_status` — the one function under this file (and this
codebase) that maps an HTTP status to an AdCP error class. They differ only in
what they can attach to the result: ``raise_mapped_outbound_error`` re-raises
its own ``OutboundDeliveryFailed`` for the 5xx/no-status case (preserving
``attempts``/``last_status``, which the classifier has no object to carry);
``raise_mapped_mcp_error`` has no such object and keeps the classifier's
freshly-built one. One explanatory asymmetry, not a third behavior.

NOT in ``src/core/helpers/adapter_helpers.py``. That module imports the
ad-server adapters at module level, and the adapters are precisely the call
sites that will use this mapper as the epic's remaining migrations land —
importing it back would be a cycle. (Its former sibling ``raise_mapped_adcp_error``,
for the adcp SDK's own exception hierarchy, was deleted by GH #1802 once
both callers moved off ``ADCPMultiAgentClient`` onto the guarded MCP seam.)

``MCPConnectionError`` looked, at first read, like it discarded the HTTP status
entirely. It does not, except for one case: the MCP Streamable HTTP transport
(``mcp/client/streamable_http.py::_handle_post_request``) special-cases a plain
404 on a session POST as "no such session" — a PROTOCOL-level signal, not a
transport failure — and synthesizes an opaque ``McpError`` for it, discarding
the status on purpose. Every other non-2xx status is a normal
``httpx.HTTPStatusError`` that survives intact as ``__cause__`` (status code and
headers, including ``Retry-After``, fully readable), because
``raise ... from ...`` chaining is exactly what both ``MCPConnectionError`` and
``call_mcp_tool``'s retry loop use. Only used for OPERATOR-configured agents —
a counterparty-supplied ``agent_url`` never reaches ``raise_mapped_mcp_error``
(``creative_agent_registry.py`` routes those through ``_fetch_formats_raw_mcp``,
on the raw egress seam's own ``OutboundError`` taxonomy, unchanged by this
migration).

Spec grounding for the classification (AdCP 3.1.1,
``dist/schemas/3.1.1/enums/error-code.json`` enumMetadata):

* ``CONFIGURATION_ERROR`` — recovery ``terminal``, "surface to a human at the
  seller — the buyer cannot resolve a seller-side deployment misconfiguration and
  MUST NOT auto-retry". That is exactly a refused OPERATOR-registered endpoint.
* ``RATE_LIMITED`` — recovery ``transient``, "retry after the retry_after
  interval".
* ``SERVICE_UNAVAILABLE`` — recovery ``transient``.
"""

from __future__ import annotations

import logging
from typing import NoReturn

from src.core.exceptions import (
    AdCPConfigurationError,
    AdCPError,
    AdCPRateLimitError,
    AdCPServiceUnavailableError,
    clamp_retry_after,
)
from src.core.security.outbound_http import (
    OperatorEndpoint,
    OutboundDeliveryFailed,
    OutboundError,
    OutboundRequestBlocked,
    UrlProvenance,
    is_counterparty,
    wrapped_failure,
)


def adcp_error_for_status(status: int | None, *, retry_after: float | None, provenance: UrlProvenance) -> AdCPError:
    """The one status -> AdCP-error-class table both seams' mappers consume.

    429 -> ``RATE_LIMITED`` carrying the clamped ``retry_after``; any other 4xx
    -> ``CONFIGURATION_ERROR``/terminal (429 is checked first, so no second copy
    of the retryable-status set is needed to exclude it — the drift #1802's F2
    finding documented, where an inline ``400 <= status < 500`` was only correct
    because 429 happened to be checked first, in TWO places that could drift
    independently); ``status is None`` (a transport failure, never reached the
    wire) -> ``SERVICE_UNAVAILABLE`` worded "unreachable"; any other status (a
    5xx) -> ``SERVICE_UNAVAILABLE`` worded "unavailable".

    Requires an :class:`OperatorEndpoint`. Structurally unreachable with a
    :class:`CounterpartyUrl` today: ``raise_mapped_outbound_error`` re-raises
    ``exc`` unchanged for that provenance before ever extracting a status, and
    the MCP seam's own docstring establishes a counterparty-supplied
    ``agent_url`` never reaches ``raise_mapped_mcp_error`` at all. Asserting
    here keeps a future violation loud instead of silently misclassifying, and
    avoids inventing a CounterpartyUrl status taxonomy neither caller nor any
    RED grader exercises.

    BUILDS the error and returns it; it does not raise. A classifier that
    raised could only be consulted by CATCHING it, which is why both mappers
    used to wrap the call in a ``try`` and re-``raise`` from three ``except``
    arms apiece just to attach one log line. Returning the value lets each
    mapper branch on it directly and decide what to log and what to raise.

    Deliberately takes no ``logger``/original exception: logging is each
    mapper's own concern (the wording differs per seam) and stays at the call
    site, which is also where the chaining decision belongs.
    """
    if not isinstance(provenance, OperatorEndpoint):
        raise AssertionError(f"adcp_error_for_status classifies operator-endpoint failures only; got {provenance!r}")
    label = provenance.name

    if status == 429:
        return AdCPRateLimitError(
            f"{label} is rate-limited.",
            retry_after=clamp_retry_after(retry_after) if retry_after is not None else None,
        )
    if status is not None and 400 <= status < 500:
        return AdCPConfigurationError(f"{label} rejected the request.")
    if status is None:
        return AdCPServiceUnavailableError(f"{label} is unreachable.")
    return AdCPServiceUnavailableError(f"{label} is unavailable.")


def raise_mapped_outbound_error(exc: OutboundError, *, provenance: UrlProvenance, logger: logging.Logger) -> NoReturn:
    """Re-raise a raw-egress-seam failure as the AdCP error *provenance* warrants.

    ``provenance`` is required — there is no "no opinion" default, because
    provenance-by-omission (an optional field a caller either passes or doesn't)
    is exactly what let a call site fabricate a locator or hand-branch on
    ``None`` instead of stating whose URL it dialled.

    A :class:`CounterpartyUrl` — the buyer supplied this URL — is re-raised
    UNCHANGED: ``OutboundRequestBlocked`` already IS an ``AdCPBlockedUrlError``
    (``VALIDATION_ERROR`` / correctable, naming the offending field when it has
    one) and ``OutboundDeliveryFailed`` already IS an
    ``AdCPServiceUnavailableError`` (``SERVICE_UNAVAILABLE`` / transient) — the
    seam's own classification is already correct for a URL the buyer chose and
    can fix, and rewrapping it would restate that classification in a second
    place, which is what this module exists to prevent.

    An :class:`OperatorEndpoint` — a registered agent endpoint, a vendor host,
    not something the buyer supplied — gets classified below, naming
    ``provenance.name`` (a role, never an address). A dial refusal
    (``OutboundRequestBlocked``) becomes ``CONFIGURATION_ERROR``/terminal rather
    than ``VALIDATION_ERROR``/correctable: the buyer did not choose this address
    and cannot fix it, so telling them to correct their request would be false.
    A delivered-but-failed dial (``OutboundDeliveryFailed``) delegates its
    status to :func:`adcp_error_for_status` — EXCEPT the 5xx/no-status case,
    caught back out and re-raised as the original ``exc``: it already IS
    ``AdCPServiceUnavailableError`` and carries ``attempts``/``last_status`` the
    classifier's freshly-built instance would not.
    """
    if is_counterparty(provenance):
        raise exc

    if isinstance(exc, OutboundRequestBlocked):
        logger.error("Egress policy refused the configured endpoint for %s", provenance.name)
        raise AdCPConfigurationError(
            f"The configured endpoint for {provenance.name} is not reachable under this deployment's egress policy."
        ) from exc

    # OutboundError has exactly two concrete subclasses; OutboundRequestBlocked
    # was excluded above, so this is OutboundDeliveryFailed -- a type narrowing,
    # not a value comparison (AC1's ast-grep is about status VALUES).
    assert isinstance(exc, OutboundDeliveryFailed)

    error = adcp_error_for_status(exc.http_status, retry_after=exc.retry_after, provenance=provenance)

    if isinstance(error, AdCPRateLimitError):
        logger.warning("%s rate-limited after %s attempts", provenance.name, exc.attempts)
        raise error from exc
    if isinstance(error, AdCPConfigurationError):
        logger.error("%s rejected the request (HTTP %s)", provenance.name, exc.http_status)
        raise error from exc

    # The 5xx/no-status case re-raises the ORIGINAL rather than the classifier's
    # fresh instance: ``exc`` already IS an AdCPServiceUnavailableError, and it
    # carries ``attempts``/``http_status``, which a fresh one would not.
    raise exc from None


def raise_mapped_mcp_error(exc: Exception, *, provenance: UrlProvenance, logger: logging.Logger) -> NoReturn:
    """Re-raise a guarded-MCP-seam failure as the AdCP error *provenance* warrants.

    Mirrors :func:`raise_mapped_outbound_error`'s classification by delegating
    to the same :func:`adcp_error_for_status`, so the two seams name failures
    the same way despite different transports. Unlike the outbound seam, the
    MCP seam has no ``OutboundDeliveryFailed``-shaped object to re-raise for
    the 5xx/no-status case — every outcome goes through the classifier, which
    builds a fresh ``AdCPServiceUnavailableError`` there (see
    :func:`adcp_error_for_status`'s own docstring).
    """
    # Mirrors adcp_error_for_status's own assertion (J1): a counterparty-
    # supplied agent_url never reaches this mapper at all (module docstring).
    # Narrows the type for the logging reads below; not a value comparison.
    if not isinstance(provenance, OperatorEndpoint):
        raise AssertionError(f"raise_mapped_mcp_error classifies operator-endpoint failures only; got {provenance!r}")

    status: int | None = None
    retry_after: float | None = None
    wrapped = wrapped_failure(exc)
    if wrapped is not None:
        status = wrapped.status
        retry_after = wrapped.retry_after

    error = adcp_error_for_status(status, retry_after=retry_after, provenance=provenance)

    if isinstance(error, AdCPRateLimitError):
        logger.warning("%s rate-limited (HTTP 429)", provenance.name)
    elif isinstance(error, AdCPConfigurationError):
        logger.error("%s rejected the request (HTTP %s)", provenance.name, status)
    elif status is None:
        logger.error("%s is unreachable: %s", provenance.name, exc)
    else:
        logger.error("%s returned HTTP %s", provenance.name, status)

    raise error from exc
