"""Core Invariant (GH #1802): a webhook sender signs the bytes it sends.

A webhook sender must never hold a signer and a body serializer as two
independent decisions. The one correct pattern in this codebase
(``protocol_webhook_service.py``) serializes once, signs those exact bytes,
and transmits those exact bytes via ``content=``. The two senders pinned here
do not: each signs a re-serialization of the payload dict
(``json.dumps(payload, sort_keys=True, separators=(",", ":"))``, escaped
ASCII) and then hands the SAME dict to ``send(..., json=...)``, which lets
httpx serialize it independently a second time (insertion order,
``ensure_ascii=False``). The two serializations diverge in both key order and
byte encoding, so the signature never covers the bytes that cross the socket.

A receiver that verifies the HMAC over the bytes it actually received — the
only sound way to verify one — must reject every delivery from either sender
today. Both cases below recompute the sender's own signing formula, but over
``env.last_delivery.body`` (the raw bytes the origin actually received)
rather than over a re-serialization of the payload dict test-side — matching
the working pattern at
``tests/integration/test_protocol_webhook_egress.py::TestSignedBodyIntegrity``.
A test that recomputed from the payload dict instead would just reproduce the
sender's own bug and pass vacuously (see the existing, vacuous
``test_hmac_signature_valid_reproduces_from_payload`` in
``tests/integration/test_delivery_service_behavioral.py``, which does exactly
that).

Both cases are RED today. They must go GREEN only once each sender routes
through one shared serialize-once -> sign -> send(content=) function
(GH #1802's implementation), not by patching either recompute here.
"""

from __future__ import annotations

import pytest

from tests.harness import CircuitBreakerEnv, WebhookEnv
from tests.helpers import assert_signature_verifies_over_wire_body

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestWebhookDeliveryWithRetrySignedBodyIntegrity:
    """``deliver_webhook_with_retry`` (src/core/webhook_delivery.py) must sign what it sends.

    Before GH #1802: signs via ``WebhookAuthenticator.sign_payload``
    (line 99) and then calls ``send(..., json=delivery.payload, ...)``
    (line 131) — httpx re-serializes ``delivery.payload`` independently of
    what was signed. After: routes through
    ``src.core.security.webhook_egress.deliver_webhook``, which signs
    via ``adcp.sign_legacy_webhook`` and transmits its returned bytes via
    ``content=`` — spec headers only (``X-AdCP-Signature``/
    ``X-AdCP-Timestamp``; ``WebhookAuthenticator`` and its non-spec
    ``X-Webhook-*`` pair no longer exist).
    """

    def test_receiver_verifies_the_signature_over_the_body_it_received(self, integration_db):
        """The signed bytes and the transmitted bytes must be the same bytes.

        The payload carries two keys whose alphabetically-sorted order differs
        from insertion order, plus non-ASCII text — so the case grades a real
        divergence in both key order (sort_keys=True vs. httpx's insertion
        order) and byte encoding (escaped \\uXXXX vs. literal UTF-8), not a
        coincidental agreement.
        """
        secret = "shared-webhook-secret-thirty-two-plus"
        payload = {"zebra_field": "café ✓ 日本語", "apple_field": 1}

        with WebhookEnv() as env:
            env.set_http_status(200)

            delivered, _ = env.call_deliver(signing_secret=secret, payload=payload)

            assert delivered is True
            assert env.delivery_attempts == 1
            assert_signature_verifies_over_wire_body(env.last_delivery, secret)


class TestWebhookDeliveryServiceSignedBodyIntegrity:
    """``WebhookDeliveryService._deliver_with_backoff`` must sign what it sends.

    Before GH #1802: signs via ``_generate_hmac_signature`` (line 337)
    and then calls ``send(config.url, json=payload, ...)`` (line 538) — the
    same re-serialization bug as ``deliver_webhook_with_retry``, under the
    spec-correct ``X-ADCP-Signature`` header name (bare hex, no ``sha256=``
    prefix). After: routes through
    ``src.core.security.webhook_egress.deliver_webhook``, same as every
    other sender.

    No custom payload is needed here: production's own delivery payload
    (``send_delivery_webhook``) already has keys in an order that differs from
    its alphabetically-sorted form (``adcp_version``, ``notification_type``,
    ``is_adjusted``, ...), so the divergence is present on every call, not
    only on a specially-crafted one.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-07
    """

    def test_receiver_verifies_the_signature_over_the_body_it_received(self, integration_db):
        """WebhookDeliveryService signs the exact bytes it transmits.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-07
        """
        # No length requirement: the 32-char minimum this line used to cite was
        # deleted with the inline resolver (GH #1802, GH #1894) -- it
        # tested a column with no writers and had never once fired.
        secret = "buyer-shared-secret-padded-to-32ch"

        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            env.setup_default_data()
            env.make_webhook_config(auth_type="HMAC-SHA256", auth_token=secret)

            env.set_http_response(200)

            delivered = env.call_send(
                media_buy_id="mb_001",
                tenant_id="t1",
                principal_id="p1",
                impressions=5000,
                spend=250.0,
            )

            assert delivered is True
            assert env.delivery_attempts == 1
            assert_signature_verifies_over_wire_body(env.last_delivery, secret)
