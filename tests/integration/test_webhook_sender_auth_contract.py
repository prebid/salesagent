"""One auth contract for every webhook sender, graded against real origins.

GH #1802 (GH #1893, #1894). ``GH #1802`` converged the
TRANSPORT — every sender dials through ``src.core.security.outbound_http`` and
``json=`` is unreachable from a signing sender. What did not converge is the
AUTH DECISION above it: three senders still answer "is this delivery signed,
and with what" three different ways, and two of them answer it wrongly.

The contract, stated once, is the one ``order_approval_service`` already keeps
(GH #1802, graded in ``tests/integration/test_order_approval_webhook.py``):

1. The stored ``(authentication_type, authentication_token)`` pair becomes an
   auth decision in exactly ONE place — ``deliver_webhook``/``adeliver_webhook``.
2. A row that asked for ``HMAC-SHA256`` and has no usable secret DELIVERS
   NOTHING. Never unsigned. A buyer who asked for a signature will reject an
   unsigned POST, so sending one is strictly worse than sending none.
3. Signing is gated by the SCHEME, not by "is some credential lying around".
4. Only the CANONICAL spelling authenticates. A non-canonical one is refused,
   not folded -- probed: ``"bearer"`` returns ``refused_auth`` /
   ``scheme_not_in_spec`` at the seam, and ingest rejects it outright. Both
   spellings are nonetheless rows a real buyer can produce (the A2A
   ``setTaskPushNotificationConfig`` handler stores
   ``params.authentication.scheme`` verbatim from a free-form protobuf string;
   ``media_buy_create`` stores the pinned enum spelling
   ``AuthenticationScheme = ["Bearer", "HMAC-SHA256"]`` @ AdCP 3.1.1), which is
   why the refusal is graded rather than assumed impossible.

Every case here was RED until each sender routed its decision through the egress
seam — ``deliver_webhook``/``adeliver_webhook`` in
``src.core.security.webhook_egress`` (GH #1802). They went green by
CONVERGING on that seam, not by repairing the inline copies — repairing them in
place would have made each the fourth divergent copy, which is the disease
itself.

Why zero-hits is the discriminating assertion and not merely a small number:
each origin below is programmed to answer 200. A destination that would have
ACCEPTED the delivery is what makes "no request arrived" mean *the sender
refused*, rather than *the request went out and failed on arrival* — the
reading an origin programmed to reject would leave open. The same rationale is
written at ``test_order_approval_webhook.py::TestHmacSigning``.

Why these are integration tests and not BDD scenarios: neither sender runs
inside a request/response cycle. ``ProtocolWebhookService.send_notification``
is driven by the delivery scheduler and ``WebhookDeliveryService`` by the
delivery poller, both after the buyer's call has already returned — there is no
wire envelope for a ``Then`` step to assert on. The identical rationale is
already recorded for the sibling senders at
``tests/bdd/features/local-egress-ssrf-refusal.feature:45-51`` and in
``test_order_approval_webhook.py``'s module docstring.
"""

from __future__ import annotations

import pytest

from tests.harness import CircuitBreakerEnv, ProtocolWebhookEnv
from tests.helpers import assert_delivered_unsigned, assert_signature_verifies_over_wire_body

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# The pinned AdCP 3.1.1 enum spelling (``AuthenticationScheme``), which is what
# every writer in ``src/`` actually persists. Written as a constant because a
# regression that changed the spelling production compares against must fail
# these cases rather than be silently re-typed into them.
HMAC_SCHEME = "HMAC-SHA256"
BEARER_SCHEME = "Bearer"

# Any credential a buyer might store. There is deliberately no length
# requirement to satisfy: the 32-char strength gate was deleted with the inline
# resolver (GH #1802) — it tested a column with no writers, so it had
# never once fired, and re-pointing it at authentication_token would have taken
# short-credential buyers from "delivered" to "not delivered at all".
STRONG_SECRET = "buyer-shared-secret-padded-to-the-pinned-32-char-min"


class TestProtocolWebhookServiceRefusesUnsignedHmac:
    """``protocol_webhook_service`` refuses rather than delivering unsigned (GH #1893).

    ``send_notification`` always resolved through the seam, but a
    missing-secret decision used to fall through to an unsigned delivery:
    the buyer asked for a signature and received none, with no error on any
    surface. That is the failure mode the sibling structural guard cannot see —
    calling the resolver and then dropping one of its answers — which is why
    ``test_architecture_no_inline_webhook_auth_resolution`` exists and why
    these two cases are the behavioural half of it.
    """

    async def test_hmac_without_credentials_delivers_nothing(self, integration_db):
        """An HMAC-SHA256 row with no credential stored sends NOTHING.

        Before the fix this resolved a missing-secret decision and then proceeded to
        ``prepare_signed_request(payload, None, headers)``, which serializes
        without signing and delivers — one hit, unsigned.
        """
        with ProtocolWebhookEnv() as env:
            env.set_http_status(200)
            config = env.make_config(authentication_type=HMAC_SCHEME, authentication_token=None)

            delivered = await env.send(config=config)

            assert env.delivery_attempts == 0
            assert delivered is False, "a refused delivery must not report success to its caller"

    async def test_hmac_with_credentials_still_signs(self, integration_db):
        """The refusal must not swallow the case that CAN be signed.

        Guards the obvious over-correction: refusing on the scheme rather than
        on the missing credential would stop signing every real HMAC row.
        """
        with ProtocolWebhookEnv() as env:
            env.set_http_status(200)
            config = env.make_config(authentication_type=HMAC_SCHEME, authentication_token=STRONG_SECRET)

            delivered = await env.send(config=config)

            assert delivered is True
            assert env.delivery_attempts == 1
            assert_signature_verifies_over_wire_body(env.last_delivery, STRONG_SECRET)


class TestWebhookDeliveryServiceResolvesAuthThroughTheResolver:
    """``webhook_delivery_service`` resolves auth through the resolver (GH #1894).

    ``_deliver_with_backoff`` used to resolve auth inline and got it wrong four
    ways at once; it was the sole debt entry in
    ``test_architecture_no_inline_webhook_auth_resolution``'s allowlist, and
    closing #1894 removed that entry. Each case below grades one of the four
    defects, so a regression on any single one fails on its own rather than
    hiding behind the other three.
    """

    def test_hmac_row_signs_from_the_column_writers_populate(self, integration_db):
        """Defect 3: the secret comes from ``authentication_token``, not ``webhook_secret``.

        AdCP 3.1.1 puts the shared secret in
        ``push_notification_config.authentication.credentials``, which every
        writer in ``src/`` persists to ``authentication_token``.
        ``webhook_secret`` has ZERO writers anywhere in ``src/``, so today's
        signing branch is unreachable for any row a buyer can actually create.

        Before the fix ``getattr(config, "webhook_secret", None)`` was None for
        this row, so the delivery went out unsigned.
        """
        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            env.setup_default_data()
            env.make_webhook_config(auth_type=HMAC_SCHEME, auth_token=STRONG_SECRET)
            env.set_http_response(200)

            delivered = env.call_send(tenant_id="t1", principal_id="p1")

            assert delivered is True
            assert env.delivery_attempts == 1
            assert_signature_verifies_over_wire_body(env.last_delivery, STRONG_SECRET)

    def test_hmac_row_without_credentials_delivers_nothing(self, integration_db):
        """Defect 1: an ABSENT secret refuses; it is never downgraded to unsigned.

        This sender used to discard the secret and proceed UNSIGNED at WARNING
        level — the same quiet failure GH #1802 refused to commit one
        file over, and the reason a missing secret is a distinct refusal
        rather than a ``None``.

        ABSENT is one of two refusals, not the only one: a credential SHORTER
        than the pinned 32 characters is refused too. Probed directly, a 31-character
        credential returns ``refused_auth`` / ``credentials_too_short`` while a
        32-character one resolves to a signed ``Authentication``.

        An earlier version of this docstring claimed the length check "is gone,
        and a short credential now SIGNS", citing a test that exists nowhere. It
        stated the opposite of what the seam does, and the citation could not be
        followed to find that out.
        """
        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            env.setup_default_data()
            env.make_webhook_config(auth_type=HMAC_SCHEME, auth_token=None)
            env.set_http_response(200)

            delivered = env.call_send(tenant_id="t1", principal_id="p1")

            assert env.delivery_attempts == 0
            assert delivered is False, "a refused delivery must not report success to its caller"

    def test_a_bearer_row_is_delivered_unsigned(self, integration_db):
        """Defect 2: signing is gated by the SCHEME, not by a truthy credential.

        This row HAS a credential — ``authentication_token`` is set, and it is
        the very column the HMAC branch signs from. What must keep it unsigned is
        the SCHEME saying Bearer. A sender that asks "is a credential present"
        instead of "did this row ask for HMAC-SHA256" attaches HMAC headers to a
        receiver expecting a plain bearer POST.

        The stronger form of the original case: that one relied on a stray
        ``webhook_secret``, which is no longer reachable at all now that the
        column is abandoned, so it could no longer fail for the right reason.
        """
        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            env.setup_default_data()
            env.make_webhook_config(
                auth_type=BEARER_SCHEME, auth_token="buyer-bearer-token-padded-to-the-pinned-32-char-min"
            )
            env.set_http_response(200)

            env.call_send(tenant_id="t1", principal_id="p1")

            assert_delivered_unsigned(env)

    def test_a_spec_cased_bearer_row_carries_the_authorization_header(self, integration_db):
        """Defect 4: the bearer branch compares a case no writer produces.

        Production compares ``config.authentication_type == "bearer"``. The
        pinned enum is ``["Bearer", "HMAC-SHA256"]`` and ``media_buy_create``
        persists ``schemes[0]`` verbatim, so the branch never fires for a
        protocol-registered config — those buyers get no ``Authorization``
        header at all.

        Before the fix: no ``Authorization`` header on the wire at all.
        """
        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            env.setup_default_data()
            env.make_webhook_config(
                auth_type=BEARER_SCHEME, auth_token="buyer-bearer-token-padded-to-the-pinned-32-char-min"
            )
            env.set_http_response(200)

            env.call_send(tenant_id="t1", principal_id="p1")

            assert env.delivery_attempts == 1
            assert (
                env.last_delivery.headers["Authorization"]
                == "Bearer buyer-bearer-token-padded-to-the-pinned-32-char-min"
            )

    def test_a_lowercase_scheme_refuses_instead_of_being_folded(self, integration_db):
        """A ``bearer`` row does NOT authenticate as ``Bearer`` — it refuses.

        This asserted the opposite until the owner collapsed the vocabulary to the
        pinned, case-sensitive ``AuthenticationScheme``. The A2A
        ``setTaskPushNotificationConfig`` handler stores the scheme verbatim from a
        free-form protobuf string with no enum guard, so lowercase rows do exist in
        production — they are now MIGRATED (re-registered by their owner) rather
        than folded on the way out.

        Kept as a case rather than deleted because the folding repair is the
        tempting one: it is invisible, it makes the row work, and RFC 7235 §2.1
        even licenses it for HTTP. What it costs is a single answer to "which
        scheme is this row?" — the divergence that had three senders comparing
        three spellings of one fact.
        """
        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            env.setup_default_data()
            env.make_webhook_config(
                auth_type="bearer", auth_token="buyer-bearer-token-padded-to-the-pinned-32-char-min"
            )
            env.set_http_response(200)

            delivered = env.call_send(tenant_id="t1", principal_id="p1")

            assert delivered is False, (
                "a stored 'bearer' row reported delivery success — the pinned enum is "
                "case-sensitive, so this scheme is not a member and must refuse"
            )
            assert env.delivery_attempts == 0, (
                f"the refusal dialled anyway ({env.delivery_attempts} attempt(s)) — the scheme "
                f"decision is pre-flight, so no socket should have been opened"
            )
