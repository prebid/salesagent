"""The approval webhook, against a real origin and the real outbound transport.

Replaces ``test_webhook_notification_sent_on_success`` and
``test_webhook_retries_on_failure`` in ``tests/unit/test_order_approval_service.py``.
Both of those substituted ``httpx.Client`` and then read the call back off the
mock, which describes the transport ``_send_approval_webhook`` happens to use
rather than what it delivers: ``call_args[1]["json"]`` is the object the caller
passed, not the bytes that crossed the socket, and a mock's ``call_count`` is
the number of times a stand-in was invoked, not the number of requests a server
actually served.

Everything here is graded off ``OrderApprovalWebhookEnv``'s local origin, so the
assertions mean the same thing before and after the egress-seam migration —
which is the point: the transport symbol changes, the delivered bytes must not.

What is deliberately NOT graded here: the retry *spacing*. BR-RULE-029's
1s/2s/4s-plus-jitter schedule is graded once, in
``tests/integration/test_outbound_http.py`` through
``tests/helpers/backoff_assertions.py``. Re-asserting a delay per call site is
exactly the duplication the seam exists to delete.

One case is RED before the migration, on purpose:
the terminal-status retry case requires a 404 to cost exactly one request.
Today's hand-rolled loop retries every non-2xx, so it costs three. The seam
classifies retryable as ``{429, 500, 502, 503, 504}`` and treats everything else
— every other 4xx, every 3xx — as terminal, so adopting it is what turns that
case green. That behaviour change is named in the PR, not smuggled.

Why the signing obligations are graded HERE and not in BDD (GH #1802):
``_send_approval_webhook`` has exactly one production trigger — the GAM adapter
body at ``src/adapters/google_ad_manager.py:757``, reached only when the
synchronous ``approve_order`` returns False — and that trigger fires it on a
daemon thread, long after ``create_media_buy`` already returned its response to
the buyer. No request/response cycle is in flight at webhook time, so there is
no wire envelope for a BDD ``Then`` step to assert on; and every media-buy BDD
env mocks ``get_adapter``
(``MediaBuyCreateEnv.EXTERNAL_PATCHES["adapter"]``), so the sole caller never
executes there at all. The identical rationale is already written down for the
sibling delivery-time refusal at
``tests/bdd/features/local-egress-ssrf-refusal.feature:45-51`` — "no
request/response cycle, so no envelope to grade" — and this module is the
integration locus that precedent points at. Do not re-open the question by
adding a scenario that would have to fake the trigger to reach the behaviour.
"""

from __future__ import annotations

import pytest

from tests.factories import PushNotificationConfigFactory
from tests.harness.order_approval_webhook import OrderApprovalWebhookEnv
from tests.helpers import assert_delivered_unsigned, assert_signature_verifies_over_wire_body

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# The attempt budget ``_send_approval_webhook`` gives one webhook: three tries
# total, not three retries after the first. Written once here because three
# cases below assert against it and they must not drift apart.
MAX_ATTEMPTS = 3


def _bearer_config(env: OrderApprovalWebhookEnv, token: str = "test_token", *, scheme: str = "Bearer") -> None:
    """Store a bearer-auth ``PushNotificationConfig`` for the env's own endpoint.

    Production looks the row up by ``(tenant_id, principal_id, url, is_active)``,
    so the URL has to be the origin that is really listening — a row pointing
    anywhere else is invisible to the code under test and would make an
    "Authorization header" assertion grade nothing.

    ``scheme`` defaults to the SPEC spelling (``AuthenticationScheme =
    ["Bearer", "HMAC-SHA256"]`` @ pinned AdCP 3.1.1), which is what every writer
    in ``src/`` actually stores — ``media_buy_create`` persists ``schemes[0]``
    verbatim. The fixture used to say lowercase ``"bearer"`` because that is
    what ``order_approval_service`` compares against, so it graded the sender's
    private spelling instead of the rows the sender really receives
    (GH #1802). It is a PARAMETER rather than a constant because both
    spellings are rows a real buyer can produce — not because both authenticate.
    Only the canonical spelling does: a lowercase scheme is REFUSED rather than
    folded, graded by
    ``test_webhook_sender_auth_contract.py::test_a_lowercase_scheme_refuses_instead_of_being_folded``.
    """
    tenant, principal = env.setup_default_data()
    PushNotificationConfigFactory(
        tenant=tenant,
        principal=principal,
        url=env.webhook_url,
        authentication_type=scheme,
        authentication_token=token,
        is_active=True,
    )


def _hmac_config(env: OrderApprovalWebhookEnv, *, secret: str | None = None) -> None:
    """Store an ``HMAC-SHA256`` ``PushNotificationConfig`` for the env's own endpoint.

    The twin of :func:`_bearer_config`, and for the same reason: the URL must be
    the origin that is really listening or production's lookup misses the row.

    ``secret`` is the AdCP ``push_notification_config.authentication.credentials``
    value and lands in ``authentication_token`` — the column every writer in
    ``src/`` populates, and the one ``order_approval_service`` reads since
    GH #1802. ``secret=None`` leaves the credential genuinely absent
    (``PushNotificationConfigFactory`` declares no default for it), which is the
    row a buyer who selected HMAC-SHA256 without supplying a secret produces.

    Both the signing case and the refusal case go through here because they
    differ in exactly one value; a second inlined factory block would be the
    parameter-substitution duplication CLAUDE.md treats as a defect.
    """
    tenant, principal = env.setup_default_data()
    PushNotificationConfigFactory(
        tenant=tenant,
        principal=principal,
        url=env.webhook_url,
        authentication_type="HMAC-SHA256",
        authentication_token=secret,
        is_active=True,
    )


class TestDeliveredPayload:
    """What the buyer's endpoint actually receives when an approval completes."""

    def test_payload_carries_the_approval_result(self, integration_db):
        """Event, media buy, status and the GAM polling outcome cross the socket.

        ``order_id`` and ``attempts`` are the two fields that only exist when the
        GAM poll produced them, so they are the ones a payload-build regression
        would drop silently — the webhook is the ONLY outward signal that an
        approval finished, and nothing downstream re-derives them.
        """
        with OrderApprovalWebhookEnv() as env:
            env.setup_default_data()
            env.set_http_status(200)

            env.call_send_approval_webhook(
                media_buy_id="mb_123",
                status="approved",
                message="Order approved successfully",
                order_id="12345",
                attempts=3,
            )

            assert env.delivery_attempts == 1
            request = env.last_delivery
            assert request.method == "POST"
            assert request.path == "/webhook"

            payload = request.json()
            assert payload["event"] == "order_approval_update"
            assert payload["media_buy_id"] == "mb_123"
            assert payload["status"] == "approved"
            assert payload["order_id"] == "12345"
            assert payload["attempts"] == 3


class TestStoredCredential:
    """The ``Authorization`` header is derived from the stored PushNotificationConfig.

    Both directions are graded: a stored bearer token must reach the wire, and
    the absence of a row must produce no ``Authorization`` header at all. The
    second half is where a stored-credential regression would hide — the deleted
    unit test covered it only incidentally, by running with no row.
    """

    def test_bearer_token_from_the_stored_config_reaches_the_wire(self, integration_db):
        """A bearer row becomes ``Authorization: Bearer <token>`` on the request."""
        with OrderApprovalWebhookEnv() as env:
            _bearer_config(env, token="test_token-padded-to-the-pinned-32-char-min")
            env.set_http_status(200)

            env.call_send_approval_webhook(status="approved")

            assert env.delivery_attempts == 1
            assert env.last_delivery.headers["Authorization"] == "Bearer test_token-padded-to-the-pinned-32-char-min"

    def test_a_lowercase_scheme_refuses_instead_of_being_folded(self, integration_db):
        """A row storing ``"bearer"`` does NOT get an ``Authorization`` header — it refuses.

        This asserted the opposite until the owner collapsed the vocabulary to the
        pinned, case-sensitive ``AuthenticationScheme``. Lowercase rows are not
        hypothetical — the A2A ``setTaskPushNotificationConfig`` handler stores
        ``params.authentication.scheme`` verbatim from a free-form protobuf string
        with no enum guarding that path — and folding them was how they kept
        delivering. They are now MIGRATED instead: the owner re-registers with a
        scheme the spec defines, and until then the row does not deliver.

        Kept as a case rather than deleted because the folding repair is the
        tempting one. It is invisible, it makes the row work, and RFC 7235 §2.1
        licenses case-insensitive scheme names for HTTP. What it costs is a single
        answer to "which scheme is this row?", and the cost was three senders
        comparing three different spellings of one fact.
        """
        with OrderApprovalWebhookEnv() as env:
            _bearer_config(env, token="test_token-padded-to-the-pinned-32-char-min", scheme="bearer")
            env.set_http_status(200)

            env.call_send_approval_webhook(status="approved")

            assert env.delivery_attempts == 0, (
                f"a stored 'bearer' row produced {env.delivery_attempts} delivery attempt(s) — "
                f"the scheme is not a member of the pinned enum, so the seam must refuse "
                f"pre-flight without opening a socket"
            )

    def test_no_stored_config_sends_no_authorization_header(self, integration_db):
        """With no active row the request still goes out — unauthenticated.

        Not "no request": production deliberately delivers without auth rather
        than refusing, so the assertion is on the header's absence on a request
        that provably arrived.
        """
        with OrderApprovalWebhookEnv() as env:
            env.setup_default_data()
            env.set_http_status(200)

            env.call_send_approval_webhook(status="approved")

            assert env.delivery_attempts == 1
            assert "Authorization" not in env.last_delivery.headers


class TestHmacSigning:
    """A ``HMAC-SHA256``-configured row must sign the webhook it sends.

    GH #1802: ``_approval_webhook_headers`` has bearer/basic/token
    branches but no HMAC-SHA256 branch at all, and ``_post_approval_webhook``
    hardcodes ``secret=None`` on every call -- so a config that ASKS for
    HMAC-SHA256 signing gets silent unsigned delivery instead, with no error.

    GH #1802 moves the secret to the column a real row actually
    carries. AdCP 3.1.1 puts the shared secret in
    ``push_notification_config.authentication.credentials``, which every writer
    in ``src/`` persists to ``authentication_token``; ``webhook_secret`` has
    zero writers anywhere in ``src/``. The fixture used to populate
    ``webhook_secret`` because that is the column the sender reads, so it
    graded a row no buyer can create -- while every row a buyer CAN create took
    the sender's "no secret stored" refusal branch. Since 47n9.20 the sender
    takes its secret from ``authentication_token`` through the egress seam.

    Both halves of the invariant are graded here (GH #1802): a
    HMAC-SHA256 row either goes out carrying a signature that verifies over the
    exact bytes that crossed the socket, or it does not go out. There is no
    third outcome in which an unsigned request reaches a receiver that asked to
    be able to verify it.
    """

    def test_hmac_sha256_config_signs_the_wire_body(self, integration_db):
        """A stored HMAC-SHA256 row produces a verifiable signature over the
        exact bytes that crossed the socket -- not a re-serialization of the
        payload dict, per the wire-byte pattern GH #1802 established.
        """
        secret = "s" * 32  # Meets the 32-char minimum every HMAC secret in this suite uses.

        with OrderApprovalWebhookEnv() as env:
            _hmac_config(env, secret=secret)
            env.set_http_status(200)

            env.call_send_approval_webhook(status="approved")

            assert env.delivery_attempts == 1
            assert_signature_verifies_over_wire_body(env.last_delivery, secret)

    def test_hmac_sha256_without_credentials_delivers_nothing(self, integration_db):
        """A row asking for HMAC-SHA256 with no credential stored sends NOTHING.

        The origin answers 200 deliberately. A destination that would have
        ACCEPTED the delivery is what makes zero hits mean "the sender refused",
        rather than "the request went out and failed on arrival" — the reading
        an origin programmed to reject would leave open.

        Zero is discriminating, not merely small. When the
        ``PushNotificationConfig`` lookup in ``_send_approval_webhook`` finds no
        row at all, production falls through to ``Unauthenticated`` and
        DELIVERS, which costs exactly one hit
        (``TestStoredCredential::test_no_stored_config_sends_no_authorization_header``
        pins that count). So 0 separates "refused because the delivery would
        have had to go unsigned" from "the row was never found" — without that
        distinction this assertion would pass just as happily against a lookup
        that silently missed, and would be grading nothing.

        The obligation itself: a buyer who asked for HMAC-SHA256 will reject an
        unsigned POST, so sending one is strictly worse than sending none — it
        is an unauthenticated request to a third-party endpoint that no receiver
        can attribute to us (GH #1802/47n9.20).
        """
        with OrderApprovalWebhookEnv() as env:
            _hmac_config(env, secret=None)
            env.set_http_status(200)

            env.call_send_approval_webhook(status="approved")

            assert env.delivery_attempts == 0


class TestSigningIsGatedByTheScheme:
    """Only a row that ASKED for HMAC-SHA256 gets a signature.

    The sibling sender ``webhook_delivery_service`` shows what the other answer
    costs (GH #1802): signing driven by "is a credential present" rather
    than "is the scheme HMAC-SHA256" starts signing rows that stored a bearer
    token, and a receiver expecting a plain bearer POST sees headers it did not
    ask for. Both non-HMAC paths are graded, because a regression in the gate
    would show up on whichever one the change happened to touch.
    """

    def test_a_bearer_row_is_delivered_unsigned(self, integration_db):
        """A stored bearer credential must not be pressed into service as a signing key."""
        with OrderApprovalWebhookEnv() as env:
            _bearer_config(env, token="test_token-padded-to-the-pinned-32-char-min")
            env.set_http_status(200)

            env.call_send_approval_webhook(status="approved")

            assert_delivered_unsigned(env)

    def test_a_row_less_delivery_is_unsigned(self, integration_db):
        """With no config row at all there is no scheme, so there is nothing to sign with.

        The companion in ``TestStoredCredential`` grades that this same request
        carries no ``Authorization``; this grades the other header a signing
        regression would add to it.
        """
        with OrderApprovalWebhookEnv() as env:
            env.setup_default_data()
            env.set_http_status(200)

            env.call_send_approval_webhook(status="approved")

            assert_delivered_unsigned(env)


class TestRetryClassification:
    """Which failures are worth trying again, counted on a server that really ran.

    ``origin.hits`` is the only honest grade for a retry policy: "it was retried
    twice" and "it was not retried at all" are both claims about how many
    requests reached a destination, and only a destination can count them.
    """

    def test_a_transient_status_is_retried_until_it_succeeds(self, integration_db):
        """``503, 503, 200`` costs three requests and ends delivered.

        503 rather than the deleted unit test's 500 — both are in the seam's
        retryable set, so the obligation is identical and 503 says "transient"
        without also implying the origin crashed.
        """
        with OrderApprovalWebhookEnv() as env:
            env.setup_default_data()
            env.set_http_sequence([(503, "unavailable"), (503, "unavailable"), (200, "ok")])

            env.call_send_approval_webhook(status="approved")

            assert env.delivery_attempts == MAX_ATTEMPTS

    def test_a_terminal_status_is_not_retried(self, integration_db):
        """A 404 costs exactly ONE request.

        The endpoint said the resource does not exist; posting the same body to
        the same URL twice more cannot change that answer, and doing so triples
        the load a misconfigured buyer endpoint takes from us. Retryable is
        ``{429, 500, 502, 503, 504}`` and 404 is not in it.

        RED until ``_send_approval_webhook`` adopts the egress seam: the
        hand-rolled loop it has today retries every non-2xx, so this currently
        counts three.
        """
        with OrderApprovalWebhookEnv() as env:
            env.setup_default_data()
            env.set_http_status(404, "not found")

            env.call_send_approval_webhook(status="approved")

            assert env.delivery_attempts == 1


class TestExhaustedDeliveryIsSilent:
    """A webhook that cannot be delivered must not escape into the caller."""

    def test_a_dropped_connection_exhausts_the_budget_and_raises_nothing(self, integration_db):
        """Every attempt is answered with a closed socket; the call still returns.

        ``_send_approval_webhook`` runs on the approval daemon thread, reached
        only from ``_mark_approval_complete`` / ``_mark_approval_failed``, and
        both ignore its return value. An exception escaping it would kill that
        thread after the approval had already completed — so "delivery failed"
        has to stay a log line. Reaching the assertion below at all is that
        obligation; the assertion itself pins that the failure was genuinely
        exhausted rather than abandoned after one try.

        A dropped connection rather than a failing status, so this grades the
        transport-exception path — the one the deleted unit tests never reached.
        """
        with OrderApprovalWebhookEnv() as env:
            env.setup_default_data()
            env.set_http_error()

            env.call_send_approval_webhook(status="failed", message="GAM order rejected")

            assert env.delivery_attempts == MAX_ATTEMPTS
