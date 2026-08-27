"""The harness's request-signature challenge vocabulary is production's own (lane L13, clause 3).

Core Invariant: *the set of codes the harness will let a test name in a
signature challenge is the set the verifier can emit — one derivation, shared
with production, never a second one that answers smaller.*

WHY THIS IS A UNIT TEST AND NOT A SCENARIO, stated rather than assumed. The
subject is :func:`tests.helpers.signing.request_signature_codes`, a HARNESS
vocabulary, and its one consumer is
:meth:`tests.harness.transport.TransportResult.assert_signature_challenge`
(``tests/harness/transport.py:314-321``), which refuses an unknown code UP FRONT
— before it reads the wire at all. So the behaviour is a veto the harness
exercises over a test, and no buyer-visible surface owns it: there is nothing to
drive through ``dispatch_request`` and nothing to read back off a wire. The
outer surface that owns this is the helper itself.

And the lane cannot substitute a wire test for it. ``_handle_signed`` calls
``_strict_header_precheck`` twenty-three lines above ``reject_malformed_target``,
both apply the same ``malformed_authority_reason`` rule, and ``_verify_url``
builds the authority FROM the ``Host`` header — so every authority the target
gate would refuse, the precheck refuses first with
``request_signature_header_malformed`` at checklist step 1. The middleware names
that pre-emption as deliberate. No wire refusal carrying
``request_target_uri_malformed`` exists to drive; the only residue is a request
with NO Host header and a malformed ``scope["server"]``, which no HTTP client
produces and which a test would have to hand-build — a detector, which this
epic's own correction bars.

Covers: salesagent-nx8jp.13 (clause 3 — the challenge vocabulary).
"""

from __future__ import annotations

import pytest

#: The code the verifier emits from ``reject_malformed_target``
#: (``src/core/signing/request_verifier_middleware.py``) and the one name the
#: ``REQUEST_SIGNATURE_``-prefix scan of ``adcp.signing.errors`` structurally
#: cannot see: the SDK has no constant whose NAME carries that prefix for it.
_TARGET_URI_MALFORMED = "request_target_uri_malformed"


def _sdk_prefix_scan() -> frozenset[str]:
    """Every ``REQUEST_SIGNATURE_*`` string constant in ``adcp.signing.errors``.

    An INDEPENDENT derivation, and that is the whole point of it being here. It
    is deliberately NOT the expression the helper under test evaluates: an
    assertion written as ``request_signature_codes() ==
    frozenset(REQUEST_TO_WEBHOOK_CODE)`` compares the function to its own body
    after the edit, so half of it is a tautology and it cannot see a shrinking
    ``REQUEST_TO_WEBHOOK_CODE`` — drop ten rows from that table and the harness
    veto returns for ten codes while such an assertion stays green. The lane's
    own words are the rule: "a grader holding its own copy of the thing under
    test is the defect this epic already found once", and an assertion derived
    from the implementation's own expression IS that copy.

    Used as a LOWER BOUND only. The claim below is a superset claim, so this
    scan growing (a new SDK code) tightens the test rather than breaking it.
    """
    from adcp.signing import errors as sdk_errors

    return frozenset(
        value
        for name, value in vars(sdk_errors).items()
        if name.startswith("REQUEST_SIGNATURE_") and isinstance(value, str)
    )


@pytest.mark.arch_guard
def test_the_challenge_vocabulary_admits_the_target_uri_code() -> None:
    """The harness admits ``request_target_uri_malformed``.

    Without it ``assert_signature_challenge`` refuses the code before reading the
    wire, so the harness vetoes a code production sends and no scenario can grade
    that refusal — a harness veto over production's own vocabulary.
    """
    from tests.helpers.signing import request_signature_codes

    codes = request_signature_codes()

    assert _TARGET_URI_MALFORMED in codes, (
        f"the harness refuses {_TARGET_URI_MALFORMED!r} up front in "
        "TransportResult.assert_signature_challenge, but the verifier emits it from "
        "reject_malformed_target (src/core/signing/request_verifier_middleware.py). A grader that "
        f"cannot name a code production sends grades nothing about it. Vocabulary was {sorted(codes)}"
    )


@pytest.mark.arch_guard
def test_the_challenge_vocabulary_covers_every_sdk_request_signature_code() -> None:
    """Every ``REQUEST_SIGNATURE_*`` value the SDK defines is still admitted.

    The other half of the superset claim, and what stops the first test from being
    satisfied by a vocabulary that ADDED one name while dropping others. Written
    against an independent scan of ``adcp.signing.errors`` rather than against the
    helper's own source expression, so it reddens both on a revert to the prefix
    scan (which loses the target-URI code above) and on a shrinking
    ``REQUEST_TO_WEBHOOK_CODE`` (which this one catches and the equality form
    cannot).
    """
    from tests.helpers.signing import request_signature_codes

    scanned = _sdk_prefix_scan()
    codes = request_signature_codes()

    # Non-vacuity, asserted BEFORE the containment: an empty scan makes every
    # superset claim below true for free, which is exactly the shape of assertion
    # this lane exists to remove.
    assert "request_signature_header_malformed" in scanned, (
        "the independent SDK scan yielded no known code, so the containment assertion below would "
        f"hold for want of evidence. Scan returned {sorted(scanned)}"
    )

    missing = scanned - codes
    assert not missing, (
        f"the harness vocabulary no longer admits {sorted(missing)} — codes adcp.signing.errors "
        "defines and the verifier can emit. assert_signature_challenge refuses an unknown code "
        "before it reads the wire, so every scenario naming one of these grades nothing"
    )
