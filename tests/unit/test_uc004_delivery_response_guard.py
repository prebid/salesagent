"""Regression tests for salesagent-pqb5 (review finding HIGH-02).

UC-004 delivery Then steps must access the response via ``ctx.get("response")``
guarded by an assertion, so that a missing response produces a diagnostic
``AssertionError`` rather than a bare ``KeyError`` from ``ctx["response"]``.

These call the real step functions with a response-less ``ctx`` and assert the
diagnostic ``AssertionError`` is raised. Before the fix the steps raised
``KeyError`` (uncaught by ``pytest.raises(AssertionError)``), so the test failed.
"""

from __future__ import annotations

import pytest

from tests.bdd.steps.domain import uc004_delivery

# Step functions whose first ctx operation is the response access. Each must
# surface a missing response as a diagnostic AssertionError.
RESPONSE_FIRST_STEPS = [
    uc004_delivery.then_has_metrics,
    uc004_delivery.then_has_packages,
]


@pytest.mark.parametrize("step", RESPONSE_FIRST_STEPS, ids=lambda s: s.__name__)
def test_missing_response_raises_assertion_not_keyerror(step):
    with pytest.raises(AssertionError):
        step({})
