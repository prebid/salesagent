"""Prometheus metrics for monitoring AI review and webhook operations."""

from prometheus_client import REGISTRY, Counter, Gauge, Histogram, generate_latest

# AI Review Metrics
ai_review_total = Counter(
    "ai_review_total",
    "Total AI reviews performed",
    ["tenant_id", "decision", "policy_triggered"],
)

ai_review_duration = Histogram(
    "ai_review_duration_seconds",
    "AI review latency in seconds",
    ["tenant_id"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)

ai_review_errors = Counter(
    "ai_review_errors_total",
    "AI review errors by type",
    ["tenant_id", "error_type"],
)

ai_review_confidence = Histogram(
    "ai_review_confidence",
    "AI review confidence scores (0-1)",
    ["tenant_id", "decision"],
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

# Webhook Metrics
webhook_delivery_total = Counter(
    "webhook_delivery_total",
    "Total webhook deliveries",
    ["tenant_id", "event_type", "status"],
)

webhook_delivery_duration = Histogram(
    "webhook_delivery_duration_seconds",
    "Webhook delivery latency in seconds",
    ["tenant_id", "event_type"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

webhook_delivery_attempts = Histogram(
    "webhook_delivery_attempts",
    "Number of delivery attempts before success",
    ["tenant_id", "event_type"],
    buckets=[1, 2, 3, 4, 5],
)

# Active monitoring gauges
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

# Distinct from ``webhook_delivery_total{status='failure'}`` so operators
# can page on credential errors without drowning in transient buyer-endpoint
# failures. Slice 3 of signing-non-embedded.
webhook_signing_misconfigured_total = Counter(
    "webhook_signing_misconfigured_total",
    "Outbound webhook signing failures caused by tenant credential config "
    "(missing key, KMS-backed, unreadable PEM, weak/missing HMAC secret) — "
    "NOT buyer-endpoint failures.",
    ["tenant_id", "signing_mode"],
)


def get_metrics_text() -> str:
    """Return current metrics in Prometheus text format."""
    return generate_latest(REGISTRY).decode("utf-8")
