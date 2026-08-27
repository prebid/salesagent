"""Prometheus metrics for monitoring AI review and webhook operations.

Label cardinality is deliberately bounded to keep memory flat for a
long-running multi-tenant process:

- **Histograms** never label by ``tenant_id``. Each series allocates a full
  bucket array, so a per-tenant label makes memory grow linearly with the
  tenant count. Latency views stay aggregated; per-tenant *volume* is still
  available on the cheaper Counters.
- **``error_type``** is collapsed to a fixed enum via :func:`categorize_error`
  instead of ``type(e).__name__`` (otherwise unbounded as code evolves, and
  attacker-influenceable).
- **``policy_triggered``** is validated against :data:`POLICY_TRIGGERED_ALLOWLIST`
  via :func:`sanitize_policy_triggered`; unknown values collapse to ``"other"``.
- **``code``** (request-signature outcomes) is validated against the SDK's own
  request-family taxonomy via :func:`sanitize_signature_code`; anything else —
  including anything an attacker could induce — collapses to ``"other"``.
- **``operation``** is the sharpest of these, because the verifier runs ABOVE
  authentication and the value arrives VERBATIM out of the request body on two of
  the three transports (an MCP ``tools/call``'s ``params.name``, an A2A
  ``message/send``'s ``data.skill``). Recorded raw, one anonymous POST per distinct
  value mints one series per request. :func:`sanitize_operation` bounds it against
  :func:`~src.core.signing_contract.resolved_operation_names` — the closed set DERIVED from
  the transport registries, never hand-listed here.
- **``reason``** (unsigned verdicts) is the closed set :data:`UNSIGNED_REASONS` via
  :func:`sanitize_unsigned_reason`.
- **``keyid``** is the signer's real key id ONLY after the verifier resolved it
  (checklist step 7), i.e. only for a key we already recognize. Before that it is
  attacker-supplied, so it is recorded as ``"unresolved"``.
- **``reason``** (revocation availability) is the closed set
  :data:`REVOCATION_UNAVAILABLE_REASONS` via
  :func:`sanitize_revocation_unavailable_reason`. Deliberately NOT the issuer origin,
  which is derived from a counterparty-supplied ``brand_json_url`` and would let
  a pre-auth caller mint series; the origin goes in the WARNING log instead.

Call sites must record AI-review metrics through :func:`record_ai_review` and
:func:`record_ai_review_error`, and request-signature outcomes through
:func:`record_signature_verified` / :func:`record_signature_failed` /
:func:`record_request_unsigned`, so the bounding logic lives in exactly one place.

That "exactly one place" is INSIDE the recording helpers, never at their call sites.
The four ``record_request_unsigned`` call sites in
``src/core/signing/request_verifier_middleware.py`` (:452 on the absent-signature arm,
and :492, :506 and :624 on the three ignored-posture exits the verifier's pre-check
phase created) are the standing proof: sanitizing at any one of them leaves the other
three minting series, and a sanitizer you have to REMEMBER to call is the same shape of
defect as the unbounded label it was added to fix.
"""

from collections.abc import Container

from prometheus_client import REGISTRY, Counter, Gauge, Histogram, generate_latest

from src.core.exceptions import (
    AdCPRateLimitError,
    AdCPServiceUnavailableError,
    AdCPValidationError,
)
from src.core.signing_contract import REQUEST_TO_WEBHOOK_CODE, resolved_operation_names

# ---------------------------------------------------------------------------
# Bounded label vocabularies
# ---------------------------------------------------------------------------

#: The one value every bounded label collapses to. A single shared bucket is what
#: makes "series count is a function of the vocabulary, not of the caller" true.
OTHER_LABEL = "other"


def _bounded(value: str | None, vocabulary: Container[str]) -> str:
    """Return *value* when the closed *vocabulary* admits it, else :data:`OTHER_LABEL`.

    Every ``sanitize_*`` below is this one rule with a different vocabulary, so the
    rule is written once. The NAMED wrappers are the contract — each states which
    vocabulary bounds which label — and this is only their shared body.
    """
    return value if value is not None and value in vocabulary else OTHER_LABEL


#: Fixed enum for the ``error_type`` label. Keep <= 5 values.
ERROR_TYPE_VALUES = ("validation", "timeout", "model_error", "other")

#: Closed set of ``policy_triggered`` values emitted by the AI review flow.
#: Anything outside this set (e.g. an AI-generated free-form reason) collapses
#: to ``"other"`` to prevent unbounded series growth.
POLICY_TRIGGERED_ALLOWLIST = frozenset(
    {
        "sensitive_category",
        "auto_approve",
        "low_confidence_approval",
        "auto_reject",
        "uncertain_rejection",
        "uncertain",
        "other",
    }
)


def categorize_error(error: BaseException) -> str:
    """Collapse an arbitrary exception into a bounded ``error_type`` enum.

    The mapping is intentionally coarse — its only job is to keep Prometheus
    series count constant regardless of how many exception classes exist.
    """
    # Timeouts first: a TimeoutError may also subclass OSError, and project
    # AdCP errors that mean "service unavailable" are timeout-ish operationally.
    if isinstance(error, TimeoutError | AdCPServiceUnavailableError | AdCPRateLimitError):
        return "timeout"
    if isinstance(error, ValueError | TypeError | KeyError | AdCPValidationError):
        return "validation"
    # AI/model layer surfaces failures as RuntimeError or connection errors.
    if isinstance(error, RuntimeError | ConnectionError):
        return "model_error"
    return "other"


def sanitize_policy_triggered(value: str | None) -> str:
    """Return ``value`` if it is in the allowlist, else ``"other"``."""
    return _bounded(value, POLICY_TRIGGERED_ALLOWLIST)


#: The 27 request-family signature rejection codes, taken from the SDK's own
#: request->webhook translation table rather than re-listed here. The spec grades
#: these byte-for-byte, so a hand-maintained copy would be a second source that can
#: drift from the one the verifier actually raises.
SIGNATURE_ERROR_CODES = frozenset(REQUEST_TO_WEBHOOK_CODE)

#: ``keyid`` before the verifier resolved one (checklist step 7). Every rejection
#: carries this: ``SignatureVerificationError`` does not expose the keyid, and a
#: pre-resolution keyid is attacker-supplied and therefore unbounded.
UNRESOLVED_KEYID = "unresolved"

#: Why a request was not verified. Two values, both closed.
UNSIGNED_REASONS = frozenset({"absent", "ignored"})


def sanitize_signature_code(code: str | None) -> str:
    """Return ``code`` if it is a spec request-signature code, else ``"other"``."""
    return _bounded(code, SIGNATURE_ERROR_CODES)


def sanitize_operation(operation: str | None) -> str:
    """Return ``operation`` if a transport registry names it, else ``"other"``.

    THE attacker-facing label in this module. The verifier that records it sits ABOVE
    authentication, and on two of the three transports the value is lifted verbatim
    out of the request body (``params.name``, ``data.skill``), so an anonymous caller
    chooses it. The closed set comes from
    :func:`~src.core.signing_contract.resolved_operation_names`, which DERIVES it from the
    SDK definitions, the registered MCP tools, the ``/api/v1`` route table and the A2A
    skill table — the same registries the resolver names requests from. Re-listing
    those names here would create a second source of truth that silently demotes a
    newly added tool's real traffic into the ``"other"`` bucket.

    ``""`` is a member of that set, not a collapse: it is what the resolver returns
    for a request named in the JSON-RPC PROTOCOL namespace or carrying no body at all,
    and keeping it distinct is what stops every MCP handshake from landing in the
    bucket that exists to make an attacker-supplied name visible.
    """
    return _bounded(operation, resolved_operation_names())


def sanitize_unsigned_reason(reason: str | None) -> str:
    """Return ``reason`` if it is an :data:`UNSIGNED_REASONS` member, else ``"other"``."""
    return _bounded(reason, UNSIGNED_REASONS)


# ---------------------------------------------------------------------------
# AI Review Metrics
# ---------------------------------------------------------------------------
ai_review_total = Counter(
    "ai_review_total",
    "Total AI reviews performed",
    ["tenant_id", "decision", "policy_triggered"],
)

ai_review_duration = Histogram(
    "ai_review_duration_seconds",
    "AI review latency in seconds (aggregated across tenants)",
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)

ai_review_errors = Counter(
    "ai_review_errors_total",
    "AI review errors by bounded error type",
    ["tenant_id", "error_type"],
)

ai_review_confidence = Histogram(
    "ai_review_confidence",
    "AI review confidence scores (0-1, aggregated across tenants)",
    ["decision"],
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

# ---------------------------------------------------------------------------
# Webhook Metrics
# ---------------------------------------------------------------------------
webhook_delivery_total = Counter(
    "webhook_delivery_total",
    "Total webhook deliveries",
    ["tenant_id", "event_type", "status"],
)

webhook_delivery_duration = Histogram(
    "webhook_delivery_duration_seconds",
    "Webhook delivery latency in seconds (aggregated across tenants)",
    ["event_type"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

webhook_delivery_attempts = Histogram(
    "webhook_delivery_attempts",
    "Number of delivery attempts before success (aggregated across tenants)",
    ["event_type"],
    buckets=[1, 2, 3, 4, 5],
)

# ---------------------------------------------------------------------------
# Active monitoring gauges
# ---------------------------------------------------------------------------
# Gauges are keyed by tenant_id but are self-bounding: they track *currently
# active* work, so series stay proportional to live concurrency, not history.
active_ai_reviews = Gauge(
    "active_ai_reviews",
    "Currently running AI reviews",
    ["tenant_id"],
)

webhook_queue_size = Gauge(
    "webhook_queue_size",
    "Number of webhooks pending delivery",
    ["tenant_id"],
)

# ---------------------------------------------------------------------------
# RFC 9421 inbound request-signature outcomes (#1291 B1)
# ---------------------------------------------------------------------------
# The middleware is the ONLY layer that sees the verifier's outcome before it is
# swallowed (``warn_for``) or turned into a transport 401, so these three counters
# are the whole evidence base for the shadow-mode promotion ladder
# (supported_for -> warn_for -> required_for). No ``tenant_id`` label: the posture
# is per-tenant but the series count must not grow with the tenant list.
request_signature_verified_total = Counter(
    "adcp_request_signature_verified_total",
    "Inbound RFC 9421 request signatures that passed the verifier checklist",
    ["operation", "keyid"],
)

request_signature_failed_total = Counter(
    "adcp_request_signature_failed_total",
    "Inbound RFC 9421 request signatures rejected by the verifier checklist",
    ["operation", "keyid", "code"],
)

request_unsigned_total = Counter(
    "adcp_request_unsigned_total",
    "Inbound AdCP requests the verifier did not grade (no signature, or posture ignores it)",
    ["operation", "reason"],
)

# ---------------------------------------------------------------------------
# Revocation availability — checklist step 9 (#1291 A5)
# ---------------------------------------------------------------------------
# The evidence base for flipping ``SigningConfig.require_revocation_list``: every
# increment is a signed request served WITHOUT a revocation answer. No ``issuer``
# label — the issuer origin comes from a counterparty-supplied ``brand_json_url``,
# so labelling by it would let a PRE-AUTH caller mint series at will. ``reason`` is
# the closed set of ways the SDK's fetch can fail, and it stays in lockstep with the
# translation site's exception tuple in ``src/core/signing/revocation.py``. The
# issuer origin is carried in that module's WARNING log line, where cardinality is
# free and the operator actually wants it.
request_revocation_unavailable_total = Counter(
    "adcp_request_revocation_unavailable_total",
    "Signed requests served without a revocation answer because the list could not be read",
    ["reason"],
)

#: Closed vocabulary for the ``reason`` label above — one member per member of the
#: exception tuple in ``CounterpartyRevocationChecker.__call__``.
REVOCATION_UNAVAILABLE_REASONS = frozenset({"fetch", "parse", "signature", "ssrf"})


def sanitize_revocation_unavailable_reason(reason: str | None) -> str:
    """Return ``reason`` if it is a :data:`REVOCATION_UNAVAILABLE_REASONS` member.

    Anything else collapses to ``"other"``, which keeps the series count fixed even
    if a future exception member is added to the translation site without its label.
    """
    return _bounded(reason, REVOCATION_UNAVAILABLE_REASONS)


# ---------------------------------------------------------------------------
# Recording helpers — single source of truth for label bounding
# ---------------------------------------------------------------------------
def record_ai_review(tenant_id: str, decision: str, policy_triggered: str | None) -> None:
    """Increment :data:`ai_review_total` with a bounded ``policy_triggered``."""
    ai_review_total.labels(
        tenant_id=tenant_id,
        decision=decision,
        policy_triggered=sanitize_policy_triggered(policy_triggered),
    ).inc()


def record_ai_review_error(tenant_id: str, error: BaseException) -> None:
    """Increment :data:`ai_review_errors` with a bounded ``error_type``."""
    ai_review_errors.labels(tenant_id=tenant_id, error_type=categorize_error(error)).inc()


def record_signature_verified(operation: str, keyid: str) -> None:
    """Increment :data:`request_signature_verified_total` for a verified signer.

    ``keyid`` is safe to record verbatim here and ONLY here: the verifier resolved it
    against the counterparty's JWKS at checklist step 7, so the value is drawn from a
    key set we already know. ``operation`` never is — it comes off the wire on every
    transport — so it is bounded here like it is on the other two counters.
    """
    request_signature_verified_total.labels(operation=sanitize_operation(operation), keyid=keyid).inc()


def record_signature_failed(operation: str, code: str | None) -> None:
    """Increment :data:`request_signature_failed_total` with bounded labels."""
    request_signature_failed_total.labels(
        operation=sanitize_operation(operation),
        keyid=UNRESOLVED_KEYID,
        code=sanitize_signature_code(code),
    ).inc()


def record_request_unsigned(operation: str, reason: str) -> None:
    """Increment :data:`request_unsigned_total` with bounded labels.

    ``reason="absent"`` — the request carried no signature headers.
    ``reason="ignored"`` — headers were present but the tenant's posture puts this
    operation in the ``none`` bucket, so no CHECKLIST ran.

    What "ignored" costs depends on which half of the ``none`` bucket answered, and the
    two differ since the verifier gained its pre-check phase:

    * ``supported: false`` — the seller does not verify at all, so the spec's pre-check
      does not bind it and R-H3 still holds exactly: nothing buffered, nothing hashed.
    * ``supported: true`` with the operation in none of the three lists — a VERIFIER, so
      AdCP 3.1.1 ``security.mdx`` :1226 binds it "even for operations not in
      ``required_for``". The body is buffered and the SDK parses the headers, against an
      EMPTY resolver that stops execution at ``verifier.py:256``. Bounded and in-memory:
      no key resolution, no crypto, no outbound walk, no database session.

    All four of this helper's call sites are pre-auth arms of the verifier, and all are
    reached with an ``operation`` the anonymous caller chose — which is why the bounding
    is here and not at any of them.
    """
    request_unsigned_total.labels(
        operation=sanitize_operation(operation),
        reason=sanitize_unsigned_reason(reason),
    ).inc()


def record_signature_revocation_unavailable(reason: str) -> None:
    """Increment :data:`request_revocation_unavailable_total` with a bounded ``reason``.

    Called on the fail-open path only: the counterparty's revocation list could not
    be read at all, so step 9 was answered from the local set alone.
    """
    request_revocation_unavailable_total.labels(
        reason=sanitize_revocation_unavailable_reason(reason),
    ).inc()


def get_metrics_text() -> str:
    """Return current metrics in Prometheus text format."""
    return generate_latest(REGISTRY).decode("utf-8")
