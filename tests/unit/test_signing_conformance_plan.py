"""CI guard: the L2 execution plan covers every vendored vector, mechanically.

#1291 B3 (``salesagent-z6nr.14``), design step 6. ``TRANSPLANT`` is OUR data about
how each conformance vector is driven against the real app. Two ways it can rot
silently, both of which produce a vacuous pass rather than a failure:

* a new upstream vector arrives and is never planned (so it never runs), or a stale
  row lingers for a vector that no longer exists;
* a hand-typed ``wire_url`` diverges from the mechanical transplant rule, so the
  request sent is not the request the row claims — and a canonicalization pathology
  (``:443``, ``/./``, ``%2F``, ``[2001:db8::1]``) is quietly normalised away.

Both are guarded here, in ``tests/unit`` (pure data, no app, no DB), so they fail at
``make quality`` rather than inside a 40-case integration run.
"""

from __future__ import annotations

import pytest

from tests.helpers.signing_vectors import (
    TRANSPLANT,
    Credential,
    Outcome,
    load_signing_vectors,
    transplant_url,
)

_VERBATIM = {"negative/028-unsigned-protocol-method-required"}


def test_every_vendored_vector_has_exactly_one_plan_row() -> None:
    """``TRANSPLANT.keys()`` equals the vendored vector file set, exactly."""
    planned = set(TRANSPLANT)
    vendored = set(load_signing_vectors())
    assert planned == vendored, (
        "The L2 execution plan drifted from the vendored vector set.\n"
        f"  vendored but UNPLANNED (would never run): {sorted(vendored - planned)}\n"
        f"  planned but not vendored (stale row): {sorted(planned - vendored)}"
    )


@pytest.mark.parametrize("vector_id", sorted(set(TRANSPLANT) - _VERBATIM))
def test_wire_url_matches_the_mechanical_rule(vector_id: str) -> None:
    """Each row's ``wire_url`` is what :func:`transplant_url` produces — no creativity."""
    vector = load_signing_vectors()[vector_id]
    assert TRANSPLANT[vector_id].wire_url == transplant_url(vector["request"]["url"]), (
        f"{vector_id}: the planned wire_url is not the mechanical transplant of "
        f"{vector['request']['url']!r}. Either the rule changed or the row has a typo — "
        "a typo here sends a different request than the row claims."
    )


@pytest.mark.parametrize("vector_id", sorted(_VERBATIM))
def test_verbatim_rows_send_the_vectors_own_url(vector_id: str) -> None:
    """``negative/028`` already targets a real surface (``POST /mcp``): no transplant."""
    assert TRANSPLANT[vector_id].wire_url == load_signing_vectors()[vector_id]["request"]["url"]


@pytest.mark.parametrize("vector_id", sorted(TRANSPLANT))
def test_planned_outcome_matches_the_vectors_own_expected_outcome(vector_id: str) -> None:
    """The plan never disagrees with the spec data about accept-vs-reject or the code."""
    plan = TRANSPLANT[vector_id]
    expected = load_signing_vectors()[vector_id]["expected_outcome"]
    if expected.get("success"):
        assert plan.outcome is Outcome.ACCEPTED and plan.expected_code is None
    else:
        assert plan.outcome is Outcome.REJECTED
        assert plan.expected_code == expected["error_code"], (
            f"{vector_id}: the plan grades {plan.expected_code!r} but the vector expects "
            f"{expected['error_code']!r} — the wire code is the graded artifact."
        )


@pytest.mark.parametrize("vector_id", sorted(TRANSPLANT))
def test_unsigned_vectors_are_the_only_ones_without_a_credential(vector_id: str) -> None:
    """Credential assignment is derived from the wire, not chosen per vector.

    Every SIGNED vector presents a principal token, because with no accepted
    credential the middleware hands the verifier an empty JWKS resolver and every
    signed vector short-circuits at step 7 — which would make ``negative/008``
    (which expects exactly ``request_signature_key_unknown``) pass for the wrong
    reason. The three UNSIGNED vectors present no credential the verifier accepts,
    because the composition rule they grade only fires on an unauthenticated caller.
    """
    plan = TRANSPLANT[vector_id]
    headers = load_signing_vectors()[vector_id]["request"]["headers"]
    # ``negative/019`` ships ``Signature`` with NO ``Signature-Input`` — still a
    # signature ATTEMPT, graded at step 1, so it belongs on the credentialed side.
    signed = "Signature-Input" in headers or "Signature" in headers
    expected = Credential.PRINCIPAL_TOKEN if signed else Credential.NONE
    assert plan.credential is expected, (
        f"{vector_id}: signed={signed} but planned credential is {plan.credential}. "
        "A mismatched credential column silently moves the graded step."
    )


@pytest.mark.parametrize("vector_id", sorted(TRANSPLANT))
def test_resigned_rows_use_the_vectors_own_keyid(vector_id: str) -> None:
    """A re-signed row signs with the key its own ``Signature-Input`` names.

    Signing ``positive/003`` (``keyid="test-es256-2026"``, ``alg=ecdsa-p256-sha256``)
    with the Ed25519 runner key produces a well-formed request that fails at step 10 —
    a red indistinguishable from a real crypto defect. This is the guard that tells the
    two apart, and it is why the ``resigned`` column is not free text.
    """
    from adcp.signing.canonical import parse_signature_input_header

    plan = TRANSPLANT[vector_id]
    if plan.resigned is None:
        return
    headers = {key.lower(): value for key, value in load_signing_vectors()[vector_id]["request"]["headers"].items()}
    keyid = parse_signature_input_header(headers["signature-input"])["sig1"].params["keyid"]
    assert plan.resigned == keyid, (
        f"{vector_id}: the plan re-signs with {plan.resigned!r} but the vector's Signature-Input names keyid {keyid!r}"
    )
