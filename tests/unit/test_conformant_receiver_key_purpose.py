"""The conformant-receiver shim accepts the pinned spec's WHOLE key-purpose set — and nothing wider.

WHAT THIS GRADES, precisely: a TEST HELPER —
``tests/helpers/signing.py::verify_as_conformant_receiver`` — over JWKs this module
FABRICATES by choosing their ``adcp_use``. It grades no production code, and it cannot:
production only ever publishes ``request-signing`` keys, so no production path can reach
the deprecated arm at all. That is exactly why the obligation needs its own grader — the
accept-set breadth is a property of the RECEIVER we simulate, and the only receiver in
this repo is that helper. A sender-side test can never observe it.

A module of its own, on purpose. The neighbouring pin
``tests/integration/test_notification_proof_challenge.py``
``::TestChallengeIsSignedAndVerifiable::test_the_sdk_webhook_verifier_diverges_from_the_pin``
is BUILT TO BE DELETED — the day adcp-client-python#1018 lands, the SDK's
``expected_adcp_use`` inversion is gone, that test fails, and it goes out along with the
substitution it pins. The breadth asserted below outlives that deletion: it is the
spec's standing requirement on any conformant receiver, not a note about a broken SDK.
Bolting it onto a test designed for deletion would take it out with the tide.

Spec, at the version this repo pins (AdCP 3.1.1 via ``adcp==6.6.0``) — from
``docs/building/by-layer/L1/security.mdx`` at tag ``v3.1.1``:

* **step 8 (:1478)** — *"Verify the JWK's ``use`` is ``"sig"``, ``key_ops`` includes
  ``"verify"``, and ``adcp_use`` is ``"request-signing"`` … the deprecated
  ``"webhook-signing"`` value MUST also be accepted for backward compatibility. Reject
  on any other outcome with ``webhook_signature_key_purpose_invalid``."*
* **taxonomy row (:1560)** — *"JWK ``adcp_use`` not-in {webhook-signing, request-signing},
  absent, or ``key_ops`` lacks verify | ``webhook_signature_key_purpose_invalid``"*.
* **:1438** — ``"webhook-signing"`` is DEPRECATED; *"Verifiers MUST still accept it …
  new signers SHOULD publish and sign with ``"request-signing"`` keys only"*.

Two-sided, because both sides are real defects and each has a live instance. Too NARROW
is today's shim (and the SDK's own ``verify_webhook_signature``): it rejects a sender the
spec says is conformant. Too WIDE — retrying on any failure, or accepting any
``adcp_use`` — makes the shim a WEAKER check than the SDK performs, which the shim's own
docstring forbids, and would let a real signing defect through every grader that calls it.

The rejection-side assertions also pin the two webhook-profile behaviours the shim
currently BYPASSES by calling the request verifier directly: the ``webhook_signature_*``
error taxonomy (the SDK's ``_retag_to_webhook``) and the step-6 component precheck
(``_precheck_webhook_has_required_components``). Both are part of "the whole checklist,
the tag pin, the required components" the docstring claims to run; today that claim is
false, and these are what make it true.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from adcp.signing.errors import (
    WEBHOOK_SIGNATURE_COMPONENTS_INCOMPLETE,
    WEBHOOK_SIGNATURE_KEY_PURPOSE_INVALID,
    WEBHOOK_SIGNATURE_REQUIRED,
    SignatureVerificationError,
)

from tests.helpers.signing import verify_as_conformant_receiver
from tests.helpers.webhook_wire import CapturedWebhook

#: A receiver URL with a real-looking TLD and an https scheme, so ``@target-uri`` and
#: ``@authority`` canonicalize to the values a socket would have carried.
_URL = "https://buyer.example.com/adcp/notifications"

#: One delivery body. Bytes, not a dict: ``content-digest`` covers exactly these bytes,
#: and re-serializing a dict here would grade a different message than the one signed.
_BODY = b'{"notification_type":"scheduled","sequence_number":1}'

_KID = "seller-webhook-1"
_ALG = "ed25519"


def _sign_one_webhook(*, published_adcp_use: str, published_alg: str = _ALG) -> tuple[CapturedWebhook, dict[str, Any]]:
    """One correctly signed webhook, plus the JWKS a receiver would resolve it against.

    The SIGNATURE is always honest — real Ed25519 over the real bytes, through the SDK's
    own ``sign_webhook`` so the tag, the covered components and the content-digest are
    the ones a conformant sender emits. Only the PUBLISHED document is fabricated, and
    only in ``adcp_use``. That separation is what makes every outcome below attributable
    to the key-purpose decision rather than to anything about the signature itself.

    ``published_alg`` exists for the one case that needs the JWK to describe a DIFFERENT
    key type than the signature declares — the step-8 failure that is not a purpose
    mismatch.
    """
    from adcp.signing import generate_signing_keypair, load_private_key_pem
    from adcp.signing.webhook_signer import sign_webhook

    pem, public_jwk = generate_signing_keypair(alg=_ALG, kid=_KID, purpose="request-signing")
    unsigned_headers = {"Content-Type": "application/json"}
    signed = sign_webhook(
        method="POST",
        url=_URL,
        headers=unsigned_headers,
        body=_BODY,
        private_key=load_private_key_pem(pem),
        key_id=_KID,
        alg=_ALG,
    )
    captured = CapturedWebhook(
        url=_URL,
        headers=httpx.Headers({**unsigned_headers, **signed.as_dict()}),
        content=_BODY,
    )

    published = {**public_jwk, "adcp_use": published_adcp_use}
    if published_alg != _ALG:
        _, other_jwk = generate_signing_keypair(alg=published_alg, kid=_KID, purpose="request-signing")
        published = {**other_jwk, "adcp_use": published_adcp_use}
    return captured, {"keys": [published]}


def _rejection(captured: CapturedWebhook, jwks: dict[str, Any]) -> SignatureVerificationError:
    """Verify *captured* expecting refusal, and hand back the refusal itself."""
    with pytest.raises(SignatureVerificationError) as raised:
        verify_as_conformant_receiver(captured, jwks)
    return raised.value


def _without_component(captured: CapturedWebhook, component: str) -> CapturedWebhook:
    """The same webhook with *component* struck from the covered-component list.

    A fabrication, and it necessarily invalidates the signature — which is the point:
    the webhook profile refuses this shape at step 6, BEFORE any crypto runs, so a
    receiver that reaches a signature-verification failure here is running the request
    profile's weaker component rule rather than the webhook profile's.
    """
    header = captured.headers["signature-input"]
    stripped = header.replace(f'"{component}" ', "", 1)
    assert stripped != header, f"{component!r} was not in the covered components to begin with: {header}"
    return CapturedWebhook(
        url=captured.url,
        headers=httpx.Headers({**dict(captured.headers), "Signature-Input": stripped}),
        content=captured.content,
    )


class TestKeyPurposeAcceptSet:
    """security.mdx @ v3.1.1 :1478 / :1560 — the accept-set is {request-signing, webhook-signing}."""

    def test_the_spec_mandated_request_signing_purpose_is_accepted(self):
        """The value :1426 mandates for new signers verifies. The other half of two-sided.

        Without this, "widen the accept-set" could be satisfied by swapping one narrow
        pin for another — which is the SDK's exact bug, mirrored.
        """
        captured, jwks = _sign_one_webhook(published_adcp_use="request-signing")

        verified = verify_as_conformant_receiver(captured, jwks)

        assert verified.key_id == _KID, (
            f"the verifier returned key_id {verified.key_id!r}; a conformant request-signing key "
            f"must verify and be attributed to {_KID!r}, or nothing was actually verified"
        )

    def test_the_deprecated_webhook_signing_purpose_is_also_accepted(self):
        """:1438/:1478 — DEPRECATED is not REMOVED: verifiers MUST still accept it.

        A sender that published its key before :1438 deprecated the value is conformant
        and stays conformant; a receiver that rejects it is the non-conformant party.
        """
        captured, jwks = _sign_one_webhook(published_adcp_use="webhook-signing")

        verified = verify_as_conformant_receiver(captured, jwks)

        assert verified.key_id == _KID, (
            f"the verifier returned key_id {verified.key_id!r}; the deprecated but still-accepted "
            f"webhook-signing purpose must verify and be attributed to {_KID!r}"
        )

    def test_a_purpose_outside_the_accept_set_is_still_rejected(self):
        """:1560 — not-in {webhook-signing, request-signing} rejects. Widening has a floor.

        ``"encryption"`` is a plausible JWK-registry value that is not a signing purpose
        at all, so accepting it would mean the accept-set had become "any string".
        """
        captured, jwks = _sign_one_webhook(published_adcp_use="encryption")

        error = _rejection(captured, jwks)

        assert error.code == WEBHOOK_SIGNATURE_KEY_PURPOSE_INVALID, (
            f"an out-of-set adcp_use must be refused with {WEBHOOK_SIGNATURE_KEY_PURPOSE_INVALID!r} "
            f"(:1560 taxonomy row), got {error.code!r} — a request-family code here means the "
            "webhook profile's error taxonomy is not being applied"
        )

    def test_an_absent_purpose_is_rejected(self):
        """:1560 names ABSENT alongside out-of-set. An unmarked key is not a webhook key.

        Distinct from the case above because a widening implemented as "accept anything
        we do not recognise" passes that one by accident and this one by design.
        """
        captured, jwks = _sign_one_webhook(published_adcp_use="request-signing")
        jwks["keys"][0].pop("adcp_use")

        error = _rejection(captured, jwks)

        assert error.code == WEBHOOK_SIGNATURE_KEY_PURPOSE_INVALID, (
            f"a JWK with no adcp_use claim at all must be refused with "
            f"{WEBHOOK_SIGNATURE_KEY_PURPOSE_INVALID!r} (:1560, 'absent'), got {error.code!r}"
        )


class TestRejectionsAreNotMisattributed:
    """A widened accept-set must not blur WHY a key was refused.

    ``request_signature_key_purpose_invalid`` covers FOUR distinct step-8 conditions
    (``adcp.signing.verifier._check_key_purpose``): ``use`` != sig, ``key_ops`` lacking
    verify, the ``adcp_use`` mismatch, and an algorithm that cannot be derived from — or
    does not match — the JWK. Only the third is the one this widening is about. A retry
    triggered by the CODE alone re-runs all four under the second purpose and reports
    whatever the second attempt hit, so a genuine algorithm defect comes back described
    as a purpose mismatch: a receiver operator debugging their key would be sent to the
    wrong line of their JWKS.
    """

    def test_an_algorithm_mismatch_is_not_reported_as_a_purpose_mismatch(self):
        """Conformant ``adcp_use``, wrong key type — the message must name the algorithm.

        This JWK passes the ``adcp_use`` check on the first attempt and fails the alg
        check after it, which is precisely the shape that a code-only retry converts
        into an ``adcp_use`` complaint on the second attempt.
        """
        captured, jwks = _sign_one_webhook(published_adcp_use="request-signing", published_alg="es256")

        error = _rejection(captured, jwks)

        assert "adcp_use" not in str(error), (
            "the refusal must describe the ALGORITHM problem this JWK actually has; reporting "
            f"an adcp_use mismatch means the second retry attempt's error was surfaced: {error}"
        )
        assert "alg" in str(error), f"the refusal must name the algorithm mismatch it is: {error}"
        assert error.code == WEBHOOK_SIGNATURE_KEY_PURPOSE_INVALID, (
            f"step-8 refusals carry the webhook taxonomy's key-purpose code, got {error.code!r}"
        )


class TestTheWebhookProfileChecksAreActuallyRun:
    """The shim's docstring promises "the whole checklist … the required components".

    It reaches the SDK's webhook profile through the REQUEST verifier, so two things
    ``verify_webhook_signature`` does are skipped today: the step-6 component precheck
    and the request→webhook error retag. Both are observable, so both are graded here
    rather than left as a docstring claim.
    """

    def test_a_signature_not_covering_content_type_is_refused_at_step_6(self):
        """``_precheck_webhook_has_required_components`` — content-type coverage is not optional.

        The core verifier requires ``@method``/``@target-uri``/``@authority``
        unconditionally and treats ``content-type`` as cover-if-present; the webhook
        profile escalates it to REQUIRED. A receiver running only the core rule accepts
        a delivery whose declared content type is unprotected — the receiver parses the
        body as whatever an attacker relabelled it.
        """
        captured, jwks = _sign_one_webhook(published_adcp_use="request-signing")

        error = _rejection(_without_component(captured, "content-type"), jwks)

        assert error.code == WEBHOOK_SIGNATURE_COMPONENTS_INCOMPLETE, (
            f"an uncovered content-type must be refused with {WEBHOOK_SIGNATURE_COMPONENTS_INCOMPLETE!r} "
            f"at the profile's step 6, got {error.code!r} — reaching a later signature-verification "
            "failure instead means the precheck never ran"
        )
        assert error.step == 6, (
            f"the precheck is step 6 of the webhook checklist; this refusal reports step {error.step!r}, "
            "so it came from a later stage that happened to fail too"
        )

    def test_an_unsigned_delivery_is_refused_in_the_webhook_taxonomy(self):
        """``_retag_to_webhook`` — a webhook receiver never emits request-family codes.

        The SDK is explicit that the retag "guarantees webhook routes never leak
        request-signing error strings". Calling the request verifier directly leaks all
        of them, so the shim reports a code no conformant webhook receiver would.
        """
        captured, jwks = _sign_one_webhook(published_adcp_use="request-signing")
        unsigned = CapturedWebhook(
            url=captured.url,
            headers=httpx.Headers({"Content-Type": "application/json"}),
            content=captured.content,
        )

        error = _rejection(unsigned, jwks)

        assert error.code == WEBHOOK_SIGNATURE_REQUIRED, (
            f"an unsigned delivery must be refused with {WEBHOOK_SIGNATURE_REQUIRED!r}; got "
            f"{error.code!r}, a request-family code the webhook taxonomy retags away"
        )
