"""Guard: the dormancy-vs-production-gap tripwire (tests/bdd/conftest.py, #1721 M4)
must actually flip a MISCLASSIFIED strict-xfail to FAILED, and must leave alone
every xfail that is not a misclassification.

R1-2 (six independent reviewers converged on this): T-UC-010-main's strict-xfail
reason claimed a graded "production gap" (account.sandbox), but the scenario
never reached that assert -- it failed on a missing Given-side write, a pure
test-wiring gap. The tripwire this guard covers (``_classify_strict_xfail_dormancy``,
fed by ``pytest_bdd_step_func_lookup_error`` / ``pytest_bdd_step_error``) is the
mechanism added so this defect class fails loud at write time instead of six
reviewer passes later. This meta-test drives the real functions directly with
minimal stand-ins (same technique as test_guards_bdd_strict_xfail_representative.py).
"""

from __future__ import annotations

import pytest

from tests.bdd.conftest import (
    _STEP_ERROR_CLASSIFICATION,
    _classify_strict_xfail_dormancy,
    pytest_bdd_step_error,
    pytest_bdd_step_func_lookup_error,
)


class _FakeStep:
    def __init__(self, step_type: str, name: str, line_number: int = 1):
        self.type = step_type
        self.name = name
        self.line_number = line_number


class _FakeRequest:
    def __init__(self, nodeid: str):
        self.node = _FakeItem(nodeid)


class _FakeMark:
    def __init__(self, reason: str):
        self.kwargs = {"reason": reason}


class _FakeItem:
    def __init__(self, nodeid: str, xfail_reason: str | None = None):
        self.nodeid = nodeid
        self._xfail_reason = xfail_reason

    def iter_markers(self, name=None):
        if name == "xfail" and self._xfail_reason is not None:
            yield _FakeMark(self._xfail_reason)


class _FakeReport:
    def __init__(self, outcome: str, wasxfail: str | None = None):
        self.outcome = outcome
        self.wasxfail = wasxfail
        self.longrepr = None


@pytest.fixture(autouse=True)
def _clean_classification_state():
    """The module-level dict is shared/global state -- isolate each test."""
    _STEP_ERROR_CLASSIFICATION.clear()
    yield
    _STEP_ERROR_CLASSIFICATION.clear()


class TestPositiveMisclassificationDetected:
    """A strict-xfail claiming a graded gap, but whose real cause is dormancy,
    MUST flip to failed with a MISCLASSIFIED longrepr."""

    def test_missing_step_definition_flips_a_production_gap_claim_to_failed(self):
        item = _FakeItem("test_fake.py::test_x[a2a]", xfail_reason="account.sandbox — production gap")
        report = _FakeReport(outcome="skipped", wasxfail="Step definition not found: ...")
        _STEP_ERROR_CLASSIFICATION[item.nodeid] = "a missing step definition for given 'foo' (line 12)"

        _classify_strict_xfail_dormancy(item, report)

        assert report.outcome == "failed"
        assert "MISCLASSIFIED" in report.longrepr
        assert "missing step definition" in report.longrepr

    def test_given_side_setup_error_flips_a_spec_production_gap_claim_to_failed(self):
        """Covers the OTHER accepted phrasing ('spec-production') -- a reason
        using only this wording must not slip past the check."""
        item = _FakeItem("test_fake.py::test_y[mcp]", xfail_reason="UC-019 spec-production gap")
        report = _FakeReport(outcome="failed")
        _STEP_ERROR_CLASSIFICATION[item.nodeid] = "a Given-side setup error on 'bar' (line 30): KeyError('x')"

        _classify_strict_xfail_dormancy(item, report)

        assert report.outcome == "failed"
        assert "MISCLASSIFIED" in report.longrepr

    def test_case_insensitive_production_gap_phrasing_is_still_caught(self):
        """Would-be-missed case: a reason spelled 'Production Gap' (mixed case)
        must not evade the substring check (r.lower() is what prevents this)."""
        item = _FakeItem("test_fake.py::test_z[rest]", xfail_reason="This is a Production Gap, not a bug")
        report = _FakeReport(outcome="skipped")
        _STEP_ERROR_CLASSIFICATION[item.nodeid] = "a missing step definition for given 'baz' (line 5)"

        _classify_strict_xfail_dormancy(item, report)

        assert report.outcome == "failed"
        assert "MISCLASSIFIED" in report.longrepr


class TestNegativeLeavesHonestOrRealFailuresAlone:
    """Anything that is NOT a misclassified dormancy must be left untouched."""

    def test_no_classification_recorded_leaves_a_real_then_failure_alone(self):
        """A genuine When/Then grading failure never populates
        _STEP_ERROR_CLASSIFICATION -- the report must pass through unchanged."""
        item = _FakeItem("test_fake.py::test_real[a2a]", xfail_reason="account.sandbox — production gap")
        report = _FakeReport(outcome="failed")

        _classify_strict_xfail_dormancy(item, report)

        assert report.outcome == "failed"
        assert report.longrepr is None

    def test_honest_dormancy_reason_is_left_alone(self):
        """A reason that already names itself as test-harness/wiring dormancy
        (no 'production gap' / 'spec-production' claim) is honest -- untouched."""
        item = _FakeItem(
            "test_fake.py::test_honest[a2a]",
            xfail_reason="UC-019 test-harness gap: MediaBuyListEnv wires no adapter mock",
        )
        report = _FakeReport(outcome="skipped")
        _STEP_ERROR_CLASSIFICATION[item.nodeid] = "a missing step definition for given 'qux' (line 9)"

        _classify_strict_xfail_dormancy(item, report)

        assert report.outcome == "skipped"
        assert report.longrepr is None

    def test_passed_outcome_is_never_flipped(self):
        """Defensive: even if classification + reason both match, only
        skipped/failed outcomes are eligible -- a passed report is never touched."""
        item = _FakeItem("test_fake.py::test_passed[a2a]", xfail_reason="production gap")
        report = _FakeReport(outcome="passed")
        _STEP_ERROR_CLASSIFICATION[item.nodeid] = "a missing step definition for given 'x' (line 1)"

        _classify_strict_xfail_dormancy(item, report)

        assert report.outcome == "passed"
        assert report.longrepr is None


class TestStepErrorHooksClassifyCorrectly:
    """pytest_bdd_step_func_lookup_error / pytest_bdd_step_error must populate
    _STEP_ERROR_CLASSIFICATION with the right cause, and only Given failures
    (never When/Then) are recorded by the second hook."""

    def test_lookup_error_records_missing_step_definition(self):
        request = _FakeRequest("test_fake.py::test_a[a2a]")
        step = _FakeStep("given", "some precondition", line_number=42)

        pytest_bdd_step_func_lookup_error(request, None, None, step, Exception("boom"))

        assert "missing step definition" in _STEP_ERROR_CLASSIFICATION[request.node.nodeid]
        assert "42" in _STEP_ERROR_CLASSIFICATION[request.node.nodeid]

    def test_step_error_records_given_side_failure(self):
        request = _FakeRequest("test_fake.py::test_b[a2a]")
        step = _FakeStep("given", "some setup", line_number=7)

        pytest_bdd_step_error(request, None, None, step, None, None, KeyError("missing"))

        assert "Given-side setup error" in _STEP_ERROR_CLASSIFICATION[request.node.nodeid]

    def test_step_error_ignores_then_failures(self):
        """A Then failure is the scenario grading its own behavior -- never
        dormancy. Must NOT populate the classification dict."""
        request = _FakeRequest("test_fake.py::test_c[a2a]")
        step = _FakeStep("then", "the response should equal X", line_number=99)

        pytest_bdd_step_error(request, None, None, step, None, None, AssertionError("nope"))

        assert request.node.nodeid not in _STEP_ERROR_CLASSIFICATION

    def test_step_error_ignores_when_failures(self):
        request = _FakeRequest("test_fake.py::test_d[a2a]")
        step = _FakeStep("when", "the buyer sends the request", line_number=55)

        pytest_bdd_step_error(request, None, None, step, None, None, RuntimeError("nope"))

        assert request.node.nodeid not in _STEP_ERROR_CLASSIFICATION
