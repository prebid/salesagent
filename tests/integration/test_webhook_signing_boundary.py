"""C1 (#1291) — one outbound webhook boundary, one auth mode per receiver.

Core Invariant under test: *every outbound AdCP webhook is authenticated by exactly
ONE mode, selected at ONE seam from the receiver's own registration, and the bytes
signed are the bytes sent.*

THE SEAM THIS GRADES IS ``src/core/security/webhook_egress.py``. On the #1721
architecture there is no webhook sender factory and no
``adcp.webhooks.WebhookSender``: ``deliver_webhook`` / ``adeliver_webhook`` are the
two twins every sender dials, and the whole mode decision is the single ``match`` in
``_headers_for(auth, headers, signer) -> (headers, hmac_secret, sign)`` whose three
return slots are populated one at a time. "Never signed both ways" (security.mdx @
v3.1.1 :1425) is therefore a SHAPE rather than a rule, and the RFC 9421 signature is
applied by ``outbound_http.send``/``asend`` invoking the returned ``SignAttempt``
once PER ATTEMPT. This module drove ``build_webhook_sender`` before the merge; that
factory was deleted with the second delivery path, and every claim it carried is
re-expressed here against the surviving seam.

Four groups, each grading one thing the design step 6 ("Evidence") names.

**1. The mode selector** (``TestOneAuthModePerReceiver``). ``_headers_for`` is the
oracle, read through the same ``_authentication_or_refusal`` gate every sender goes
through, so a stored row is graded exactly as production grades it. The regression it
pins is the mode-selector defect: the predicate the three senders once shared keyed on
``authentication_type == "HMAC-SHA256"``, so a **bearer** registration fell through
into the RFC 9421 arm. security.mdx @ v3.1.1 :1424 keys mode selection on
``authentication`` being PRESENT — HMAC-SHA256 **or** Bearer — and :1466 makes the
consequence concrete: a receiver that registered bearer and gets a 9421 signature
answers ``webhook_mode_mismatch``. Every registration is graded WITH A LIVE SIGNER
resolved through production's own ``webhook_delivery_signer``, so "not 9421" cannot
be true merely because there was nothing to sign with. Case variants are parametrized
because the three senders spelled the same value three ways; under the pinned enum the
off-spelling rows are now REFUSALS rather than deliveries, which is still "not 9421"
and is graded on the wire by group 3.

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
IDENTICAL bytes and an ASCII oracle is VACUOUS. Only a payload carrying a non-ASCII
value distinguishes "signed the dict" from "sent the signed bytes". Each payload also
has non-sorted keys, so a fix that signs with ``sort_keys=True`` while POSTing
insertion order is caught too. This is #1441, fixed by construction now that
``prepare_signed_request`` returns ``(headers, bytes)`` and those exact bytes are the
``content=`` handed to the seam.

**4. Signature freshness across a retry ladder**
(``TestSignatureFreshnessAcrossTheRetryLadder``). The ladder moved INTO the seam:
``deliver_webhook(..., max_attempts=3)`` retries inside ``outbound_http.send``, which
rebuilds the request and re-invokes ``sign`` per attempt. That placement is the whole
obligation, and it is the same obligation as before the merge — only its owner changed.

``order_approval_service`` is the fourth sender and is graded by
``tests/integration/test_order_approval_webhook_signing.py`` on the legacy HMAC leg
of the same contract; it is not repeated here. ``notification_proof_service`` (C2)
and the mock adapter are graded structurally by
``tests/unit/test_architecture_webhook_sender_boundary.py``, which enumerates SENDERS
rather than signing calls.

Only the socket is stubbed (``tests.helpers.webhook_wire``). The tenant's signing
key is minted through production (``provision_signing_key``), read back through the
production repository, and key presence is derived by production's
``signing_key_backed`` — never re-derived here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, NamedTuple
from unittest.mock import patch

import pytest
from adcp import create_mcp_webhook_payload
from adcp.signing import content_digest_matches
from adcp.signing.webhook_signer import WEBHOOK_TAG
from adcp.types import McpWebhookPayload

from tests.harness._base import BareIntegrationEnv
from tests.harness.protocol_webhook import DELIVERY_METADATA_TASK_TYPE, DELIVERY_PAYLOAD_TASK_TYPE
from tests.helpers.signing import deployment_kek, just_after_provisioning, provision_key, signing_key_repo
from tests.helpers.webhook_wire import CapturedWebhook, capture_outbound_webhooks, signature_input_params

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_TENANT_ID = "tenant_webhook_boundary"
_PRINCIPAL_ID = "principal_webhook_boundary"
_MEDIA_BUY_ID = "mb_webhook_boundary"
_WEBHOOK_URL = "https://buyer.example.com/adcp/notifications"
_KID = "webhook-boundary-key-1"

#: 44 chars — clears the pinned ``minLength: 32`` on ``authentication.credentials``, so
#: no registration can be refused on strength grounds and leave a test green for a
#: reason that has nothing to do with mode selection.
_SECRET = "webhook-boundary-shared-secret-0123456789abc"  # noqa: S105

#: Load-bearing, not decoration: the one value that makes "signed the dict" and
#: "sent the signed bytes" produce different bytes (module docstring).
_ADVERTISER = "Grüße Tōkyō Medien"

#: The Content-Type every sender frames its body with, and the ONE spelling the 9421
#: arm accepts (``_check_signable_content_type``). Named here so the seam probe below
#: reaches ``_headers_for`` the way a sender does rather than through the absence case.
_JSON_CONTENT_TYPE = {"Content-Type": "application/json"}


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
#: object ``webhook_delivery_signer`` reads: ``webhook_signing.supported`` requires an
#: active key AND an origin that can serve https, because key discovery for our outbound
#: signatures runs through ``identity.brand_json_url`` and the pin fixes that to
#: ``^https://``. With no ``virtual_host`` and no ``SALES_AGENT_DOMAIN`` (which no
#: integration env sets), ``canonical_agent_url`` returns ``http://localhost:8080`` and
#: the RFC 9421 arm is unreachable — so every test in this file would grade the unsigned
#: branch while reading like it graded the signed one. The precedent is
#: ``tls_trust_root_tenant(netloc)`` (``tests/e2e/test_trust_root_e2e.py``).
_AGENT_HOST = "seller-webhook-boundary.example.com"


def _seed_tenant_with_key(env: BareIntegrationEnv) -> _Seeded:
    """Create the tenant/principal and mint their signing key through production.

    The repository handed back is the tenant-scoped ``SigningKeyRepository`` the
    signing layer takes, so the test gives production exactly the object production
    gets.
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

    ``signing_key_backed`` is THE single key-presence derivation; asserting through it
    rather than re-querying the table is what stops this suite from growing a second
    one. Without this control, "no RFC 9421 headers" would be equally explained by a
    keyless tenant, and every mode assertion below would be vacuous.
    """
    from src.core.signing.posture import signing_key_backed

    assert signing_key_backed(repo, now=now).signs is True, (
        "the tenant has no ACTIVE signing key at the instant under test, so every "
        "assertion about RFC 9421 mode below would hold for the wrong reason"
    )


# ---------------------------------------------------------------------------
# The seam, read the way a sender reads it
# ---------------------------------------------------------------------------

#: The four answers ``_headers_for`` can give, named. Exactly one of its three return
#: slots is ever non-empty (security.mdx @ v3.1.1 :1425), so reading all three and
#: naming the result is a total description of the arm that was selected — not a
#: guess derived from one of them.
_REFUSED = "refused"
_RFC9421 = "rfc9421"
_LEGACY_HMAC = "legacy-hmac"
_LEGACY_BEARER = "legacy-bearer"
_UNAUTHENTICATED = "unauthenticated"


def _selected_arm(scheme: str | None, credentials: str | None, *, signer: Any) -> str:
    """Which authentication arm the ONE seam selects for a stored registration.

    Composed of the two functions every sender reaches through
    ``deliver_webhook``/``adeliver_webhook`` — ``_authentication_or_refusal`` (does
    this stored pair validate against the pinned type at all) and ``_headers_for``
    (which arm does it select) — rather than re-implementing either. A stored row is
    therefore graded exactly as production grades it, with the delivery act removed:
    the claim under test is about the SELECTION, and a probe that also dialled would
    be grading the socket twice (group 3 does that, on the wire).

    They are module-private on purpose — the seam has no public non-delivering entry
    point, because production must never be able to ask "which arm?" without also
    delivering through it. Reaching them here is what keeps this a statement about
    the seam rather than about a factory the tests own.
    """
    from src.core.security.webhook_egress import _authentication_or_refusal, _headers_for
    from src.core.webhooks.delivery import WebhookDeliveryOutcome

    decided = _authentication_or_refusal(scheme, credentials)
    if isinstance(decided, WebhookDeliveryOutcome):
        return _REFUSED

    prepared, secret, sign = _headers_for(decided, dict(_JSON_CONTENT_TYPE), signer)
    if sign is not None:
        return _RFC9421
    if secret is not None:
        return _LEGACY_HMAC
    if "Authorization" in prepared:
        return _LEGACY_BEARER
    return _UNAUTHENTICATED


def _live_delivery_signer(seeded: _Seeded, now: datetime) -> Any:
    """The tenant's real RFC 9421 strategy, resolved through production.

    Non-vacuity for every row of the mode parametrization: a legacy registration that
    ignores a signer and a tenant that had no signer to ignore produce the SAME
    ``sign is None``, and only one of those is the claim. Asserting the strategy
    exists first is what tells them apart.
    """
    from src.core.signing.outbound import webhook_delivery_signer

    signer = webhook_delivery_signer(tenant_id=_TENANT_ID, repo=seeded.repo, now=now)
    assert signer is not None, (
        "production resolved NO RFC 9421 delivery strategy for a tenant holding an active "
        "signing key on a publishable origin — every 'not 9421' assertion below would then "
        "hold because there was nothing to sign with, not because the seam chose a legacy arm"
    )
    return signer


# ---------------------------------------------------------------------------
# Driving the production senders
# ---------------------------------------------------------------------------


def _protocol_notification_payload() -> McpWebhookPayload:
    """The real AdCP webhook envelope, carrying a non-ASCII, non-sorted ``result``.

    Built through the SDK's own ``create_mcp_webhook_payload`` — the builder
    ``src.core.webhooks.delivery.build_webhook_envelope`` uses — because
    ``send_notification`` takes the typed ``McpWebhookPayload`` and dumps it itself.
    A loose dict here would not reach the sender at all.
    """
    return create_mcp_webhook_payload(
        task_id="task_webhook_boundary",
        task_type=DELIVERY_PAYLOAD_TASK_TYPE,
        status="completed",
        result={
            "media_buy_id": _MEDIA_BUY_ID,
            "advertiser_name": _ADVERTISER,
            "adcp_version": "3.1.1",
        },
    )


def _attempt_protocol_notification(config: Any, payload: McpWebhookPayload) -> tuple[bool, list[CapturedWebhook]]:
    """Drive ``ProtocolWebhookService`` and report what became of the delivery.

    Returns the sender's own verdict alongside the captured POSTs, because a
    refusal is graded on BOTH: zero bytes on the wire, and a sender that says so.
    :func:`_send_protocol_notification` is the delivered-path narrowing of this.
    """
    from src.services.protocol_webhook_service import ProtocolWebhookService
    from tests.factories.webhook import WebhookTaskContextFactory

    with capture_outbound_webhooks() as captured:
        delivered = asyncio.run(
            ProtocolWebhookService().send_notification(
                config,
                payload,
                # The TYPED context, not a four-key dict. ``send_notification`` takes
                # ``task: WebhookTaskContext`` and uses it as given. Through the factory
                # rather than a literal: the dataclass requires all seven fields and
                # three files had spelled the same literal, which is what the duplication
                # ratchet refuses.
                WebhookTaskContextFactory(
                    task_type=DELIVERY_METADATA_TASK_TYPE,
                    tenant_id=_TENANT_ID,
                    principal_id=_PRINCIPAL_ID,
                    media_buy_id=_MEDIA_BUY_ID,
                ),
            )
        )

    return delivered, captured


def _send_protocol_notification(config: Any, payload: McpWebhookPayload) -> CapturedWebhook:
    """Deliver through ``ProtocolWebhookService`` — the already-async AdCP sender."""
    _delivered, captured = _attempt_protocol_notification(config, payload)
    return _exactly_one(captured, "media_buy delivery push notification")


def _send_delivery_report() -> CapturedWebhook:
    """Deliver through ``WebhookDeliveryService`` — the sync delivery-report sender.

    ``by_package`` is passed through verbatim into the AdCP payload, which is where
    the non-ASCII value enters this sender's body; the surrounding keys are emitted
    in construction order, not sorted.
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
    ``created`` as ``int(time.time())`` (``adcp/signing/signer.py`` :164), and with the
    ladder's backoff patched out all three signings land inside the SAME second. A
    "created does not go backwards" assertion is then true in the armed arm AND in an
    arm that signs once and reuses the signature, which is an assertion that cannot
    fail: the exact defect this test exists to grade, reproduced inside the instrument.

    Advancing the clock by the delay the seam asked for is what makes the assertion
    strict instead. The alternative — dropping the assertion — is cheaper and wrong:
    freshness is ``created`` as much as ``nonce``, and a stale ``created`` is what a
    receiver's window check refuses on a slow ladder.
    """

    def __init__(self, start: float) -> None:
        self.now = start

    def sleep(self, seconds: float) -> None:
        """Stand in for ``time.sleep`` — advance instead of blocking.

        At least a whole second per hop, because ``created`` is an integer number of
        seconds: a sub-second advance would round away and leave the three stamps
        equal again. The seam's real first backoff is 1s + jitter, so this
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

    * ``src.core.security.outbound_http.time.sleep`` — the SEAM's own backoff. The
      retry ladder lives there now (``send(..., max_attempts=3)``), not in the
      service, which is why this target and not the sender's module; it is the same
      target ``tests/harness/egress.py`` records the delivery envs mocking, so the
      seam suite and this one pin one implementation.
    * the ``time`` name inside ``adcp.signing.signer``, whose sole use is
      ``int(time.time())`` for ``created``. Replaced with a namespace exposing only
      that call, so nothing else in the process reads a fake clock.

    Nothing about the delivery path itself is patched: the service, the seam, the
    signer and the retry schedule all run for real, and only the socket
    (:func:`capture_outbound_webhooks`) and the clock are replaced.
    """
    from adcp.signing import signer as sdk_signer

    clock = _LadderClock(start=time.time())
    with (
        patch("src.core.security.outbound_http.time.sleep", clock.sleep),
        patch.object(sdk_signer, "time", SimpleNamespace(time=clock.time)),
    ):
        yield


def _send_delivery_report_over_a_retry_ladder() -> list[CapturedWebhook]:
    """Drive ``WebhookDeliveryService`` through refuse/refuse/accept, capturing all three.

    Entered at ``send_delivery_webhook`` — the PUBLIC seam, the same one
    :func:`_send_delivery_report` uses — never at the private ``_deliver_with_backoff``
    and never at ``outbound_http.send``. The claim is about where the signature is
    minted relative to the retry loop, so a test that reached inside the loop would be
    grading its own reach into it.
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

    ``Content-Digest`` is signed (the webhook profile pins ``cover_content_digest=True``),
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
# 1. The mode selector
# ---------------------------------------------------------------------------


class TestOneAuthModePerReceiver:
    """One seam picks the mode, and it keys on ``authentication`` being PRESENT."""

    @pytest.mark.parametrize(
        "authentication_type",
        ["HMAC-SHA256", "hmac-sha256", "Bearer", "bearer", "Basic", "basic"],
        ids=["hmac-canonical", "hmac-lower", "bearer-title", "bearer-lower", "basic-title", "basic-lower"],
    )
    def test_registration_with_authentication_never_signs_with_rfc9421(
        self, integration_db, signing_env, authentication_type
    ):
        """Any registration carrying ``authentication`` selects a NON-9421 arm.

        The bearer rows are the regression for the mode-selector defect: under the
        ``== "HMAC-SHA256"`` predicate they took the 9421 arm, and security.mdx
        :1466 says the receiver answers ``webhook_mode_mismatch``. The off-spelling
        rows (``hmac-sha256``, ``bearer``, ``Basic``, ``basic``) name schemes outside
        the pinned ``AuthenticationScheme`` enum and are REFUSED by the same seam
        rather than delivered — still not 9421, and graded end-to-end by
        :meth:`TestSignedBytesAreTheBytesSent.test_a_scheme_outside_the_pinned_enum_is_refused_not_downgraded`.
        What every row shares, and what this method states, is that a stored
        ``authentication`` value NEVER reaches the RFC 9421 arm.

        The signer is live and is passed on every row: ``_headers_for`` IGNORES it on
        both legacy arms, which is :1425's "never signed both ways" expressed as the
        shape of the return rather than as a rule someone must remember.
        """
        seeded = _seed_tenant_with_key(signing_env)
        now = just_after_provisioning()
        _key_is_live(seeded.repo, now)
        signer = _live_delivery_signer(seeded, now)

        arm = _selected_arm(authentication_type, _SECRET, signer=signer)

        assert arm != _RFC9421, (
            f"a receiver registered as authentication_type={authentication_type!r} was routed to the "
            "RFC 9421 arm; it will answer webhook_mode_mismatch (security.mdx @ v3.1.1 :1466). "
            "Mode selection keys on `authentication` being PRESENT (:1424), not on the value being HMAC"
        )

    def test_registration_without_authentication_signs_with_rfc9421(self, integration_db, signing_env):
        """No ``authentication`` and a live key -> the RFC 9421 arm.

        The positive half of the same switch. Without it the assertion above is
        satisfied by a seam that never selects the 9421 arm at all.
        """
        seeded = _seed_tenant_with_key(signing_env)
        now = just_after_provisioning()
        _key_is_live(seeded.repo, now)
        signer = _live_delivery_signer(seeded, now)

        arm = _selected_arm(None, None, signer=signer)

        assert arm == _RFC9421, (
            f"a receiver that registered no `authentication` was routed to the {arm!r} arm even "
            "though the tenant holds an active signing key — absence of `authentication` is the "
            "pinned schema's own selector for the RFC 9421 profile (security.mdx @ v3.1.1 :1424)"
        )

    def test_the_legacy_arms_are_reachable_and_distinct(self, integration_db, signing_env):
        """Each pinned scheme selects its OWN legacy arm, and neither is 9421.

        The control for the parametrization above: ``arm != _RFC9421`` is equally true
        of a seam that refused every registration it was given, which would make the
        six rows pass while no buyer was ever authenticated at all. Only the two
        spellings the pinned enum actually names can be asserted this way —
        ``AuthenticationScheme`` @ v3.1.1 is exactly ``["Bearer", "HMAC-SHA256"]``.
        """
        seeded = _seed_tenant_with_key(signing_env)
        now = just_after_provisioning()
        _key_is_live(seeded.repo, now)
        signer = _live_delivery_signer(seeded, now)

        assert _selected_arm("HMAC-SHA256", _SECRET, signer=signer) == _LEGACY_HMAC, (
            "an HMAC-SHA256 registration did not select the legacy HMAC arm — the stored credential "
            "never reaches prepare_signed_request and the buyer's registration is silently ignored"
        )
        assert _selected_arm("Bearer", _SECRET, signer=signer) == _LEGACY_BEARER, (
            "a Bearer registration did not select the legacy Bearer arm — no Authorization header is "
            "built and the buyer's registration is silently ignored"
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
        and a signer that signs correctly but whose headers are attached to nothing
        would satisfy the narrower version.
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

        Its body is serialized ONCE by ``prepare_signed_request`` and handed to
        ``asend`` as ``content=``, so the signed object and the transmitted object are
        one object. The non-ASCII value is what keeps that a real claim rather than a
        coincidence of two encoders agreeing.
        """
        seeded = _seed_tenant_with_key(signing_env)
        _key_is_live(seeded.repo, just_after_provisioning())
        config = _register_receiver(seeded)

        captured = _send_protocol_notification(config, _protocol_notification_payload())

        _assert_content_digest_covers_received_bytes(captured, _ADVERTISER)

    def test_delivery_report_digest_covers_the_bytes_received(self, integration_db, signing_env):
        """``webhook_delivery_service`` POSTs the bytes it signed.

        The vacuous-oracle case the design calls out: httpx's ``encode_json`` already
        matches the compact separators, so ONLY the non-ASCII ``by_package`` value can
        distinguish re-serialization from transmission of the signed bytes.
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
            ("Bearer", "authorization"),
        ],
        ids=["hmac-canonical", "bearer-title"],
    )
    def test_legacy_registration_is_authenticated_and_unsigned_on_the_wire(
        self, integration_db, signing_env, authentication_type, credential_header
    ):
        """The legacy leg of the same switch, graded on the wire rather than at the seam.

        :class:`TestOneAuthModePerReceiver` grades the arm selection; this grades its
        consequence for the receiver, which is what the buyer actually experiences.
        Two claims, and each one has failed in production:

        * the registered credential is ON the delivery — a registration this seller
          accepted must not be silently ignored at delivery time;
        * and NO RFC 9421 headers ride along — security.mdx @ v3.1.1 :1425 forbids
          signing one webhook both ways, and :1466 has the receiver answer
          ``webhook_mode_mismatch``.

        THE TENANT HOLDS A LIVE KEY, so ``delivery_signer_for_tenant`` resolves a real
        strategy and the sender passes it to the seam UNCONDITIONALLY. The absent
        ``signature-input`` is therefore the seam ignoring an available signer, not the
        absence of one — which is exactly the shape :1425 asks for.

        Only the PRESENCE of the credential header is pinned, not its exact value:
        which legacy header a scheme maps onto is the boundary's to decide, whereas
        "the buyer's registration was honoured at all" is the contract.

        THE TWO SPELLINGS PINNED HERE ARE THE PINNED ENUM'S OWN
        (``AuthenticationScheme`` @ v3.1.1 = ``["Bearer", "HMAC-SHA256"]``). This
        parametrization used to carry ``hmac-sha256``, ``bearer`` and ``basic``
        rows asserting the SAME delivered outcome; they moved to
        :meth:`test_a_scheme_outside_the_pinned_enum_is_refused_not_downgraded`
        when #1802's egress seam made an out-of-spec stored scheme a refusal
        rather than a delivery. Do not re-add them here — the obligation they
        carried ("the registration was not silently ignored") is stronger on the
        refusal, which says so out loud.
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

    @pytest.mark.parametrize(
        "authentication_type",
        ["hmac-sha256", "bearer", "basic"],
        ids=["hmac-lower", "bearer-lower", "basic"],
    )
    def test_a_scheme_outside_the_pinned_enum_is_refused_not_downgraded(
        self, integration_db, signing_env, authentication_type, caplog
    ):
        """A stored scheme the pin does not name yields NO delivery, and says so.

        ``AuthenticationScheme`` @ v3.1.1 is exactly ``["Bearer", "HMAC-SHA256"]``
        (``core/push-notification-config.json``, read off the pinned SDK), so a row
        holding ``hmac-sha256``, ``bearer`` or ``basic`` names an authentication this
        seller cannot conformantly produce. #1802's egress seam answers that with
        ``refused_auth``/``scheme_not_in_spec`` before anything is serialized —
        Epic D owner ruling #2, which ``_authentication_or_refusal`` now owns for
        every sender at once: "a buyer who asked for an authentication this seller
        cannot conformantly produce gets no delivery, rather than an unauthenticated
        POST it can neither verify nor attribute."

        These three rows are UNREACHABLE through any write path today — every one
        normalizes through the pinned model (``ValidatedWebhookRegistration
        .authentication_type`` reads ``schemes[0]`` off it), and the ingest gate
        refuses the lowercase spelling outright
        (``test_webhook_hmac_credentials_ingest_refusal.py``). They are seeded
        directly by the factory because the state they represent is a row written
        BEFORE that gate existed, and what happens to those rows is the thing worth
        pinning.

        THE ASSERTION IS THE ABSENCE PLUS THE ANNOUNCEMENT, and both halves are
        load-bearing. Zero captures alone is equally true of a sender that crashed;
        ``delivered is False`` alone is equally true of one that POSTed and got a
        500. The failure mode this rules out is the third option — a silent
        downgrade to an unauthenticated POST, which would show one capture with no
        credential header — and, since this tenant CAN sign, the fourth: a downgrade
        to an RFC 9421 delivery the buyer never registered for.
        """
        seeded = _seed_tenant_with_key(signing_env)
        _key_is_live(seeded.repo, just_after_provisioning())
        config = _register_receiver(seeded, authentication_type=authentication_type, authentication_token=_SECRET)

        with caplog.at_level(logging.ERROR, logger="src.core.security.webhook_egress"):
            delivered, captured = _attempt_protocol_notification(config, _protocol_notification_payload())

        assert captured == [], (
            f"a receiver stored with the out-of-spec scheme {authentication_type!r} was POSTed to anyway: "
            f"{[sorted(sent.headers.keys()) for sent in captured]}. The seam must refuse before serializing, "
            "not downgrade to an unauthenticated delivery the buyer can neither verify nor attribute"
        )
        assert delivered is False, (
            f"send_notification reported success for a {authentication_type!r} registration it refused to "
            "deliver — a caller reading the bool would record a webhook the buyer never received"
        )
        assert "scheme_not_in_spec" in caplog.text and authentication_type in caplog.text, (
            f"the refusal of scheme {authentication_type!r} was not announced as scheme_not_in_spec naming the "
            f"scheme, so the registration's owner has nothing to act on; log was:\n{caplog.text}"
        )


# ---------------------------------------------------------------------------
# 4. Signature freshness across a retry ladder
# ---------------------------------------------------------------------------


class TestSignatureFreshnessAcrossTheRetryLadder:
    """Every attempt at one event carries its OWN signature (lane L13, clause 2).

    The retry ladder lives in ``outbound_http.send``, which rebuilds the request and
    re-invokes the ``SignAttempt`` returned by ``_headers_for`` INSIDE the loop. That
    placement is the whole obligation: a receiver enforcing RFC 9421 replay protection
    rejects a second POST carrying a nonce it has already seen, and a receiver
    enforcing a ``created`` window rejects one whose stamp has aged out of it. A
    refactor that hoisted the signing above the loop — one signature reused for all
    three POSTs — would turn every retry into a replay rejection. It is graded here
    rather than in the seam's own suite because only a real sender can show that the
    signer it hands over is re-invoked.

    ONE LOOP, NAMED. This class drives ``WebhookDeliveryService``
    (``src/services/webhook_delivery_service.py``); ``_send_approval_webhook``
    (``src/services/order_approval_service.py``) stays a KNOWN-UNGRADED call site for
    signature freshness — one test, one sender.

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
        # arm (the mode selector above), and without it there would be no
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

        The SDK mints a fresh nonce per signing call, so distinctness is a direct
        statement that the ``SignAttempt`` was invoked per attempt. Reused, the second
        and third POSTs are byte-identical replays and a conformant receiver refuses
        them.
        """
        captured = self._ladder(signing_env)

        nonces = [signature_input_params(sent)["nonce"] for sent in captured]

        assert len(set(nonces)) == 3, (
            f"the {len(captured)} attempts at ONE event carried {len(set(nonces))} distinct nonce(s), "
            f"expected 3: {nonces}. A repeated nonce means one signature was minted and reused across "
            "the ladder, so every retry is a replay a receiver enforcing RFC 9421 replay protection "
            "rejects — the request must be rebuilt and re-signed per attempt"
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

        READ OUT OF THE JSON BODY, never a header, and that is load-bearing. The key
        is a REQUIRED PAYLOAD FIELD of the AdCP webhook document, not transport
        metadata: webhooks.mdx @ v3.1.1 :195 — "Every webhook payload carries a
        required ``idempotency_key`` … the canonical dedup field" — and :253 names
        THIS sender's events specifically ("for delivery-report data events such as
        scheduled, final, delayed and adjusted, ``notification_id`` is absent by
        design; dedupe the transport event with ``idempotency_key``"). Graded by
        ``dist/compliance/3.1.1/universal/webhook-emission.yaml`` step
        ``idempotency_key_presence``. No ``Idempotency-Key`` header reaches the wire
        at all, so a test reading a header collects three ``None`` values and
        ``len({None, None, None}) == 1`` passes under every mutation — including one
        that mints a fresh key per attempt.

        It must be merged into the body BEFORE the seam serializes, so it is inside
        the digest the signature covers; the signature stays fresh per attempt while
        the key stays constant across them. Those are different fields and only the
        key is stable by contract.
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
