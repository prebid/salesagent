"""C2 (#1291) — the proof-of-control challenge is SIGNED and CONFORMANT, or not sent.

Core Invariant under test: *the challenge POST asserts this seller's identity, so it
carries an RFC 9421 signature a conformant receiver can verify against the JWKS we
publish, over a body that receiver is allowed to accept — or it is not sent at all.*

## Why this level, and why it is the acceptance's locus rather than a sub-claim

``NotificationProofService.prove`` has ZERO real coverage today, and it is worth being
precise about how it got there, because two different mechanisms hide it:

* every in-process transport replaces the whole prover with a ``MagicMock`` whose
  ``prove`` is a dict lookup (``tests/harness/account_sync.py``), so production's method
  body never executes;
* on ``e2e_rest`` the real prover DOES run, but the harness realizes a proof outcome by
  pointing the subscriber at a reserved-TLD host, which
  ``notification_proof_service`` refuses BEFORE the POST.

So a unit test of `prove()` would grade a method nothing else reaches, and the BDD suite
grades the FAILURE direction only. This module therefore drives the real
``sync_accounts`` tool end to end with the REAL prover restored, against a real database
and a key minted through production, and replaces only two true externals: the SOCKET
(``tests.helpers.webhook_wire``) and DNS. That is the acceptance's locus because every
clause of the acceptance is observable there and nowhere else together:

* the emitted signature verifies against **the JWKS A3 publishes** — which needs a real
  tenant, a real ``signing_keys`` row and the real publication path, not a fixture JWK;
* the challenge BODY is what a conformant receiver validates, and the body is only built
  on this path (``_resolve_activation_proofs`` threads the strategy and the seller agent
  URL in — a direct ``prove()`` call would let the test supply both and grade nothing
  about how production obtains them);
* "not proven" has to surface as ``action: "failed"`` plus a ``VALIDATION_ERROR`` at
  ``notification_configs[0].url``, which is a ``sync_accounts`` response field.

The signature is graded through the SDK's own verifier rather than by re-deriving a
signature base here: a test that recomputed the signature would be asserting our
implementation against itself. ``WEBHOOK_TAG`` is imported for the same reason — a literal
profile string would pass for a tag we never actually emit. The SDK's webhook ENTRY POINT
diverges from the pin on one value, so verification runs through
:func:`tests.helpers.signing.verify_as_conformant_receiver`, which substitutes exactly that value and nothing
else; the divergence itself is pinned by its own test so the substitution cannot outlive
it.

## Spec, at the version this repo pins (AdCP 3.1.1 via ``adcp==6.6.0``)

``sync_accounts.mdx`` :207 — *"The challenge POST itself MUST be signed with the seller's
RFC 9421 webhook profile key even when the candidate config selects legacy delivery auth
… The receiver MUST verify the RFC 9421 signature and MUST reject the challenge unless
``seller_agent_url``, ``delivery_auth``, and ``event_types`` match the pending
registration."* :235 — the challenge value MUST be cryptographically random and
single-use, and a non-2xx, malformed, mismatched or timed-out challenge means proof
FAILED. ``dist/schemas/3.1.1/core/webhook-challenge.json`` — SEVEN required properties
and ``additionalProperties: false``; ``delivery_auth.credential_fingerprint`` is the
sha256 hex of the legacy credential, required for the legacy modes and forbidden for
``rfc9421``. No 3.1.1 conformance storyboard grades this surface
(``notification-config-lifecycle.yaml`` puts active registration out of scope), so these
assertions are the only grader that exists.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from typing import Any

import pytest
from adcp.signing.webhook_signer import WEBHOOK_TAG
from adcp.types.generated_poc.core.webhook_challenge import WebhookChallenge

from src.core.schemas.account import SyncAccountsRequest
from tests.harness.account_sync import AccountSyncEnv
from tests.helpers.signing import (
    deployment_kek,
    provision_key,
    signing_key_repo,
    verify_as_conformant_receiver,
)
from tests.helpers.webhook_wire import capture_outbound_webhooks, echoing_challenge_response, signature_input_params

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

#: The buyer's notification endpoint. A real (resolvable-looking) TLD, because
#: ``is_reserved_tld_host`` refuses ``.example`` BEFORE the POST — that refusal is the
#: e2e harness's deterministic lever and would make every assertion below vacuous.
_SUBSCRIBER_URL = "https://buyer.example.com/adcp/notifications"

#: The seller's own host, DOTTED so ``canonical_agent_url`` derives ``https://`` and the
#: published ``agents[].url`` this challenge names is one a receiver could resolve.
#: MIXED CASE on purpose. A receiver matches ``seller_agent_url`` byte-for-byte against our
#: published ``agents[].url`` (security.mdx @ v3.1.1 :1104 step 5, "no canonicalization at
#: this step"), and ``WebhookChallenge.seller_agent_url`` is a pydantic ``AnyUrl`` whose
#: serializer LOWERCASES the host. An all-lowercase fixture host cannot tell the published
#: string from the lowercased one, so it would grade nothing here.
_AGENT_HOST = "Seller-Proof.Example.com"

_SUBSCRIBER_ID = "buyer-subscriber-1"
#: An ACCOUNT-surface notification type. Both halves are load-bearing and were found by
#: probing rather than assumed: the enum is closed, and production separately refuses a
#: MEDIA-BUY-anchored type on the account surface with ``INVALID_REQUEST`` at
#: ``event_types[0]`` BEFORE any proof runs. A media-buy type here made every
#: ``action == "failed"`` assertion below pass for that unrelated reason.
_EVENT_TYPES = ["creative.status_changed"]


def _request(**config_overrides: Any) -> SyncAccountsRequest:
    """The typed request for one activating entry — built once, used by every case."""
    return SyncAccountsRequest(accounts=[_account_entry(**config_overrides)])


def _account_entry(**config_overrides: Any) -> dict[str, Any]:
    """One provisioning-mode account entry activating ONE notification subscriber.

    Provisioning mode (``brand`` + ``operator``, no ``account`` reference) on purpose:
    it is the mode where the account id does not exist until the write transaction, so
    it is the mode that grades whether the ``account_id`` in the challenge is the id the
    account is actually created with.
    """
    return {
        "brand": {"domain": "acme.com"},
        "operator": "acme.com",
        "billing": "operator",
        "notification_configs": [
            {
                "subscriber_id": _SUBSCRIBER_ID,
                "url": _SUBSCRIBER_URL,
                "event_types": list(_EVENT_TYPES),
                "active": True,
                **config_overrides,
            }
        ],
    }


@pytest.fixture
def proof_env(integration_db, monkeypatch) -> Iterator[AccountSyncEnv]:
    """An ``AccountSyncEnv`` running the REAL prover on a publishable host.

    ``deployment_kek`` first: a ``db:`` mint refuses without the deployment-wide KEK, so
    without it every keyed case would fail in provisioning rather than on the behaviour
    it grades.
    """
    with deployment_kek(monkeypatch), AccountSyncEnv() as env:
        env.setup_default_data()
        env.configure_tenant_field("virtual_host", _AGENT_HOST)
        env.use_real_proof_service()
        yield env


def _provision_key(env: AccountSyncEnv, alg: str = "ed25519") -> None:
    """Mint one ACTIVE signing key for this tenant through production."""
    tenant_id = env.tenant_id
    provision_key(signing_key_repo(env, tenant_id), tenant_id, f"{tenant_id}-proof-1", alg=alg)


def _published_jwks(env: AccountSyncEnv) -> dict[str, Any]:
    """The JWKS this tenant PUBLISHES, through A3's own builder.

    Resolved through ``build_jwks(publishable_at(...))`` rather than from the row we
    minted, so the assertion grades the publication hop a receiver actually walks: a
    signature verifiable only against a key we never published is not verifiable.
    """
    from src.core.config import get_config
    from src.core.signing.trust_root import build_jwks

    repo = signing_key_repo(env, env.tenant_id)
    keys = repo.publishable_at(now=_now(), grace_seconds=get_config().signing.grace_seconds)
    return build_jwks(keys)


def _now() -> Any:
    from datetime import UTC, datetime

    return datetime.now(UTC)


def _entry_errors(response: Any) -> list[Any]:
    """The per-ACCOUNT errors, which is where a proof failure lands.

    Not ``response.errors``: ``sync_accounts`` is an atomic XOR at the top level, and a
    per-entry rejection inside a transport-level success is reported on the account
    result. Reading the wrong one made an assertion that looked specific match nothing.
    """
    return list(getattr(response.accounts[0], "errors", None) or [])


class TestChallengeIsSignedAndVerifiable:
    """The acceptance: the emitted signature verifies against the JWKS A3 publishes."""

    async def test_emitted_challenge_verifies_against_the_published_jwks(self, proof_env):
        """One challenge, signed under the webhook profile, verifiable by a receiver.

        Graded exactly as a conformant receiver would: parse ``Signature-Input`` with the
        SDK's structured-field parser, then hand the captured method/url/headers/bytes to
        the SDK's verifier with a resolver fed from our PUBLISHED JWKS. If the key we sign
        with were not the key we publish — a distinct failure from "unsigned" — verification
        is what catches it.
        """
        _provision_key(proof_env)
        jwks = _published_jwks(proof_env)

        with capture_outbound_webhooks(responder=echoing_challenge_response()) as captured:
            response = await proof_env.call_impl_async(req=_request())

        assert len(captured) == 1, (
            f"expected exactly one challenge POST to {_SUBSCRIBER_URL}, got {len(captured)} — "
            "with none there is no signature to verify, and the proof would be vacuous"
        )
        challenge = captured[0]

        assert signature_input_params(challenge)["tag"] == WEBHOOK_TAG, (
            "the challenge must be signed under the WEBHOOK signing profile: security.mdx @ v3.1.1 "
            ":1426 carries request/webhook domain separation in the signature tag, so a "
            "request-profile tag here is a signature a webhook verifier rejects"
        )

        verified = verify_as_conformant_receiver(challenge, jwks)
        assert verified.key_id, "the verifier returned no signer key id, so nothing was actually verified"

        # The other half of the acceptance: a proven challenge must ACTIVATE the
        # subscriber. Without it the module would grade the signature of a challenge whose
        # outcome production ignored.
        assert not _entry_errors(response), (
            f"a proven challenge must leave the entry clean; got errors {_entry_errors(response)!r}"
        )
        assert response.accounts[0].notification_configs[0].active is True, (
            "the subscriber must be persisted ACTIVE once control is proven; got "
            f"{response.accounts[0].notification_configs[0]!r}"
        )

    async def test_the_sdk_webhook_verifier_diverges_from_the_pin(self, proof_env):
        """PINS an SDK divergence, so the workaround above cannot outlive it.

        ``verify_webhook_signature`` demands ``adcp_use == "webhook-signing"`` — the value
        security.mdx @ v3.1.1 :1438 DEPRECATES — and therefore rejects a conformant
        ``request-signing`` key with ``webhook_signature_key_purpose_invalid``. Our JWK is
        right and the SDK is wrong, which is the CLAUDE.md rule landing on a real case: the
        installed SDK is a cross-check, not the authority.

        Asserted rather than remembered because the failure mode of an undocumented
        workaround is that it becomes permanent. When the SDK is fixed this test fails,
        which is the signal to delete both it and the substitution in
        :func:`tests.helpers.signing.verify_as_conformant_receiver`.
        """
        from adcp.signing.errors import SignatureVerificationError
        from adcp.webhooks import WebhookVerifyOptions, verify_webhook_signature

        _provision_key(proof_env)
        jwks = _published_jwks(proof_env)

        with capture_outbound_webhooks(responder=echoing_challenge_response()) as captured:
            await proof_env.call_impl_async(req=_request())

        from adcp.signing.jwks import StaticJwksResolver

        with pytest.raises(SignatureVerificationError) as raised:
            verify_webhook_signature(
                method="POST",
                url=captured[0].url,
                headers=dict(captured[0].headers),
                body=captured[0].content,
                options=WebhookVerifyOptions(jwks_resolver=StaticJwksResolver(jwks)),
            )
        assert "adcp_use" in str(raised.value), (
            "the SDK webhook verifier was expected to reject our conformant request-signing key on "
            f"its adcp_use check; it failed for a different reason instead: {raised.value}. If it now "
            "ACCEPTS the key, the divergence is fixed — delete this test and the expected_adcp_use "
            "substitution in verify_as_conformant_receiver"
        )


class TestChallengeBodyIsConformant:
    """The body a conformant receiver validates — all seven fields, right values."""

    async def test_body_carries_the_seven_required_fields(self, proof_env):
        """The payload validates as ``WebhookChallenge`` and names THIS registration.

        ``additionalProperties: false`` plus seven required properties means a body built
        by hand drifts silently; validating through the SDK type is what makes the
        assertion track the schema instead of a copy of it. The field-by-field checks then
        pin the three the receiver matches against its pending registration (:207).
        """
        _provision_key(proof_env)

        with capture_outbound_webhooks(responder=echoing_challenge_response()) as captured:
            response = await proof_env.call_impl_async(req=_request())

        payload = captured[0].payload
        challenge = WebhookChallenge.model_validate(payload)

        assert challenge.type == "webhook.challenge", (
            f"the wire type must be the spec's `webhook.challenge`, got {challenge.type!r}"
        )
        assert re.fullmatch(r"[A-Za-z0-9_.:-]{32,255}", challenge.challenge), (
            f"challenge {challenge.challenge!r} violates the schema pattern; it must also be "
            "cryptographically random and single-use (:235)"
        )
        assert challenge.subscriber_id == _SUBSCRIBER_ID
        assert [e.value if hasattr(e, "value") else str(e) for e in challenge.event_types] == _EVENT_TYPES, (
            "event_types is part of the proof scope, so it must be the set the subscriber asked for"
        )
        # Read off the RAW payload, never off the re-validated model: round-tripping through
        # `WebhookChallenge` would lowercase the host again and the assertion would be
        # checking pydantic against itself rather than checking the bytes we sent.
        assert payload["seller_agent_url"] == f"https://{_AGENT_HOST}/mcp/", (
            "seller_agent_url must be the PUBLISHED agents[] entry for the registering transport, "
            "BYTE-FOR-BYTE — a receiver matches it against our brand.json with no canonicalization "
            f"(security.mdx :1104 step 5); got {payload['seller_agent_url']!r}"
        )
        assert str(challenge.seller_agent_url) != payload["seller_agent_url"], (
            "this assertion exists to keep the one above honest: it pins that the SDK type's "
            "serializer really does mangle the host, so if a future SDK stops lowercasing, the "
            "emit-the-published-string workaround can be deleted rather than carried forever"
        )
        assert challenge.account_id == response.accounts[0].account_id, (
            "the challenge must name the account id the account is actually created with, or the "
            f"proof is scoped to an account that never exists; challenge={challenge.account_id!r} "
            f"created={response.accounts[0].account_id!r}"
        )

    async def test_delivery_auth_is_rfc9421_when_the_candidate_declares_no_authentication(self, proof_env):
        """No ``authentication`` on the candidate -> ``mode: rfc9421``, no fingerprint.

        The schema FORBIDS ``credential_fingerprint`` for ``rfc9421``, so emitting one
        here is not a harmless extra — it is a body the receiver must reject.
        """
        _provision_key(proof_env)

        with capture_outbound_webhooks(responder=echoing_challenge_response()) as captured:
            await proof_env.call_impl_async(req=_request())

        delivery_auth = WebhookChallenge.model_validate(captured[0].payload).delivery_auth
        assert delivery_auth.mode.value == "rfc9421", f"expected mode rfc9421, got {delivery_auth.mode!r}"
        assert delivery_auth.credential_fingerprint is None, (
            "credential_fingerprint is forbidden for rfc9421 (webhook-challenge.json), got "
            f"{delivery_auth.credential_fingerprint!r}"
        )

    @pytest.mark.parametrize(
        ("scheme", "expected_mode"),
        [("Bearer", "Bearer"), ("HMAC-SHA256", "HMAC-SHA256")],
    )
    async def test_delivery_auth_reports_the_legacy_mode_and_credential_fingerprint(
        self, proof_env, scheme, expected_mode
    ):
        """A legacy candidate is REPORTED as legacy while the challenge stays 9421-signed.

        :207 is explicit that the challenge is signed with the webhook profile key "even
        when the candidate config selects legacy delivery auth", so the candidate's
        authentication appears in the challenge only as DATA. The fingerprint is asserted
        against an independently computed sha256 of the exact credential string — a
        fingerprint copied from production's own output would grade nothing.
        """
        credential = "s" * 40
        _provision_key(proof_env)

        with capture_outbound_webhooks(responder=echoing_challenge_response()) as captured:
            await proof_env.call_impl_async(
                req=_request(authentication={"schemes": [scheme], "credentials": credential})
            )

        assert signature_input_params(captured[0])["tag"] == WEBHOOK_TAG, (
            "a legacy DELIVERY candidate must not downgrade the CHALLENGE's signature (:207)"
        )
        delivery_auth = WebhookChallenge.model_validate(captured[0].payload).delivery_auth
        assert delivery_auth.mode.value == expected_mode, (
            f"declared schemes=[{scheme!r}] must be reported as mode {expected_mode!r}, got {delivery_auth.mode!r}"
        )
        assert delivery_auth.credential_fingerprint == hashlib.sha256(credential.encode()).hexdigest(), (
            "credential_fingerprint must be the sha256 hex of the exact credential string the buyer "
            f"supplied; got {delivery_auth.credential_fingerprint!r}"
        )


class TestFailClosed:
    """Every refusal is "not proven" on the wire, and never an exception."""

    async def test_an_uncredentialed_legacy_registration_is_refused(self, proof_env):
        """A legacy scheme with NO credential has no conformant challenge, so none is sent.

        ``Authentication.credentials`` is optional in the SDK type, so
        ``{"schemes": ["Bearer"]}`` alone is a legal registration. But
        ``webhook-challenge.json`` REQUIRES ``delivery_auth.credential_fingerprint`` when
        ``mode`` is a legacy value and FORBIDS it for ``rfc9421`` — there is no fingerprint
        to compute and no legal mode to report, so no conformant document exists.

        Pydantic does not enforce a conditional required, so without an explicit branch the
        body serializes as ``{"mode": "Bearer"}`` and the receiver rejects it: the entry
        would still fail, but by ACCIDENT, with the log blaming the network. Graded as a
        refusal BEFORE the POST so the failure is the one production intends.
        """
        _provision_key(proof_env)

        with capture_outbound_webhooks(responder=echoing_challenge_response()) as captured:
            response = await proof_env.call_impl_async(req=_request(authentication={"schemes": ["Bearer"]}))

        assert captured == [], (
            f"no challenge can be built for an uncredentialed legacy registration, so none must be "
            f"SENT; {len(captured)} went out and would have carried a schema-invalid delivery_auth"
        )
        assert response.accounts[0].action == "failed", (
            f"expected action 'failed' for an unprovable registration, got {response.accounts[0].action!r}"
        )
        errors = _entry_errors(response)
        assert errors and errors[0].code == "VALIDATION_ERROR", f"expected VALIDATION_ERROR, got {errors!r}"

    async def test_keyless_tenant_sends_no_challenge_and_fails_the_entry(self, proof_env):
        """No signing key -> no POST at all, and ``action: failed`` with a field pointer.

        An unsigned challenge cannot be proven by a conformant receiver, so sending one is
        pure liability. This also pins the fail-closed SHAPE: the refusal happens where the
        per-entry error is produced, not as an exception that would 500 the whole
        ``sync_accounts`` request for every other entry in the batch.
        """
        with capture_outbound_webhooks(responder=echoing_challenge_response()) as captured:
            response = await proof_env.call_impl_async(req=_request())

        assert captured == [], (
            f"a keyless tenant must send NO challenge; it sent {len(captured)} — an unsigned challenge "
            "is one no conformant receiver can attribute to us"
        )
        assert response.accounts[0].action == "failed", (
            f"expected action 'failed' for an unprovable activation, got {response.accounts[0].action!r}"
        )
        errors = _entry_errors(response)
        assert errors and errors[0].code == "VALIDATION_ERROR", f"expected VALIDATION_ERROR, got {errors!r}"
        assert errors[0].field == "notification_configs[0].url", (
            f"the error must point at the endpoint that could not be proven; got {errors[0].field!r}"
        )

    async def test_two_hundred_without_an_echo_is_not_proven(self, proof_env):
        """A 2xx that does not echo the single-use value proves nothing.

        The rule this replaces — "any 2xx == proven" — is satisfied by any endpoint that
        accepts POSTs, including one an attacker points at us. Only the echo binds the
        answer to the value WE generated (:223-235).
        """
        _provision_key(proof_env)

        with capture_outbound_webhooks() as captured:
            response = await proof_env.call_impl_async(req=_request())

        assert len(captured) == 1, "the challenge must still be SENT; only the echo is missing"
        assert response.accounts[0].action == "failed", (
            f"a 2xx with no echoed challenge value is NOT proof of control; got action {response.accounts[0].action!r}"
        )

    async def test_rest_registered_subscriber_refuses_to_challenge(self, proof_env):
        """A REST registration has no published ``agents[].url``, so it fails closed.

        ``agent_endpoint_urls`` publishes one entry per transport we actually serve —
        ``/mcp/`` and ``/a2a`` — and a receiver matches ``seller_agent_url`` byte-for-byte
        against those. There is no REST entry, so any value we could send is one a strict
        receiver cannot resolve a key for; refusing is the honest outcome, and publishing a
        REST entry is its own ticket.
        """
        from tests.harness.transport import Transport

        _provision_key(proof_env)
        rest_identity = proof_env.identity_for(Transport.REST)

        with capture_outbound_webhooks(responder=echoing_challenge_response()) as captured:
            response = await proof_env.call_impl_async(req=_request(), identity=rest_identity)

        assert captured == [], (
            f"a REST-registered subscriber must not be challenged; {len(captured)} POST(s) went out with a "
            "seller_agent_url no receiver can match"
        )
        assert response.accounts[0].action == "failed", (
            f"expected action 'failed' for a REST registration, got {response.accounts[0].action!r}"
        )
