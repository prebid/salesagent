"""Regression tests for Prometheus label cardinality bounds.

These tests guard against the OOM contributor identified 2026-05-18: per-tenant
Prometheus series accumulating without bound, plus unbounded ``error_type`` (from
``type(e).__name__``) and free-form ``policy_triggered`` labels.

See salesagent-x2h.10.1.
"""

from prometheus_client import Histogram


def _series_count(collector) -> int:
    """Number of distinct label series exposed by a Counter/Gauge collector."""
    return len(list(collector.collect())[0].samples)


def test_no_histogram_has_tenant_id_label():
    """Histograms allocate a bucket array per series — tenant_id makes them
    grow linearly with tenant count. No Histogram may carry tenant_id."""
    from src.core import metrics

    offenders = []
    for name in dir(metrics):
        obj = getattr(metrics, name)
        if isinstance(obj, Histogram):
            label_names = obj._labelnames
            if "tenant_id" in label_names:
                offenders.append((name, label_names))

    assert not offenders, f"Histograms must not label by tenant_id: {offenders}"


def test_categorize_error_bounds_error_type_to_enum():
    """categorize_error must collapse arbitrary exceptions into a fixed enum."""
    from src.core.metrics import categorize_error

    allowed = {"validation", "timeout", "model_error", "other"}

    # 1000 distinct exception classes must all map into the fixed enum.
    seen = set()
    for i in range(1000):
        exc_cls = type(f"FakeError{i}", (Exception,), {})
        seen.add(categorize_error(exc_cls("boom")))

    assert seen <= allowed, f"categorize_error produced out-of-enum values: {seen - allowed}"
    assert len(allowed) <= 5


def test_record_ai_review_error_cardinality_bounded():
    """Recording 1000 unique error types for one tenant must produce a bounded
    number of series (<= enum size, accounting for Counter's _total/_created
    sample pair)."""
    from src.core import metrics

    metrics.ai_review_errors.clear()
    for i in range(1000):
        exc_cls = type(f"FakeError{i}", (Exception,), {})
        metrics.record_ai_review_error(tenant_id="t1", error=exc_cls("boom"))

    samples = list(metrics.ai_review_errors.collect())[0].samples
    # One tenant x <=4 enum error types. prometheus emits _total + _created per
    # label set, so <= 4 * 2 = 8; allow headroom up to 10.
    assert len(samples) <= 10, f"Expected bounded error_type series, got {len(samples)}"


def test_sanitize_policy_triggered_allowlist():
    """Unknown / AI-driven free-form policy_triggered values collapse to 'other'."""
    from src.core.metrics import POLICY_TRIGGERED_ALLOWLIST, sanitize_policy_triggered

    # Known values pass through unchanged.
    for known in POLICY_TRIGGERED_ALLOWLIST:
        assert sanitize_policy_triggered(known) == known

    # Arbitrary AI-generated strings collapse to a single bucket.
    for i in range(1000):
        assert sanitize_policy_triggered(f"ai_made_up_reason_{i}") == "other"

    assert sanitize_policy_triggered(None) == "other"


def test_ai_review_total_cardinality_bounded_under_freeform_policy():
    """Feeding 1000 free-form policy_triggered values through the recording
    path must not explode ai_review_total series for a single tenant/decision."""
    from src.core import metrics

    metrics.ai_review_total.clear()
    for i in range(1000):
        metrics.record_ai_review(
            tenant_id="t1",
            decision="pending_review",
            policy_triggered=f"free_form_{i}",
        )

    samples = list(metrics.ai_review_total.collect())[0].samples
    # tenant t1 x decision pending_review x policy in {whatever known + other}.
    # Free-form all collapse to 'other' -> 1 label set -> <= 2 samples.
    assert len(samples) <= 4, f"Expected bounded policy_triggered series, got {len(samples)}"
