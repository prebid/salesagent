"""Acceptance: on a wire transport, an error assertion cannot pass without wire bytes.

This is the lane's Core Invariant expressed as an executable property rather than
a code review: for every error-path Then step, delete the captured wire and the
step must REDDEN. A step that still goes green is grading a test-side
reconstruction — the synthesized envelope the harness built from the caught
exception, or the reconstructed ``ctx["error"]`` — and would keep passing through
a total regression of the production boundary translator, because both sides of
that comparison move together.

The mutation is applied to the ctx, not to production: each case hands the step
exactly what a wire transport that captured NOTHING would leave behind
(``synthesized_error_envelope`` and/or a reconstructed ``ctx["error"]``, with
``wire_error_envelope`` absent), and requires an ``AssertionError``. Every case
is paired with a positive control carrying real wire bytes, so "raises always"
cannot satisfy the grader either.

Scope note: ``Transport.IMPL`` is sunsetted for BDD (tests/CLAUDE.md — every BDD
run is a real wire run), which is what makes "no envelope captured" a wiring
defect to surface rather than a legitimate no-wire branch to fall back through.
"""

from __future__ import annotations

import pytest

from tests.harness.transport import Transport
from tests.harness.wire_fixtures import wire_error_result


def _two_layer(
    code: str,
    message: str,
    *,
    recovery: str = "correctable",
    details: dict | None = None,
    field: str | None = None,
    issues: list[dict] | None = None,
) -> dict:
    error: dict = {"code": code, "message": message, "recovery": recovery}
    if details is not None:
        error["details"] = details
    if field is not None:
        error["field"] = field
    if issues is not None:
        error["issues"] = issues
    return {"adcp_error": dict(error), "errors": [dict(error)]}


_AUTH_MESSAGE = "Authorization header was present but token verification failed"
_VERSION_DETAILS = {"supported_versions": ["3.0", "3.1"], "build_version": "3.1.1"}


def _no_wire_ctx(envelope: dict) -> dict:
    """What a wire dispatch that captured NOTHING leaves in ctx.

    ``dispatch_request`` is the one writer: on a transport that produced no
    capturable envelope it leaves ``ctx["wire_error_envelope"]`` unset. The ctx
    also used to carry a ``synthesized_error_envelope`` -- an envelope the harness
    rebuilt from the caught exception -- which is what every fallback branch under
    grading here used to accept. That key is deleted along with the no-wire
    pseudo-transport, so the negative control is now simply "no wire, and nothing
    standing in for one". The ``error`` entry stays: a fallback onto the raised
    exception is still a way to pass without wire bytes, and still must redden.
    """
    return {
        "transport": Transport.REST,
        # has_wire=False: nothing came back. The production analogue is
        # client.py's ``unwrap_rest_error`` — a REST DELIVER exception, no HTTP
        # body at all. False is also the READER's most permissive branch (the
        # only one on which a synthesized envelope may stand in for a wire), so
        # a step that still reddens here reddens on genuine absence rather than
        # because the fallback was structurally closed off.
        "result": wire_error_result(None),
        "error": RuntimeError(envelope["errors"][0]["message"]),
    }


def _wire_ctx(envelope: dict) -> dict:
    """The positive control: the same dispatch, with real wire bytes captured."""
    return {
        "transport": Transport.REST,
        "wire_error_envelope": envelope,
        # has_wire=True: the positive control IS the captured-wire case — the
        # envelope below is what ``unwrap_rest_response`` recovered from a real
        # >= 400 HTTP body.
        "result": wire_error_result(envelope),
    }


#: The generic error-path primitives, each with the envelope it needs to be
#: satisfiable and the arguments a scenario would pass it.
#:
#: One row per primitive is the POINT, not bookkeeping: this file's invariant is
#: "for every error-path Then step, delete the wire and it must redden", so a
#: primitive missing from this table is a step nothing holds to the invariant.
#: When ``then_error.py`` grows a primitive that reads the wire envelope, add it
#: here — that is the whole maintenance contract.
_WIRE_ENVELOPE_PRIMITIVES = [
    pytest.param("then_response_arrives", (), {}, id="arrives"),
    pytest.param("then_response_error_code", ("AUTH_INVALID",), {}, id="code"),
    pytest.param("then_response_error_field", ("billing",), {"field": "billing"}, id="field"),
    pytest.param(
        "then_response_error_issue",
        ("enum", "billing"),
        {"field": "billing", "issues": [{"keyword": "enum", "pointer": "/accounts/0/Accounts/billing"}]},
        id="issue",
    ),
]


class TestGenericWireEnvelopeStepsRequireWireBytes:
    """Every generic error-path primitive in ``then_error.py`` needs real wire bytes.

    This covers FOUR primitives where it once covered two bundled steps. The two
    it named (``then_wire_envelope_code`` and its ``with recovery`` variant) were
    deleted when the bundled trio was decomposed (salesagent-prkv.65): they graded
    arrived+code and arrived+code+recovery, so the set had to be kept in step by
    hand. Repointing at the four orthogonal primitives is not a rename — it widens
    the invariant to every step that reads the envelope, which is what this file's
    module docstring already claimed to enforce.

    ``recovery`` has no primitive of its own by design, so none appears here:
    ``assert_wire_error`` defaults it from the pinned CODE_TABLE, so a scenario
    naming it would restate what the code already determines.
    """

    @pytest.mark.parametrize(("step_name", "args", "envelope_kwargs"), _WIRE_ENVELOPE_PRIMITIVES)
    def test_primitive_reddens_without_wire_bytes(self, step_name, args, envelope_kwargs):
        """The negative control: delete the captured wire, the step must fail."""
        from tests.bdd.steps.generic import then_error

        step = getattr(then_error, step_name)
        ctx = _no_wire_ctx(_two_layer("AUTH_INVALID", _AUTH_MESSAGE, recovery="terminal", **envelope_kwargs))
        with pytest.raises(AssertionError):
            step(ctx, *args)

    @pytest.mark.parametrize(("step_name", "args", "envelope_kwargs"), _WIRE_ENVELOPE_PRIMITIVES)
    def test_primitive_passes_on_real_wire_bytes(self, step_name, args, envelope_kwargs):
        """The positive control, so "raises always" cannot satisfy the grader."""
        from tests.bdd.steps.generic import then_error

        step = getattr(then_error, step_name)
        ctx = _wire_ctx(_two_layer("AUTH_INVALID", _AUTH_MESSAGE, recovery="terminal", **envelope_kwargs))
        step(ctx, *args)


class TestCapabilitiesDetailStepsRequireWireBytes:
    """``uc010``'s version-negotiation details oracles (the ``_error_details`` consumers)."""

    def _envelope(self) -> dict:
        return _two_layer("VERSION_UNSUPPORTED", "adcp 2.9 is not supported", details=_VERSION_DETAILS)

    def test_supported_versions_step_reddens_without_wire_bytes(self):
        from tests.bdd.steps.domain.uc010_capabilities import then_details_supported_versions

        with pytest.raises(AssertionError):
            then_details_supported_versions(_no_wire_ctx(self._envelope()))

    def test_supported_versions_step_passes_on_real_wire_bytes(self):
        from tests.bdd.steps.domain.uc010_capabilities import then_details_supported_versions

        then_details_supported_versions(_wire_ctx(self._envelope()))

    def test_build_version_step_reddens_without_wire_bytes(self):
        from tests.bdd.steps.domain.uc010_capabilities import then_details_build_version

        with pytest.raises(AssertionError):
            then_details_build_version(_no_wire_ctx(self._envelope()), "3.1.1")

    def test_build_version_step_passes_on_real_wire_bytes(self):
        from tests.bdd.steps.domain.uc010_capabilities import then_details_build_version

        then_details_build_version(_wire_ctx(self._envelope()), "3.1.1")

    def test_version_unsupported_oracle_reddens_without_wire_bytes(self):
        from tests.bdd.steps.domain.uc010_capabilities import then_version_details_supported_versions

        with pytest.raises(AssertionError):
            then_version_details_supported_versions(_no_wire_ctx(self._envelope()))

    def test_version_unsupported_oracle_passes_on_real_wire_bytes(self):
        from tests.bdd.steps.domain.uc010_capabilities import then_version_details_supported_versions

        then_version_details_supported_versions(_wire_ctx(self._envelope()))


class TestPackageOutcomeDispatchRequiresWireBytes:
    """``uc026.then_outcome`` — the conjunct that routes AROUND the wire assertion.

    ``is_pinned_error_code(code) and result.wire_error_envelope is not None`` means
    a missing envelope silently downgrades the row to a reconstructed-exception
    check. For a PINNED code there is no legitimate no-wire branch left (IMPL is
    sunsetted for BDD), so the conjunct is what makes the wire optional.
    """

    def _reconstructed_ctx(self, error: dict) -> dict:
        return {
            "transport": Transport.REST,
            # has_wire=False, as in ``_no_wire_ctx``: nothing was captured, and
            # nothing (no synthesized envelope) stands in for one — the state a
            # reconstructed ctx["error"] is all that survives from.
            "result": wire_error_result(None),
            "error": error,
        }

    def test_pinned_code_reddens_when_no_envelope_was_captured(self):
        from tests.bdd.steps.domain.uc026_package_media_buy import then_outcome

        ctx = self._reconstructed_ctx({"code": "VALIDATION_ERROR", "message": "budget must be positive"})
        with pytest.raises(AssertionError):
            then_outcome(ctx, 'error "VALIDATION_ERROR"')

    def test_pinned_code_passes_on_real_wire_bytes(self):
        from tests.bdd.steps.domain.uc026_package_media_buy import then_outcome

        ctx = _wire_ctx(_two_layer("VALIDATION_ERROR", "budget must be positive"))
        then_outcome(ctx, 'error "VALIDATION_ERROR"')

    def test_recovery_does_not_stand_in_for_a_suggestion(self):
        """``suggestion`` and ``recovery`` are distinct error.json fields.

        Falling back to ``recovery`` makes "with suggestion" pass on any error
        carrying a recovery hint — which, being pin-defaulted, is all of them.
        """
        from tests.bdd.steps.domain.uc026_package_media_buy import then_outcome

        ctx = self._reconstructed_ctx(
            {"code": "VALIDATION_ERROR", "message": "budget must be positive", "recovery": "correctable"}
        )
        with pytest.raises(AssertionError):
            then_outcome(ctx, 'error "VALIDATION_ERROR" with suggestion')

    def test_a_real_suggestion_on_the_wire_satisfies_with_suggestion(self):
        from tests.bdd.steps.domain.uc026_package_media_buy import then_outcome

        envelope = _two_layer("VALIDATION_ERROR", "budget must be positive")
        for layer in (envelope["adcp_error"], envelope["errors"][0]):
            layer["suggestion"] = "review error details and fix field values"
        then_outcome(_wire_ctx(envelope), 'error "VALIDATION_ERROR" with suggestion')
