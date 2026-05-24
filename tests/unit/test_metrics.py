"""Tests for Prometheus metrics module."""


def test_metrics_are_registered():
    """Test that all metrics are registered with Prometheus."""
    from src.core.metrics import (
        active_ai_reviews,
        ai_review_confidence,
        ai_review_duration,
        ai_review_errors,
        ai_review_total,
        webhook_delivery_attempts,
        webhook_delivery_duration,
        webhook_delivery_total,
        webhook_queue_size,
    )

    # Verify metrics are registered (Prometheus client strips "_total" suffix from Counter names)
    assert ai_review_total._name == "ai_review"  # Counter - _total is stripped
    assert ai_review_duration._name == "ai_review_duration_seconds"
    assert ai_review_errors._name == "ai_review_errors"  # Counter - _total is stripped
    assert ai_review_confidence._name == "ai_review_confidence"
    assert active_ai_reviews._name == "active_ai_reviews"

    assert webhook_delivery_total._name == "webhook_delivery"  # Counter - _total is stripped
    assert webhook_delivery_duration._name == "webhook_delivery_duration_seconds"
    assert webhook_delivery_attempts._name == "webhook_delivery_attempts"
    assert webhook_queue_size._name == "webhook_queue_size"


def test_ai_review_counter_increments():
    """Test that AI review counter increments correctly."""
    from src.core.metrics import ai_review_total

    # Get initial value
    initial_value = ai_review_total.labels(
        tenant_id="test_tenant", decision="approved", policy_triggered="auto_approve"
    )._value.get()

    # Increment counter
    ai_review_total.labels(tenant_id="test_tenant", decision="approved", policy_triggered="auto_approve").inc()

    # Verify increment
    new_value = ai_review_total.labels(
        tenant_id="test_tenant", decision="approved", policy_triggered="auto_approve"
    )._value.get()
    assert new_value == initial_value + 1


def test_ai_review_duration_observes():
    """AI review duration histogram records observations (no tenant_id label —
    histograms are aggregated to bound cardinality)."""
    from src.core.metrics import ai_review_duration

    # Observe duration (no labels — histogram is unlabeled)
    ai_review_duration.observe(2.5)

    # Verify observation was recorded (check sum)
    assert ai_review_duration._sum.get() >= 2.5


def test_ai_review_confidence_observes():
    """AI review confidence histogram records observations, labeled only by
    the bounded ``decision`` enum (no tenant_id)."""
    from src.core.metrics import ai_review_confidence

    # Observe confidence score
    ai_review_confidence.labels(decision="approved").observe(0.95)

    # Verify observation was recorded
    metric = ai_review_confidence.labels(decision="approved")
    assert metric._sum.get() >= 0.95


def test_ai_review_errors_increments():
    """AI review error counter increments via the bounded recording helper.

    ``error_type`` is the categorized enum value (e.g. ``ValueError`` ->
    ``validation``), never the raw exception class name.
    """
    from src.core.metrics import ai_review_errors, record_ai_review_error

    # ValueError categorizes to "validation"
    initial_value = ai_review_errors.labels(tenant_id="test_tenant", error_type="validation")._value.get()

    record_ai_review_error(tenant_id="test_tenant", error=ValueError("bad input"))

    new_value = ai_review_errors.labels(tenant_id="test_tenant", error_type="validation")._value.get()
    assert new_value == initial_value + 1


def test_active_ai_reviews_gauge():
    """Test that active AI reviews gauge can increment and decrement."""
    from src.core.metrics import active_ai_reviews

    # Get initial value
    initial_value = active_ai_reviews.labels(tenant_id="test_tenant")._value.get()

    # Increment gauge
    active_ai_reviews.labels(tenant_id="test_tenant").inc()
    assert active_ai_reviews.labels(tenant_id="test_tenant")._value.get() == initial_value + 1

    # Decrement gauge
    active_ai_reviews.labels(tenant_id="test_tenant").dec()
    assert active_ai_reviews.labels(tenant_id="test_tenant")._value.get() == initial_value


def test_webhook_delivery_counter():
    """Test that webhook delivery counter increments correctly."""
    from src.core.metrics import webhook_delivery_total

    # Get initial value
    initial_value = webhook_delivery_total.labels(
        tenant_id="test_tenant", event_type="creative_approved", status="success"
    )._value.get()

    # Increment counter
    webhook_delivery_total.labels(tenant_id="test_tenant", event_type="creative_approved", status="success").inc()

    # Verify increment
    new_value = webhook_delivery_total.labels(
        tenant_id="test_tenant", event_type="creative_approved", status="success"
    )._value.get()
    assert new_value == initial_value + 1


def test_webhook_delivery_duration():
    """Webhook delivery duration histogram records observations, labeled only
    by the bounded ``event_type`` (no tenant_id)."""
    from src.core.metrics import webhook_delivery_duration

    # Observe duration
    webhook_delivery_duration.labels(event_type="creative_approved").observe(0.5)

    # Verify observation was recorded
    metric = webhook_delivery_duration.labels(event_type="creative_approved")
    assert metric._sum.get() >= 0.5


def test_webhook_delivery_attempts():
    """Webhook delivery attempts histogram records observations, labeled only
    by the bounded ``event_type`` (no tenant_id)."""
    from src.core.metrics import webhook_delivery_attempts

    # Observe attempts
    webhook_delivery_attempts.labels(event_type="creative_approved").observe(3)

    # Verify observation was recorded
    metric = webhook_delivery_attempts.labels(event_type="creative_approved")
    assert metric._sum.get() >= 3


def test_webhook_queue_size_gauge():
    """Test that webhook queue size gauge works correctly."""
    from src.core.metrics import webhook_queue_size

    # Get initial value
    initial_value = webhook_queue_size.labels(tenant_id="test_tenant")._value.get()

    # Set gauge value
    webhook_queue_size.labels(tenant_id="test_tenant").set(5)
    assert webhook_queue_size.labels(tenant_id="test_tenant")._value.get() == 5

    # Increment gauge
    webhook_queue_size.labels(tenant_id="test_tenant").inc(2)
    assert webhook_queue_size.labels(tenant_id="test_tenant")._value.get() == 7

    # Decrement gauge
    webhook_queue_size.labels(tenant_id="test_tenant").dec(3)
    assert webhook_queue_size.labels(tenant_id="test_tenant")._value.get() == 4


def test_get_metrics_text():
    """Test that get_metrics_text returns valid Prometheus format."""
    from src.core.metrics import ai_review_total, get_metrics_text

    # Increment a metric so we have something to see
    ai_review_total.labels(tenant_id="test_metrics_text", decision="approved", policy_triggered="auto_approve").inc()

    # Get metrics text
    metrics_text = get_metrics_text()

    # Verify it's a string
    assert isinstance(metrics_text, str)

    # Verify it contains Prometheus format
    assert "# HELP" in metrics_text
    assert "# TYPE" in metrics_text

    # Verify our metric is present
    assert "ai_review_total" in metrics_text


def test_metrics_labels():
    """Test that metrics support different label combinations."""
    from src.core.metrics import ai_review_total

    # Test different label combinations
    labels = [
        ("tenant1", "approved", "auto_approve"),
        ("tenant1", "pending", "sensitive_category"),
        ("tenant2", "rejected", "auto_reject"),
        ("tenant2", "pending", "uncertain"),
    ]

    for tenant_id, decision, policy_triggered in labels:
        initial = ai_review_total.labels(
            tenant_id=tenant_id, decision=decision, policy_triggered=policy_triggered
        )._value.get()
        ai_review_total.labels(tenant_id=tenant_id, decision=decision, policy_triggered=policy_triggered).inc()
        new = ai_review_total.labels(
            tenant_id=tenant_id, decision=decision, policy_triggered=policy_triggered
        )._value.get()
        assert new == initial + 1


def test_histogram_buckets():
    """Test that histograms have correct bucket definitions."""
    from src.core.metrics import ai_review_confidence, ai_review_duration, webhook_delivery_duration

    # AI review duration should have buckets for seconds
    duration_buckets = ai_review_duration._upper_bounds
    expected_duration_buckets = [0.5, 1.0, 2.0, 5.0, 10.0, 30.0, float("inf")]
    assert duration_buckets == expected_duration_buckets

    # AI review confidence should have 0.1 increments
    confidence_buckets = ai_review_confidence._upper_bounds
    expected_confidence_buckets = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, float("inf")]
    assert confidence_buckets == expected_confidence_buckets

    # Webhook delivery duration should have sub-second buckets
    webhook_buckets = webhook_delivery_duration._upper_bounds
    expected_webhook_buckets = [0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, float("inf")]
    assert webhook_buckets == expected_webhook_buckets


def test_metrics_thread_safety():
    """Test that metrics can be safely incremented from multiple threads."""
    import threading

    from src.core.metrics import ai_review_total

    # Get initial value
    tenant_id = "test_thread_safety"
    initial_value = ai_review_total.labels(
        tenant_id=tenant_id, decision="approved", policy_triggered="auto_approve"
    )._value.get()

    # Increment from multiple threads
    num_threads = 10
    increments_per_thread = 100
    threads = []

    def increment_counter():
        for _ in range(increments_per_thread):
            ai_review_total.labels(tenant_id=tenant_id, decision="approved", policy_triggered="auto_approve").inc()

    for _ in range(num_threads):
        t = threading.Thread(target=increment_counter)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # Verify all increments were recorded
    final_value = ai_review_total.labels(
        tenant_id=tenant_id, decision="approved", policy_triggered="auto_approve"
    )._value.get()
    expected_value = initial_value + (num_threads * increments_per_thread)
    assert final_value == expected_value
