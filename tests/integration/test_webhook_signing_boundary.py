"""C1 (#1291, salesagent-z6nr.18) — one outbound webhook boundary, one mode per receiver.

Core Invariant under test: *every outbound AdCP webhook is authenticated by exactly
ONE mode, selected at ONE seam from the receiver's own registration, and the bytes
signed are the bytes sent.*

TDD RED. Nothing here passes until the boundary exists:
``src/core/signing/webhook_sender_factory.build_webhook_sender`` (design step 1),
the ``authentication``-PRESENT mode predicate (step 2), the three senders routed
through it (step 3) and ``WebhookSigningPosture`` (step 5).

Three groups, each grading one thing the design step 6 ("Evidence") names.

**1. The mode oracle** (``TestWebhookSenderModeOracle``). ``WebhookSender``'s own
``signs_with_rfc9421`` is the oracle — its docstring states the C1<->D1 contract
verbatim, and ``from_bearer_token`` / ``from_adcp_legacy_hmac`` /
``from_standard_webhooks_secret`` senders return False. The regression it pins is
the mode-selector defect: the predicate the three senders share today keys on
``authentication_type == "HMAC-SHA256"``, so a **bearer** registration falls
through into the RFC 9421 arm. security.mdx @ v3.1.1 :1424 keys mode selection on
``authentication`` being PRESENT — HMAC-SHA256 **or** Bearer — and :1466 makes the
consequence concrete: a receiver that registered bearer and gets a 9421 signature
answers ``webhook_mode_mismatch``. Every registration is graded WITH a live tenant
signing key, so "not 9421" cannot be true merely because there was nothing to sign
with. Case variants are parametrized because the three senders spell the same
value three ways today (``"Bearer"``, ``"bearer"``, ``"basic"``).

**2. Tag equals declared profile** (``TestDeclaredProfileEqualsEmittedTag``). The
``webhook_signing.profile`` enum value MUST equal the ``tag=`` parameter in the
emitted ``Signature-Input`` (``get-adcp-capabilities-response.json``: "so receivers
can statically validate the declared profile against the on-wire tag"). Both sides
are compared against ``adcp.signing.webhook_signer.WEBHOOK_TAG``; there is no
hand-copied literal anywhere in this module, which is the only way the assertion
can catch a drift rather than restate one.

**3. The bytes sent are the bytes signed** (``TestSignedBytesAreTheBytesSent``),
graded PER SENDER over a NON-ASCII payload. Measured fact, not re-derived here:
httpx 0.28.1's ``encode_json`` already emits ``separators=(",", ":")`` with
``ensure_ascii=False``, so for an ASCII payload ``json=`` and ``content=`` produce
IDENTICAL bytes and an ASCII oracle is VACUOUS on the two httpx senders. The SDK
serializes with ``json.dumps`` defaults (``ensure_ascii=True``), so only a payload
carrying a non-ASCII value distinguishes "signed the dict" from "sent the signed
bytes". Each payload also has non-sorted keys, so a fix that signs with
``sort_keys=True`` (``webhook_delivery_service.py:320`` does today) while POSTing
insertion order is caught too. This is #1441, fixed by construction.

``order_approval_service`` is the fourth sender and is graded by
``tests/integration/test_order_approval_webhook_signing.py`` (salesagent-98t2) on
the legacy HMAC leg of the same contract; it is not repeated here. The fifth and
sixth POSTs — ``notification_proof_service`` (C2) and the mock adapter — are
graded structurally by ``tests/unit/test_architecture_webhook_sender_boundary.py``,
which enumerates SENDERS rather than signing calls.

Only the socket is stubbed (``tests.helpers.webhook_wire``). The tenant's signing
key is minted through production (``provision_signing_key``, now reachable since
salesagent-7x8t), read back through the production repository, and key presence is
derived by production's ``signing_key_backed`` — never re-derived here.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, NamedTuple
from unittest.mock import patch

import pytest
from adcp.signing import content_digest_matches
from adcp.signing.webhook_signer import WEBHOOK_TAG

from tests.harness._base import BareIntegrationEnv
from tests.helpers.signing import deployment_kek, just_after_provisioning, provision_key, signing_key_repo
from tests.helpers.webhook_wire import CapturedWebhook, capture_outbound_webhooks, signature_input_params

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_TENANT_ID = "tenant_webhook_boundary"
_PRINCIPAL_ID = "principal_webhook_boundary"
_MEDIA_BUY_ID = "mb_webhook_boundary"
_WEBHOOK_URL = "https://buyer.example.com/adcp/notifications"
_KID = "webhook-boundary-key-1"

#: 44 chars — clears the 32-char floor ``webhook_delivery_service`` enforces, so no
#: sender can decline to authenticate on strength grounds and leave a test green for
#: a reason that has nothing to do with mode selection.
_SECRET = "webhook-boundary-shared-secret-0123456789abc"  # noqa: S105

#: Load-bearing, not decoration: the one value that makes "signed the dict" and
#: "sent the signed bytes" produce different bytes (module docstring).
_ADVERTISER = "Grüße Tōkyō Medien"


# ---------------------------------------------------------------------------
# Seeding — factories only, and the key is minted through production
# ---------------------------------------------------------------------------


@pytest.fixture
def signing_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[BareIntegrationEnv]:
    """A tenant that HOLDS an active signing key, so 9421 mode is reachable.

    ``deployment_kek`` first: a ``db:`` mint refuses without the deployment KEK, so
    without it every test here would fail on provisioning rather than on the
    behavior it grades.
    """
    with deployment_kek(monkeypatch), BareIntegrationEnv(tenant_id=_TENANT_ID, principal_id=_PRINCIPAL_ID) as env:
        yield env


class _Seeded(NamedTuple):
    """The tenant-scoped signing repository plus the rows a registration hangs off."""

    repo: Any
    tenant: Any
    principal: Any


#: A DOTTED host, so ``canonical_agent_url`` derives ``https://`` for it.
#:
#: Load-bearing, not decoration. #1291 D1 put the publishability gate on the ONE posture
#: object this factory's sender reads: ``webhook_signing.supported`` requires an active key
#: AND an origin that can serve https, because key discovery for our outbound signatures
#: runs through ``identity.brand_json_url`` and the pin fixes that to ``^https://``. With
#: no ``virtual_host`` and no ``SALES_AGENT_DOMAIN`` (which no integration env sets),
#: ``canonical_agent_url`` returns ``http://localhost:8080`` and the RFC 9421 arm is
#: unreachable — so every test in this file would grade the unsigned branch while reading
#: like it graded the signed one. The precedent is ``tls_trust_root_tenant(netloc)``
#: (``tests/e2e/test_trust_root_e2e.py``).
_AGENT_HOST = "seller-webhook-boundary.example.com"


def _seed_tenant_with_key(env: BareIntegrationEnv) -> _Seeded:
    """Create the tenant/principal and mint their signing key through production.

    The repository handed back is the tenant-scoped ``SigningKeyRepository`` C1's
    factory takes, so the test gives production exactly the object production gets.
    """
    from tests.factories import PrincipalFactory, TenantFactory

    tenant = TenantFactory(tenant_id=_TENANT_ID, virtual_host=_AGENT_HOST)
    principal = PrincipalFactory(tenant=tenant, principal_id=_PRINCIPAL_ID)

    repo = signing_key_repo(env, _TENANT_ID)
    provision_key(repo, _TENANT_ID, _KID, alg="ed25519")
    return _Seeded(repo=repo, tenant=tenant, principal=principal)


def _register_receiver(seeded: _Seeded, **registration: Any) -> Any:
    """Seed the buyer's ``PushNotificationConfig`` row — the ONE mode selector."""
    from tests.factories import PushNotificationConfigFactory

    return PushNotificationConfigFactory(
        tenant=seeded.tenant,
        principal=seeded.principal,
        url=_WEBHOOK_URL,
        **registration,
    )


def _key_is_live(repo: Any, now: datetime) -> None:
    """Assert the tenant really can sign at *now*, through production's derivation.

    ``signing_key_backed`` (salesagent-7x8t) is THE single key-presence derivation;
    asserting through it rather than re-querying the table is what stops this suite
    from growing a second one. Without this control, "no RFC 9421 headers" would be
    equally explained by a keyless tenant, and every mode assertion below would be
    vacuous.
    """
    from src.core.signing.posture import signing_key_backed

    assert signing_key_backed(repo, now=now).signs is True, (
        "the tenant has no ACTIVE signing key at the instant under test, so every "
        "assertion about RFC 9421 mode below would hold for the wrong reason"
    )


# ---------------------------------------------------------------------------
# Driving the production senders
# ---------------------------------------------------------------------------


def _protocol_notification_payload() -> dict[str, Any]:
    """A buyer push notification whose keys are NOT sorted and whose text is not ASCII."""
    return {
        "event": "media_buy_status",
        "media_buy_id": _MEDIA_BUY_ID,
        "advertiser_name": _ADVERTISER,
        "adcp_version": "3.1.1",
    }


def _send_protocol_notification(config: Any, payload: dict[str, Any]) -> CapturedWebhook:
    """Deliver through ``ProtocolWebhookService`` — the already-async AdCP sender."""
    from src.services.protocol_webhook_service import ProtocolWebhookService

    with capture_outbound_webhooks() as captured:
        asyncio.run(
            ProtocolWebhookService().send_notification(
                config,
                payload,
                {"task_type": "media_buy_status", "tenant_id": _TENANT_ID, "principal_id": _PRINCIPAL_ID},
            )
        )

    return _exactly_one(captured, "media_buy_status push notification")


def _send_delivery_report() -> CapturedWebhook:
    """Deliver through ``WebhookDeliveryService`` — the sync delivery-report sender.

    ``by_package`` is passed through verbatim into the AdCP payload, which is where
    the non-ASCII value enters this sender's body; the surrounding keys are emitted
    in construction order (``adcp_version``, ``notification_type``, ...), not sorted.
    """
    from src.services.webhook_delivery_service import WebhookDeliveryService

    period_end = datetime.now(UTC)
    with capture_outbound_webhooks() as captured:
        WebhookDeliveryService().send_delivery_webhook(
            media_buy_id=_MEDIA_BUY_ID,
            tenant_id=_TENANT_ID,
            principal_id=_PRINCIPAL_ID,
            reporting_period_start=period_end - timedelta(hours=1),
            reporting_period_end=period_end,
            impressions=125_000,
            spend=1234.56,
            by_package=[{"package_id": "pkg_1", "buyer_ref": _ADVERTISER, "impressions": 125_000}],
        )

    return _exactly_one(captured, "delivery report")


#: The ladder the receiver answers: refuse, refuse, accept. Three deliveries of ONE
#: event, which is what makes signature freshness gradeable at all — a single
#: delivery cannot show whether a signature is reused.
_REFUSE_REFUSE_ACCEPT = (500, 500, 200)


class _LadderClock:
    """A wall clock that only advances when the retry ladder sleeps.

    Necessary, not decorative, and measured rather than assumed: the SDK stamps
    ``created`` as ``int(time.time())`` (``adcp/signing/signer.py``), and with the
    ladder's backoff patched out all three signings land inside the SAME second —
    measured ``created = [1787732708, 1787732708, 1787732708]``. A "created does not
    go backwards" assertion is then true in the armed arm AND in an arm that signs
    once and reuses the signature, which is an assertion that cannot fail: the exact
    defect this test exists to grade, reproduced inside the instrument.

    Advancing the clock by the delay the service asked for is what makes the
    assertion strict instead. The alternative — dropping the assertion — is cheaper
    and wrong: freshness is ``created`` as much as ``nonce``, and a stale ``created``
    is what a receiver's window check refuses on a slow ladder.
    """

    def __init__(self, start: float) -> None:
        self.now = start

    def sleep(self, seconds: float) -> None:
        """Stand in for ``time.sleep`` — advance instead of blocking.

        At least a whole second per hop, because ``created`` is an integer number of
        seconds: a sub-second advance would round away and leave the three stamps
        equal again. The real ladder's first backoff is 2s + jitter, so this
        under-states rather than invents the passage of time.
        """
        self.now += max(seconds, 1.0)

    def time(self) -> float:
        return self.now


@contextmanager
def _ladder_without_backoff() -> Iterator[None]:
    """Run the delivery ladder in milliseconds, on a clock that still moves.

    Two patches, at two boundaries, and both are the CLOCK — the true external this
    test is allowed to mock:

    * ``src.services.webhook_delivery_service.time.sleep`` — the service's own
      backoff, patched exactly as ``test_webhook_retries_on_failure`` patches the
      sibling loop's, or the 2s/4s ladder costs seven seconds of wall time;
    * the ``time`` name inside ``adcp.signing.signer``, whose sole use is
      ``int(time.time())`` for ``created``. Replaced with a namespace exposing only
      that call, so nothing else in the process reads a fake clock.

    Nothing about the delivery path itself is patched: the service, the boundary,
    the sender and the signer all run for real, and only the socket
    (:func:`capture_outbound_webhooks`) and the clock are replaced.
    """
    from adcp.signing import signer as sdk_signer

    clock = _LadderClock(start=time.time())
    with (
        patch("src.services.webhook_delivery_service.time.sleep", clock.sleep),
        patch.object(sdk_signer, "time", SimpleNamespace(time=clock.time)),
    ):
        yield


def _send_delivery_report_over_a_retry_ladder() -> list[CapturedWebhook]:
    """Drive ``WebhookDeliveryService`` through refuse/refuse/accept, capturing all three.

    Entered at ``send_delivery_webhook`` — the PUBLIC seam, the same one
    ``_send_delivery_report`` above uses — never at the private
    ``_deliver_with_backoff``. The claim is about where the delivery boundary is
    CALLED from, so a test that reached inside the loop would be grading its own
    reach into it.
    """
    from src.services.webhook_delivery_service import WebhookDeliveryService

    period_end = datetime.now(UTC)
    with (
        _ladder_without_backoff(),
        capture_outbound_webhooks(status_codes=_REFUSE_REFUSE_ACCEPT) as captured,
    ):
        WebhookDeliveryService().send_delivery_webhook(
            media_buy_id=_MEDIA_BUY_ID,
            tenant_id=_TENANT_ID,
            principal_id=_PRINCIPAL_ID,
            reporting_period_start=period_end - timedelta(hours=1),
            reporting_period_end=period_end,
            impressions=125_000,
            spend=1234.56,
            by_package=[{"package_id": "pkg_1", "buyer_ref": _ADVERTISER, "impressions": 125_000}],
        )

    return captured


def _exactly_one(captured: list[CapturedWebhook], what: str) -> CapturedWebhook:
    assert len(captured) == 1, (
        f"expected exactly 1 {what} delivered to {_WEBHOOK_URL}, got {len(captured)} — "
        "a registered buyer was not told, or was told more than once"
    )
    return captured[0]


# ---------------------------------------------------------------------------
# Shared assertions
# ---------------------------------------------------------------------------


def _assert_content_digest_covers_received_bytes(captured: CapturedWebhook, non_ascii_value: str) -> None:
    """The digest the signature covers must recompute over the bytes received.

    ``Content-Digest`` is signed (``sign_webhook`` pins ``cover_content_digest=True``),
    so this single comparison grades the whole "bytes signed == bytes sent" claim
    without the test needing the private key: any re-serialization between signing
    and POSTing changes the body and breaks the digest.
    """
    digest = captured.headers.get("content-digest")
    assert digest, (
        "no Content-Digest header on the outbound webhook, so nothing binds the signature to the body; "
        f"headers were {sorted(captured.headers.keys())}"
    )

    # Non-vacuity, asserted BEFORE the digest: a body whose text is pure ASCII
    # cannot distinguish the two encodings, so a green digest would prove nothing.
    body_text = json.dumps(json.loads(captured.content), ensure_ascii=False)
    assert non_ascii_value in body_text, f"the non-ASCII value never reached the buyer: {captured.content!r}"
    assert any(ord(char) > 127 for char in body_text), "payload carries no non-ASCII character; the oracle is vacuous"

    assert content_digest_matches(digest, captured.content), (
        "Content-Digest does not recompute over the bytes actually POSTed — the receiver would reject "
        f"this webhook (#1441). digest={digest!r} body={captured.content!r}"
    )


# ---------------------------------------------------------------------------
# 1. The mode oracle
# ---------------------------------------------------------------------------


class TestWebhookSenderModeOracle:
    """One seam picks the mode, and it keys on ``authentication`` being PRESENT."""

    @pytest.mark.parametrize(
        "authentication_type",
        ["HMAC-SHA256", "hmac-sha256", "Bearer", "bearer", "Basic", "basic"],
        ids=["hmac-canonical", "hmac-lower", "bearer-title", "bearer-lower", "basic-title", "basic-lower"],
    )
    def test_registration_with_authentication_never_signs_with_rfc9421(
        self, integration_db, signing_env, authentication_type
    ):
        """Any registration carrying ``authentication`` selects a LEGACY sender.

        The bearer rows are the regression for the mode-selector defect: under the
        ``== "HMAC-SHA256"`` predicate they take the 9421 arm, and security.mdx
        :1466 says the receiver answers ``webhook_mode_mismatch``. The basic rows
        pin the same for the third scheme ``order_approval_service`` accepts, and
        the case variants pin that ONE predicate replaced the three spellings.
        """
        from src.core.signing.webhook_sender_factory import build_webhook_sender

        seeded = _seed_tenant_with_key(signing_env)
        now = just_after_provisioning()
        _key_is_live(seeded.repo, now)

        config = _register_receiver(seeded, authentication_type=authentication_type, authentication_token=_SECRET)

        sender = build_webhook_sender(config=config, tenant_id=_TENANT_ID, repo=seeded.repo, now=now)

        assert sender.signs_with_rfc9421 is False, (
            f"a receiver registered as authentication_type={authentication_type!r} was wired to the "
            "RFC 9421 sender; it will answer webhook_mode_mismatch (security.mdx @ v3.1.1 :1466). "
            "Mode selection keys on `authentication` being PRESENT (:1424), not on the value being HMAC"
        )

    def test_registration_without_authentication_signs_with_rfc9421(self, integration_db, signing_env):
        """No ``authentication`` and a live key -> the RFC 9421 sender.

        The positive half of the same switch. Without it the oracle above is
        satisfied by a factory that never returns a 9421 sender at all.
        """
        from src.core.signing.webhook_sender_factory import build_webhook_sender

        seeded = _seed_tenant_with_key(signing_env)
        now = just_after_provisioning()
        _key_is_live(seeded.repo, now)

        config = _register_receiver(seeded)

        sender = build_webhook_sender(config=config, tenant_id=_TENANT_ID, repo=seeded.repo, now=now)

        assert sender.signs_with_rfc9421 is True, (
            "a receiver that registered no `authentication` was NOT wired to the RFC 9421 sender even "
            "though the tenant holds an active signing key — 9421 is the default for new registrations "
            "(security.mdx @ v3.1.1 :1424)"
        )


# ---------------------------------------------------------------------------
# 2. Declared profile == emitted tag
# ---------------------------------------------------------------------------


class TestDeclaredProfileEqualsEmittedTag:
    """What we advertise and what we emit are the same string, from the same source."""

    def test_emitted_signature_input_tag_equals_declared_webhook_profile(self, integration_db, signing_env):
        """``Signature-Input`` ``tag=`` equals ``webhook_signing.profile``.

        Graded on a REAL delivery from a production sender rather than on a
        hand-driven ``sign_webhook`` call: the claim is about what a buyer receives,
        and a sender that signs correctly but attaches the headers to nothing would
        satisfy the narrower version.
        """
        from src.core.signing.posture import WebhookSigningPosture

        seeded = _seed_tenant_with_key(signing_env)
        _key_is_live(seeded.repo, just_after_provisioning())
        config = _register_receiver(seeded)

        captured = _send_protocol_notification(config, _protocol_notification_payload())

        emitted_tag = signature_input_params(captured)["tag"]
        declared_profile = WebhookSigningPosture(supported=True).profile

        assert emitted_tag == WEBHOOK_TAG, (
            f"emitted Signature-Input tag={emitted_tag!r} is not the SDK's webhook profile {WEBHOOK_TAG!r}"
        )
        assert declared_profile == emitted_tag, (
            f"we advertise webhook_signing.profile={declared_profile!r} but emit tag={emitted_tag!r}; a "
            "receiver statically validating the declared profile against the on-wire tag would reject us "
            "(get-adcp-capabilities-response.json #/properties/webhook_signing/profile)"
        )

    def test_emitted_algorithm_is_inside_the_v1_profile(self, integration_db, signing_env):
        """The ``alg`` on the wire is one the ``adcp/webhook-signing/v1`` profile allows.

        ``SIGNING_ALG_VALUES`` is DERIVED from ``adcp.signing.crypto.ALLOWED_ALGS``
        (never hand-copied — migration ``e381618812f1`` exists because a hand-written
        allowlist froze while the spec enum grew), so this compares the wire against
        the SDK rather than against a literal. A sender that fell back to
        ``hmac-sha256`` or ``rsa-pss-sha512`` fails here.
        """
        from src.core.signing.algorithms import SIGNING_ALG_VALUES

        seeded = _seed_tenant_with_key(signing_env)
        _key_is_live(seeded.repo, just_after_provisioning())
        config = _register_receiver(seeded)

        captured = _send_protocol_notification(config, _protocol_notification_payload())

        emitted_alg = signature_input_params(captured)["alg"]

        assert emitted_alg in SIGNING_ALG_VALUES, (
            f"outbound webhook signed with alg={emitted_alg!r}, outside the profile's algorithm set "
            f"{list(SIGNING_ALG_VALUES)}"
        )


# ---------------------------------------------------------------------------
# 3. The bytes sent are the bytes signed — per sender, non-ASCII
# ---------------------------------------------------------------------------


class TestSignedBytesAreTheBytesSent:
    """#1441, graded per sender over a payload the two encodings disagree on."""

    def test_protocol_notification_digest_covers_the_bytes_received(self, integration_db, signing_env):
        """``protocol_webhook_service`` POSTs the bytes it signed.

        This sender is on ``requests`` today, which emits ``{"a": 1}`` WITH spaces
        while the signing core emits the compact form — so this leg diverges even
        before ``ensure_ascii`` enters, and the non-ASCII value keeps it diverging
        after the sender moves onto httpx.
        """
        seeded = _seed_tenant_with_key(signing_env)
        _key_is_live(seeded.repo, just_after_provisioning())
        config = _register_receiver(seeded)

        captured = _send_protocol_notification(config, _protocol_notification_payload())

        _assert_content_digest_covers_received_bytes(captured, _ADVERTISER)

    def test_delivery_report_digest_covers_the_bytes_received(self, integration_db, signing_env):
        """``webhook_delivery_service`` POSTs the bytes it signed.

        The vacuous-oracle case the design calls out: this sender is on httpx, whose
        ``encode_json`` already matches the compact separators, so ONLY the
        non-ASCII ``by_package`` value can distinguish re-serialization from
        transmission of the signed bytes.
        """
        seeded = _seed_tenant_with_key(signing_env)
        _key_is_live(seeded.repo, just_after_provisioning())
        _register_receiver(seeded)

        captured = _send_delivery_report()

        _assert_content_digest_covers_received_bytes(captured, _ADVERTISER)

    @pytest.mark.parametrize(
        ("authentication_type", "credential_header"),
        [
            ("HMAC-SHA256", "x-adcp-signature"),
            ("hmac-sha256", "x-adcp-signature"),
            ("Bearer", "authorization"),
            ("bearer", "authorization"),
            ("basic", "authorization"),
        ],
        ids=["hmac-canonical", "hmac-lower", "bearer-title", "bearer-lower", "basic-lower"],
    )
    def test_legacy_registration_is_authenticated_and_unsigned_on_the_wire(
        self, integration_db, signing_env, authentication_type, credential_header
    ):
        """The legacy leg of the same switch, graded on the wire rather than at the seam.

        The mode oracle grades the constructor choice; this grades its consequence
        for the receiver, which is what the buyer actually experiences. Two claims,
        and each one has failed in production:

        * the registered credential is ON the delivery — lowercase ``bearer`` is the
          shape ``order_approval_service`` and ``webhook_delivery_service`` write,
          while ``protocol_webhook_service`` matches only ``"Bearer"``, so today
          that buyer gets no credential at all;
        * and NO RFC 9421 headers ride along — security.mdx @ v3.1.1 :1425 forbids
          signing one webhook both ways, and :1466 has the receiver answer
          ``webhook_mode_mismatch``.

        Only the PRESENCE of the credential header is pinned, not its exact value:
        which legacy header a scheme maps onto is the boundary's to decide, whereas
        "the buyer's registration was honoured at all" is the contract.
        """
        seeded = _seed_tenant_with_key(signing_env)
        _key_is_live(seeded.repo, just_after_provisioning())
        config = _register_receiver(seeded, authentication_type=authentication_type, authentication_token=_SECRET)

        captured = _send_protocol_notification(config, _protocol_notification_payload())

        assert credential_header in captured.headers, (
            f"a receiver registered as authentication_type={authentication_type!r} was delivered with no "
            f"{credential_header!r} header — the registration it chose was silently ignored; headers were "
            f"{sorted(captured.headers.keys())}"
        )
        assert "signature-input" not in captured.headers, (
            f"a {authentication_type!r} receiver was ALSO sent an RFC 9421 signature; security.mdx @ v3.1.1 "
            ":1425 forbids signing the same webhook both ways and :1466 makes the receiver answer "
            "webhook_mode_mismatch"
        )


# ---------------------------------------------------------------------------
# 4. Signature freshness across a retry ladder
# ---------------------------------------------------------------------------


class TestSignatureFreshnessAcrossTheRetryLadder:
    """Every attempt at one event carries its OWN signature (lane L13, clause 2).

    ``WebhookDeliveryService`` calls the delivery boundary
    (``deliver_adcp_webhook_sync``) from INSIDE ``for attempt in range(max_retries)``,
    so each attempt re-enters the boundary and re-signs. That placement is the whole
    obligation: a receiver enforcing RFC 9421 replay protection rejects a second POST
    carrying a nonce it has already seen, and a receiver enforcing a ``created``
    window rejects one whose stamp has aged out of it. A refactor that hoisted the
    signing above the loop — one signature reused for all three POSTs — would turn
    every retry into a replay rejection.

    NOTHING GRADED IT. ``nonce`` appears zero times across the five files that drive
    outbound webhook signing (``test_webhook_signing_boundary.py``,
    ``test_order_approval_webhook_signing.py``, ``test_webhook_signature_e2e.py``,
    ``test_delivery_service_behavioral.py``, ``tests/bdd/steps/domain/uc004_delivery.py``)
    while the same pattern matches in ``tests/helpers/webhook_wire.py``,
    ``tests/helpers/signing.py``, ``tests/helpers/signing_vectors.py`` and
    ``tests/unit/test_signing_raw_wire_gaps.py`` — so the zero is a real absence and
    not a broken pattern.

    ONE LOOP, NAMED. Two retry loops call this boundary; this class drives
    ``WebhookDeliveryService`` (``src/services/webhook_delivery_service.py``).
    ``_send_approval_webhook`` (``src/services/order_approval_service.py``) is the
    second and stays a KNOWN-UNGRADED call site for signature freshness — one test,
    one loop.

    The ladder shape is borrowed rather than invented:
    ``tests/unit/test_order_approval_service.py::test_webhook_retries_on_failure``
    already drives ``(500, 500, 200)`` and pins the count and the single
    ``idempotency_key`` on the UNSIGNED arm. What is new here is the SIGNED arm and
    the freshness of the signature parameters.
    """

    @staticmethod
    def _ladder(signing_env: BareIntegrationEnv) -> list[CapturedWebhook]:
        """A tenant that can sign, a receiver registered for 9421, three attempts."""
        seeded = _seed_tenant_with_key(signing_env)
        _key_is_live(seeded.repo, just_after_provisioning())
        # NO ``authentication`` on the registration: that is what selects the RFC 9421
        # sender (the mode oracle above), and without it there would be no
        # Signature-Input to read a nonce or a created out of.
        _register_receiver(seeded)

        captured = _send_delivery_report_over_a_retry_ladder()

        assert len(captured) == 3, (
            f"expected exactly 3 delivery attempts for one event under {_REFUSE_REFUSE_ACCEPT}, got "
            f"{len(captured)} — fewer means the ladder did not retry and the freshness assertions below "
            "would have nothing to compare; more means a foreign delivery landed in this capture"
        )
        return captured

    def test_each_retry_carries_its_own_nonce(self, integration_db, signing_env):
        """Three attempts, three DISTINCT nonces.

        The SDK mints a fresh nonce per ``sign_request`` call, so distinctness is a
        direct statement that the boundary was re-entered per attempt. Reused, the
        second and third POSTs are byte-identical replays and a conformant receiver
        refuses them.
        """
        captured = self._ladder(signing_env)

        nonces = [signature_input_params(sent)["nonce"] for sent in captured]

        assert len(set(nonces)) == 3, (
            f"the {len(captured)} attempts at ONE event carried {len(set(nonces))} distinct nonce(s), "
            f"expected 3: {nonces}. A repeated nonce means one signature was minted and reused across "
            "the ladder, so every retry is a replay a receiver enforcing RFC 9421 replay protection "
            "rejects — the delivery boundary must be re-entered per attempt"
        )

    def test_each_retry_is_stamped_later_than_the_one_before(self, integration_db, signing_env):
        """``created`` STRICTLY increases across the ladder.

        Strictly, not merely non-decreasing, and the clock is advanced inside the
        patched sleep to make that gradeable — see :class:`_LadderClock` for why the
        weaker form is an assertion that cannot fail. Freshness is ``created`` as
        much as ``nonce``: a receiver checking the signature's age against its own
        clock refuses a retry still carrying the first attempt's stamp.
        """
        captured = self._ladder(signing_env)

        created = [int(signature_input_params(sent)["created"]) for sent in captured]

        assert all(later > earlier for earlier, later in zip(created, created[1:], strict=False)), (
            f"created did not strictly increase across the ladder: {created}. Two causes produce equal "
            "stamps and the nonce test above tells them apart. If the nonces repeat, one signature was "
            "minted and reused for every attempt, so a retry arriving minutes later still claims the "
            "first attempt's time and ages out of the receiver's acceptance window. If the nonces "
            "differ, each attempt re-signed but production stopped waiting between them, so this "
            "ladder's clock never advanced"
        )

    def test_every_attempt_carries_the_same_idempotency_key(self, integration_db, signing_env):
        """One key, three attempts — these are retries of ONE event, not three events.

        The control that makes the two freshness assertions mean something: without
        it, "three distinct nonces" is equally true of a sender that minted three
        SEPARATE events, and nothing would say the receiver can dedupe them.

        READ OUT OF THE JSON BODY, never a header, and that is load-bearing. The
        boundary injects the key into the body before signing
        (``WebhookSender.send_raw``), and no ``Idempotency-Key`` header reaches the
        wire at all — the captured headers are accept, accept-encoding, connection,
        content-digest, content-length, content-type, host, signature,
        signature-input, user-agent. A test reading a header collects three ``None``
        values, and ``len({None, None, None}) == 1`` passes under every mutation,
        including one that mints a fresh key per attempt.
        """
        captured = self._ladder(signing_env)

        keys = [json.loads(sent.content)["idempotency_key"] for sent in captured]

        # Non-vacuity FIRST: three absent values also collapse into one distinct value.
        assert all(isinstance(key, str) and key for key in keys), (
            f"an attempt carried no idempotency_key in its JSON body: {keys}. Three absent values would "
            "collapse into one 'distinct' value and the equality below would hold for want of evidence"
        )
        assert len(set(keys)) == 1, (
            f"the three attempts at one event carried {len(set(keys))} distinct idempotency_key(s): "
            f"{keys}. The receiver cannot dedupe them, so a retried delivery is processed as a new event"
        )
