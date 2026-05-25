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

Call sites must record AI-review metrics through :func:`record_ai_review` and
:func:`record_ai_review_error` so the bounding logic lives in exactly one place.
"""

from prometheus_client import REGISTRY, Counter, Gauge, Histogram, generate_latest

from src.core.exceptions import (
    AdCPRateLimitError,
    AdCPServiceUnavailableError,
    AdCPValidationError,
)

# ---------------------------------------------------------------------------
# Bounded label vocabularies
# ---------------------------------------------------------------------------

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
    if value in POLICY_TRIGGERED_ALLOWLIST:
        return value
    return "other"


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


def get_metrics_text() -> str:
    """Return current metrics in Prometheus text format."""
    return generate_latest(REGISTRY).decode("utf-8")
