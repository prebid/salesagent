"""Regression tests for salesagent-quwd (review finding MED-04 / TEST-01).

``_assert_tasks_sorted`` must fail loudly when the outcome string names a sort
field that maps to no known task attribute. Previously it silently ``return``ed,
so a new Gherkin Examples outcome could pass with zero sorting verification.

These call the real helper with 2+ tasks (so the len<2 short-circuit does not
apply) and an outcome whose sort field is unmapped, asserting ``ValueError``.
Before the fix the helper returned ``None`` and no error was raised.
"""

from __future__ import annotations

import pytest

from tests.bdd.steps.domain.uc002_create_media_buy import (
    _assert_multi_value_filter,
    _assert_tasks_filtered,
    _assert_tasks_sorted,
)


def test_unmapped_sort_field_raises_valueerror():
    tasks = [{"created_at": 1}, {"created_at": 2}]
    with pytest.raises(ValueError):
        _assert_tasks_sorted(tasks, "tasks sorted by nonexistent attribute")


# ── salesagent-xoa0: _assert_tasks_filtered / _assert_multi_value_filter ──
# Same silent-skip-on-unmapped class as _assert_tasks_sorted: an outcome that
# matches no filter mapping must raise, not pass with zero verification.


def test_unmapped_filter_outcome_raises_valueerror():
    # "bogus domain" contains "domain" but is not in _FILTER_MAP, so the old
    # code's task_type branch (which excludes domain/status) skipped it silently.
    tasks = [{"domain": "media_buy"}]
    with pytest.raises(ValueError):
        _assert_tasks_filtered(tasks, "tasks filtered to bogus domain")


def test_empty_filter_outcome_raises_valueerror():
    # An empty filter value names no filter at all — must not pass silently.
    tasks = [{"task_type": "x"}]
    with pytest.raises(ValueError):
        _assert_tasks_filtered(tasks, "tasks filtered to ")


def test_mapped_filter_outcome_still_validates():
    # A known _FILTER_MAP entry on matching tasks must NOT raise.
    tasks = [{"domain": "media_buy"}, {"domain": "media_buy"}]
    _assert_tasks_filtered(tasks, "tasks filtered to media-buy domain")


def test_task_type_filter_outcome_still_validates():
    # A bare task_type value (not domain/status) on matching tasks must NOT raise.
    tasks = [{"task_type": "creative_review"}]
    _assert_tasks_filtered(tasks, "tasks filtered to creative_review")


def test_unmapped_multi_value_filter_raises_valueerror():
    # "multiple" suffix naming none of domain/status/type silently skipped before.
    tasks = [{"domain": "media_buy"}, {"domain": "signals"}]
    with pytest.raises(ValueError):
        _assert_multi_value_filter(tasks, "multiple bogus values")


def test_multi_value_filter_empty_tasks_short_circuits():
    # No tasks → legitimate early return even for an unmapped suffix.
    _assert_multi_value_filter([], "multiple bogus values")


def test_multi_value_filter_mapped_still_validates():
    # A valid "multiple domains" spanning 2+ domains must NOT raise.
    _assert_multi_value_filter([{"domain": "media_buy"}, {"domain": "signals"}], "multiple domains")


def test_mapped_sort_field_still_validates_order():
    # A known sort field ("creation timestamp" -> created_at) in correct order
    # must NOT raise — guards against the fix over-raising on valid input.
    tasks = [{"created_at": 1}, {"created_at": 2}]
    _assert_tasks_sorted(tasks, "tasks sorted by creation timestamp")


def test_fewer_than_two_tasks_short_circuits():
    # Insufficient data to verify ordering is a legitimate early return, even
    # with an unmapped field — must not raise.
    _assert_tasks_sorted([{"created_at": 1}], "tasks sorted by nonexistent attribute")
