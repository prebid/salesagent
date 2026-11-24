"""Regression tests for Prometheus label cardinality bounds.

These tests guard against the OOM contributor identified 2026-05-18: per-tenant
Prometheus series accumulating without bound, plus unbounded ``error_type`` (from
``type(e).__name__``) and free-form ``policy_triggered`` labels.


"""

import pytest
from prometheus_client import Counter, Histogram

from tests.helpers.metrics import STATUS_SCHEDULER, counter_total


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
    from src.core.metrics import ERROR_TYPE_VALUES, categorize_error

    allowed = set(ERROR_TYPE_VALUES)

    # 1000 distinct exception classes must all map into the fixed enum.
    seen = set()
    for i in range(1000):
        exc_cls = type(f"FakeError{i}", (Exception,), {})
        seen.add(categorize_error(exc_cls("boom")))

    assert seen <= allowed, f"categorize_error produced out-of-enum values: {seen - allowed}"
    assert len(allowed) <= 5


#: Labels a recording helper bounds before the counter sees them.
_BOUNDED_LABELS = frozenset({"error_type", "scheduler", "policy_triggered"})

#: One row per bounded label on a tenant-scoped counter: the recording call that
#: receives a free-form value, and the one label set every such value must land on.
_COLLAPSE_CASES = [
    pytest.param(
        "scheduler_isolation_errors",
        lambda m, v: m.record_scheduler_isolation_error(scheduler=STATUS_SCHEDULER, tenant_id="t1", error_type=v),
        {"scheduler": STATUS_SCHEDULER, "error_type": "other"},
        id="scheduler_isolation_errors-error_type",
    ),
    pytest.param(
        "scheduler_isolation_errors",
        lambda m, v: m.record_scheduler_isolation_error(scheduler=v, tenant_id="t1", error_type="other"),
        {"scheduler": "other", "error_type": "other"},
        id="scheduler_isolation_errors-scheduler",
    ),
    pytest.param(
        "ai_review_errors",
        lambda m, v: m.record_ai_review_error(tenant_id="t1", error=type(v, (Exception,), {})("boom")),
        {"error_type": "other"},
        id="ai_review_errors-error_type",
    ),
    pytest.param(
        "ai_review_total",
        lambda m, v: m.record_ai_review(tenant_id="t1", decision="pending_review", policy_triggered=v),
        {"decision": "pending_review", "policy_triggered": "other"},
        id="ai_review_total-policy_triggered",
    ),
]


def test_every_bounded_label_counter_has_a_collapse_row():
    """A tenant-scoped Counter with a bounded label must have a row in ``_COLLAPSE_CASES``.

    Derived from ``src.core.metrics``, so a new counter cannot ship with its bound ungraded.
    """
    from src.core import metrics

    bounded = {
        name
        for name in dir(metrics)
        if isinstance(counter := getattr(metrics, name), Counter)
        and "tenant_id" in counter._labelnames
        and _BOUNDED_LABELS & set(counter._labelnames)
    }
    assert bounded == {case.values[0] for case in _COLLAPSE_CASES}


@pytest.mark.parametrize(
    ("vocabulary", "pinned"),
    [
        ("ERROR_TYPE_VALUES", {"validation", "timeout", "model_error", "other"}),
        ("SCHEDULER_ERROR_TYPE_VALUES", {"db_error", "other"}),
    ],
)
def test_error_type_vocabulary_is_pinned(vocabulary, pinned):
    """A literal pin, not derived from the module: widening an ``error_type`` enum is a decision."""
    from src.core import metrics

    assert set(getattr(metrics, vocabulary)) == pinned


@pytest.mark.parametrize(("counter_name", "record", "collapsed"), _COLLAPSE_CASES)
def test_free_form_label_value_collapses_to_one_series(counter_name, record, collapsed):
    """1000 free-form values through the recording path land on exactly one label set.

    The recording helper is the last line of defense, not the classifier: an
    unknown value must collapse to its bucket instead of minting a series.
    """
    from src.core import metrics

    counter = getattr(metrics, counter_name)
    counter.clear()
    for i in range(1000):
        record(metrics, f"free_form_{i}")

    # One label set; prometheus emits _total + _created for it.
    assert _series_count(counter) == 2
    family = list(counter.collect())[0].name
    assert counter_total(f"{family}_total", tenant_id="t1", **collapsed) == 1000


@pytest.mark.parametrize(
    ("sanitizer", "vocabulary"),
    [
        ("sanitize_policy_triggered", "POLICY_TRIGGERED_ALLOWLIST"),
        ("sanitize_scheduler", "SCHEDULER_ALLOWLIST"),
        ("sanitize_scheduler_error_type", "SCHEDULER_ERROR_TYPE_VALUES"),
        ("sanitize_unsigned_reason", "UNSIGNED_REASONS"),
        ("sanitize_revocation_unavailable_reason", "REVOCATION_UNAVAILABLE_REASONS"),
    ],
)
def test_sanitizer_passes_its_vocabulary_and_collapses_the_rest(sanitizer, vocabulary):
    """Each vocabulary member passes through unchanged; anything else, and None, is ``"other"``."""
    from src.core import metrics

    sanitize = getattr(metrics, sanitizer)
    for known in getattr(metrics, vocabulary):
        assert sanitize(known) == known
    assert sanitize("not_in_the_vocabulary") == "other"
    assert sanitize(None) == "other"
