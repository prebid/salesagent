"""Integration behavioral tests for UC-004 webhook delivery (deliver_webhook_with_retry).

Delivery runs against a REAL local HTTP origin (``WebhookEnv``): the endpoint
answers with the status a test programmed, and the assertions read what actually
arrived — how many requests, with which headers, carrying which bytes. Only
``time.sleep`` is mocked, so the retry schedule stays observable without waiting
for it; nothing about the outbound transport is patched, which is what keeps
these tests indifferent to whether delivery is implemented with ``requests`` or
with the egress seam. DB operations for delivery record tracking are real.

Each test targets exactly one obligation ID and follows the 6 hard rules.
"""

from __future__ import annotations

import pytest

from tests.helpers.backoff_assertions import assert_backoff_schedule

# A stall the caller's own clock gives up on. Both numbers are as small as a
# real socket allows: the timeout is what production is told to enforce, and the
# stall must outlast it by enough that a loaded CI box cannot answer in time.
_TIMEOUT_SECONDS = 1
_STALL_SECONDS = 1.5

# The cloud-metadata address: production's URL policy refuses it outright, which
# is what makes "no request left the process" provable rather than configured.
_METADATA_URL = "http://169.254.169.254/latest/meta-data/"

# A hostname that cannot resolve. ``.invalid`` is reserved by RFC 6761 exactly so
# that it never does, which is what makes "DNS-dead customer endpoint" a fact of
# the address and not of the box the suite happens to run on.
_UNRESOLVABLE_URL = "http://webhook-endpoint-does-not-exist.invalid/webhook"

# The key set of the ``(bool, dict)`` result on every failure arm. Pinned rather
# than left to whatever the recorder happens to build: three call sites in
# ``src/services/slack_notifier.py`` read ``result["attempts"]`` and
# ``result.get("error")`` off these dicts, so the shape is a caller contract even
# though no caller reads the rest of it.
_FAILURE_RESULT_KEYS = frozenset({"delivery_id", "status", "attempts", "response_code", "error"})

# The success arm carries no ``error`` and does carry the total wall time.
_SUCCESS_RESULT_KEYS = frozenset({"delivery_id", "status", "attempts", "response_code", "duration"})

# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-01
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhookDeliveryHappyPath:
    """Scheduled webhook delivery happy path — POST with signed payload.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-01
    """

    def test_webhook_sends_signed_payload(self, integration_db):
        """Webhook delivery sends POST to configured URL with HMAC-signed payload.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-01
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(200)

            success, result = env.call_deliver(
                webhook_url=env.webhook_url,
                payload={
                    "media_buy_id": "mb_001",
                    "impressions": 5000,
                    "spend": 250.0,
                    "notification_type": "scheduled",
                },
                signing_secret="test-secret-key-padded-to-thirty-two",
                max_retries=1,
            )

            assert success is True
            assert result["status"] == "delivered"

            # Verify POST was called with correct URL
            # Verify the request reached the configured endpoint
            assert env.delivery_attempts == 1
            assert env.last_delivery.path == "/webhook"

            # Verify HMAC signature headers were added. Spec header names
            # (X-AdCP-Signature/X-AdCP-Timestamp, from adcp.sign_legacy_webhook
            # via the shared deliver_webhook seam) since GH #1802 —
            # the non-spec X-Webhook-* pair no longer exists.
            sent_headers = env.last_delivery.headers
            assert "X-AdCP-Signature" in sent_headers
            assert "X-AdCP-Timestamp" in sent_headers

            # Verify payload was sent
            sent_payload = env.last_delivery.json()
            assert sent_payload["media_buy_id"] == "mb_001"
            assert sent_payload["notification_type"] == "scheduled"


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-07
#
# Formerly TestWebhookHmacSha256Signing here, unit-testing the deleted
# WebhookAuthenticator.sign_payload directly. GH #1802 deleted that
# class (dead in production; its only production caller path never set
# signing_secret) and re-homed this obligation onto a byte-verifying test:
# tests/integration/test_webhook_sender_signed_body_integrity.py::
# TestWebhookDeliveryServiceSignedBodyIntegrity, which proves the signature
# verifies against the actual wire bytes rather than only asserting header
# presence/prefix.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-08
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhookBearerTokenAuth:
    """Webhook delivery with Bearer token authentication.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-08
    """

    def test_bearer_token_sent_in_authorization_header(self, integration_db):
        """Bearer token is forwarded in Authorization header when set by caller.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-08
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(200)

            success, result = env.call_deliver(
                webhook_url=env.webhook_url,
                payload={"media_buy_id": "mb_001"},
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer test-bearer-token-xyz",
                },
                max_retries=1,
            )

            assert success is True
            assert result["status"] == "delivered"

            sent_headers = env.last_delivery.headers
            assert sent_headers["Authorization"] == "Bearer test-bearer-token-xyz"


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-11
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhookOnlyActiveMediaBuys:
    """Only active media buys trigger webhook delivery.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-11
    """

    def test_paused_media_buy_webhook_rejected(self, integration_db):
        """Webhook delivery should be rejected for paused media buys.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-11
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(200)

            success, result = env.call_deliver(
                webhook_url=env.webhook_url,
                payload={"media_buy_id": "mb_paused", "status": "paused"},
                max_retries=1,
            )

            assert success is False, "Webhook should not be delivered for paused media buy"


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-12
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhookEndpoint2xxAcknowledgment:
    """Endpoint acknowledges with 2xx — successful delivery recorded.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-12
    """

    def test_2xx_response_records_successful_delivery(self, integration_db):
        """200 OK from buyer endpoint records delivery as successful.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-12
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(200)

            success, result = env.call_deliver(
                webhook_url=env.webhook_url,
                payload={"media_buy_id": "mb_001", "impressions": 5000},
                max_retries=1,
            )

            assert success is True
            assert result["status"] == "delivered"
            assert result["response_code"] == 200
            assert result["attempts"] == 1


# ---------------------------------------------------------------------------
# UC-004-EXT-G-01
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhook503RetryBackoff:
    """Tests that a 503 webhook endpoint triggers retries with exponential backoff.

    Covers: UC-004-EXT-G-01
    """

    def test_503_triggers_retries_with_exponential_backoff(self, integration_db):
        """When a webhook returns 503, the system retries with exponential backoff.

        Covers: UC-004-EXT-G-01
        """

        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(503, "Service Unavailable")

            success, result = env.call_deliver(max_retries=4, timeout=10)

            assert success is False
            assert result["status"] == "failed"
            assert result["attempts"] == 4
            assert result["response_code"] == 503
            assert env.delivery_attempts == 4
            assert env.mock["sleep"].call_count == 3
            # The seam's schedule now, jitter included, graded by the one helper
            # that owns BR-RULE-029 rather than by a hand-written list of exact calls.
            assert_backoff_schedule([float(c.args[0]) for c in env.mock["sleep"].call_args_list], jitter=None)

    def test_503_no_backoff_after_final_attempt(self, integration_db):
        """No sleep occurs after the last attempt — only between attempts.

        Covers: UC-004-EXT-G-01
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(503, "Service Unavailable")

            env.call_deliver(max_retries=4)

            assert env.mock["sleep"].call_count == 3
            assert env.delivery_attempts == 4

    def test_503_then_success_stops_retrying(self, integration_db):
        """If a retry succeeds, no further retries or backoff occur.

        Covers: UC-004-EXT-G-01
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_sequence([(503, "Service Unavailable"), (200, "OK")])

            success, result = env.call_deliver(max_retries=4)

            assert success is True
            assert result["status"] == "delivered"
            assert result["attempts"] == 2
            assert env.delivery_attempts == 2
            assert env.mock["sleep"].call_count == 1
            assert_backoff_schedule([float(c.args[0]) for c in env.mock["sleep"].call_args_list], jitter=None)

    # Graduated (GH #1802): the module is on the jittered egress seam, so
    # BR-RULE-029's "+ jitter" is real here now. The xfail this replaces was strict
    # and blamed the old exact 2**attempt schedule.
    def test_backoff_includes_jitter(self, integration_db):
        """Backoff delays should include jitter to prevent thundering herd.

        Covers: UC-004-EXT-G-01
        """
        from tests.harness import WebhookEnv
        from tests.helpers.backoff_assertions import assert_backoff_schedule

        with WebhookEnv() as env:
            env.set_http_status(503, "Service Unavailable")

            env.call_deliver(max_retries=4)

            sleep_values = [float(c.args[0]) for c in env.mock["sleep"].call_args_list]

            # jitter=None: WebhookEnv patches no randomness source, so a jittered
            # delay would show up as a value inside the window rather than on the base.
            assert_backoff_schedule(sleep_values, jitter=None)


# ---------------------------------------------------------------------------
# UC-004-EXT-G-02
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhookRetrySucceedsOnSecondAttempt:
    """Webhook endpoint fails first, succeeds on retry -> delivery recorded.

    Covers: UC-004-EXT-G-02
    """

    def test_transient_failure_then_success_records_delivered(self, integration_db):
        """Given a webhook that 503s then 200s, the delivery result is 'delivered'.

        Covers: UC-004-EXT-G-02
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_sequence([(503, "Service Unavailable"), (200, "OK")])

            success, result = env.call_deliver(
                webhook_url=env.webhook_url,
                payload={"media_buy_id": "mb_001", "event": "delivery.update"},
                max_retries=3,
                timeout=10,
                event_type="delivery.update",
                tenant_id="test_tenant",
                object_id="mb_001",
            )

            assert success is True
            assert result["status"] == "delivered"
            assert result["attempts"] == 2
            assert result["response_code"] == 200
            assert env.mock["sleep"].call_count == 1


# ---------------------------------------------------------------------------
# UC-004-EXT-G-05
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhook401ForbiddenNoRetry:
    """Tests that 401 authentication errors are not retried.

    Covers: UC-004-EXT-G-05
    """

    def test_401_response_is_not_retried_and_marked_failed(self, integration_db):
        """A 401 Forbidden response must cause immediate failure with no retries.

        Covers: UC-004-EXT-G-05
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(401, "Unauthorized - invalid credentials")

            success, result = env.call_deliver(
                webhook_url=env.webhook_url,
                payload={"media_buy_id": "mb_001", "event": "delivery.update"},
                max_retries=3,
                timeout=10,
                event_type="delivery.update",
                tenant_id="test_tenant",
                object_id="mb_001",
            )

            assert success is False
            assert result["status"] == "failed"
            assert result["response_code"] == 401
            assert env.delivery_attempts == 1
            assert result["attempts"] == 1
            assert "401" in result["error"]

    def test_401_vs_500_retry_behavior_contrast(self, integration_db):
        """Verify 401 does NOT retry while 500 DOES retry.

        Covers: UC-004-EXT-G-05
        """
        from tests.harness import WebhookEnv

        # --- 401 case: should stop immediately ---
        with WebhookEnv() as env:
            env.set_http_status(401, "Unauthorized")
            success_401, result_401 = env.call_deliver(max_retries=3)

            assert success_401 is False
            assert result_401["attempts"] == 1
            assert env.delivery_attempts == 1

        # --- 500 case: should retry all attempts ---
        with WebhookEnv() as env:
            env.set_http_status(500, "Internal Server Error")
            success_500, result_500 = env.call_deliver(max_retries=3)

            assert success_500 is False
            assert result_500["attempts"] == 3
            assert env.delivery_attempts == 3


# ---------------------------------------------------------------------------
# UC-004-EXT-G-06
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestEXT_G_06_HmacAuthRejection:
    """HMAC auth rejection: 401/403 logs rejection, no retry, marks failed.

    Covers: UC-004-EXT-G-06
    """

    @pytest.mark.parametrize("status_code", [401, 403])
    def test_auth_rejection_no_retry_marks_failed(self, integration_db, status_code):
        """401/403 from endpoint => single attempt, no retry, status=failed.

        Covers: UC-004-EXT-G-06
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(status_code, "HMAC signature mismatch")

            success, result = env.call_deliver(
                webhook_url=env.webhook_url,
                payload={"media_buy_id": "mb_001", "impressions": 5000},
                signing_secret="super-secret-key-for-hmac-signing",
                max_retries=3,
                event_type="delivery.report",
                tenant_id="test_tenant",
                object_id="mb_001",
            )

            assert success is False
            assert result["status"] == "failed"
            assert result["response_code"] == status_code
            assert result["attempts"] == 1
            assert env.delivery_attempts == 1
            assert f"Client error {status_code}" in result["error"]

    def test_hmac_headers_sent_before_rejection(self, integration_db):
        """HMAC signature headers are added and verify against the wire bytes, even when rejected.

        Recomputes over the raw received body rather than a re-serialization
        of the payload dict, so a sender that signs one serialization and
        transmits another cannot pass this test vacuously (GH #1802).

        Covers: UC-004-EXT-G-06
        """
        from tests.harness import WebhookEnv
        from tests.helpers import assert_signature_verifies_over_wire_body

        secret = "my-webhook-secret-key-padded-to-32"

        with WebhookEnv() as env:
            env.set_http_status(401, "Invalid signature")

            env.call_deliver(
                webhook_url=env.webhook_url,
                payload={"media_buy_id": "mb_001", "event": "delivery.report"},
                signing_secret=secret,
                event_type="delivery.report",
                tenant_id="test_tenant",
                object_id="mb_001",
            )

            # Spec header names (X-AdCP-Signature/X-AdCP-Timestamp) since
            # GH #1802 -- the non-spec X-Webhook-* pair no longer exists.
            assert_signature_verifies_over_wire_body(env.last_delivery, secret)

    def test_auth_rejection_vs_server_error_retry_behavior(self, integration_db):
        """Contrast: 401 does NOT retry, but 500 DOES retry.

        Covers: UC-004-EXT-G-06
        """
        from tests.harness import WebhookEnv

        # 401 case
        with WebhookEnv() as env:
            env.set_http_status(401, "Unauthorized")
            success_401, result_401 = env.call_deliver(max_retries=3, event_type="delivery.report", tenant_id="t1")
            assert success_401 is False
            assert result_401["attempts"] == 1
            assert env.delivery_attempts == 1

        # 500 case
        with WebhookEnv() as env:
            env.set_http_status(500, "Internal Server Error")
            success_500, result_500 = env.call_deliver(max_retries=3, event_type="delivery.report", tenant_id="t1")
            assert success_500 is False
            assert result_500["attempts"] == 3
            assert env.delivery_attempts == 3


# ---------------------------------------------------------------------------
# UC-004-EXT-G-08 (SSRF validation — webhook failure does not reach buyer)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhookSSRFValidation:
    """Invalid/internal webhook URLs are rejected before any HTTP request is made.

    The URLs below are refused by production's own address policy — nothing here
    configures the refusal, so what is graded is the policy and not the test's
    opinion of it. ``169.254.169.254`` really is the cloud-metadata address.

    Covers: UC-004-EXT-G-08
    """

    def test_internal_url_rejected_with_validation_error(self, integration_db):
        """A refused destination is rejected, recorded failed, and counted — with nothing sent.

        A refusal must be exactly as visible to an operator as an endpoint that
        answered badly: one delivery record and one counter increment. Without
        the record, the only trace a refused destination leaves is a metric with
        no row to join it to, and "we never called you" is indistinguishable
        from "we never tried".

        Covers: UC-004-EXT-G-08
        """
        from src.core.database.models import WebhookDeliveryRecord
        from src.core.metrics import webhook_delivery_total
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            tenant, _principal = env.setup_default_data()
            refusals = webhook_delivery_total.labels(
                tenant_id=tenant.tenant_id, event_type="delivery.update", status="validation_failed"
            )
            refusals_before = refusals._value.get()

            success, result = env.call_deliver(
                webhook_url=_METADATA_URL,
                payload={"media_buy_id": "mb_001"},
                max_retries=3,
                event_type="delivery.update",
                tenant_id=tenant.tenant_id,
                object_id="mb_001",
            )

            assert success is False
            assert result["status"] == "failed"
            assert "Invalid webhook URL" in result["error"]
            assert result["attempts"] == 0
            # SSRF prevented: no HTTP request was made
            assert env.delivery_attempts == 0

            # Counted for operators...
            assert refusals._value.get() == refusals_before + 1

            # ...and written down. Zero attempts is the honest count: the
            # destination was refused before a connection was opened.
            record = env.get_one(WebhookDeliveryRecord, tenant_id=tenant.tenant_id, event_type="delivery.update")
            assert record is not None, "a refused destination left no delivery record"
            assert record.status == "failed"
            assert record.attempts == 0
            assert record.webhook_url == _METADATA_URL

    def test_ssrf_validation_records_failure_metrics(self, integration_db):
        """When URL validation fails with tenant/event context, metrics are recorded.

        Covers: UC-004-EXT-G-08 (src/core/webhook_delivery.py lines 95-98)
        """
        from src.core.metrics import webhook_delivery_total
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.setup_default_data()
            counter = webhook_delivery_total.labels(
                tenant_id="test_tenant", event_type="delivery.update", status="validation_failed"
            )
            before = counter._value.get()

            success, result = env.call_deliver(
                webhook_url=_METADATA_URL,
                payload={"media_buy_id": "mb_001"},
                tenant_id="test_tenant",
                event_type="delivery.update",
            )

            assert success is False
            # The real counter, not a mock echoing its own configuration back.
            assert counter._value.get() == before + 1

    def test_ssrf_validation_skips_metrics_without_tenant(self, integration_db):
        """When no tenant_id/event_type is provided, metrics are not recorded.

        Covers: UC-004-EXT-G-08 (src/core/webhook_delivery.py line 95 -- falsy branch)
        """
        from src.core.metrics import webhook_delivery_total
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            # Every status this delivery could possibly book, sampled before and
            # after: an untenanted delivery must move none of them.
            counters = {
                status: webhook_delivery_total.labels(
                    tenant_id="test_tenant", event_type="delivery.update", status=status
                )
                for status in ("validation_failed", "client_error", "max_retries_exceeded", "success")
            }
            before = {status: c._value.get() for status, c in counters.items()}

            success, result = env.call_deliver(
                webhook_url=_METADATA_URL,
                payload={"media_buy_id": "mb_001"},
                tenant_id=None,
                event_type=None,
            )

            assert success is False
            assert result["attempts"] == 0
            assert {status: c._value.get() for status, c in counters.items()} == before


# ---------------------------------------------------------------------------
# UC-004-EXT-G-01 / UC-004-EXT-G-03 (retry backoff + retry exhaustion)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhookRetryBackoff:
    """Server errors and network exceptions trigger retries with exponential backoff.

    Covers: UC-004-EXT-G-01, UC-004-EXT-G-03
    """

    def test_5xx_retry_with_eventual_success(self, integration_db):
        """503 -> 503 -> 200: delivery succeeds after retries.

        Covers: UC-004-EXT-G-01
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_sequence(
                [
                    (503, "Service Unavailable"),
                    (503, "Service Unavailable"),
                    (200, "OK"),
                ]
            )

            success, result = env.call_deliver(
                webhook_url=env.webhook_url,
                payload={"media_buy_id": "mb_001"},
                max_retries=4,
                event_type="delivery.update",
                tenant_id="test_tenant",
            )

            assert success is True
            assert result["status"] == "delivered"
            assert result["attempts"] == 3
            assert result["response_code"] == 200
            assert env.delivery_attempts == 3
            # Backoff sleeps: 2^0=1, 2^1=2 (before attempts 2 and 3)
            assert env.mock["sleep"].call_count == 2

    def test_5xx_retry_exhaustion(self, integration_db):
        """Always-500 with max_retries=3: delivery fails after all attempts exhausted.

        Covers: UC-004-EXT-G-03
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(500, "Internal Server Error")

            success, result = env.call_deliver(
                webhook_url=env.webhook_url,
                payload={"media_buy_id": "mb_001"},
                max_retries=3,
                event_type="delivery.update",
                tenant_id="test_tenant",
            )

            assert success is False
            assert result["status"] == "failed"
            assert result["attempts"] == 3
            assert result["response_code"] == 500
            assert env.delivery_attempts == 3

    def test_timeout_triggers_retry(self, integration_db):
        """An endpoint slower than the timeout triggers retry with backoff.

        The origin really stalls past the caller's own timeout, so the Timeout
        the retry loop catches is raised by the HTTP client itself — nothing here
        chooses which exception that is.

        Covers: UC-004-EXT-G-01 (src/core/webhook_delivery.py lines 222-225)
        """
        from tests.harness import WebhookEnv
        from tests.helpers.local_http_origin import responds

        with WebhookEnv() as env:
            # Stall past the 1s timeout twice, then answer promptly.
            env.set_http_sequence(
                [
                    responds(200, delay_seconds=_STALL_SECONDS),
                    responds(200, delay_seconds=_STALL_SECONDS),
                    responds(200, body=b"OK"),
                ]
            )

            success, result = env.call_deliver(
                max_retries=4,
                timeout=_TIMEOUT_SECONDS,
                event_type="delivery.update",
                tenant_id="test_tenant",
            )

            assert success is True
            assert result["attempts"] == 3
            assert env.delivery_attempts == 3

    def test_connection_error_triggers_retry(self, integration_db):
        """An endpoint that drops the connection triggers retry with backoff.

        Covers: UC-004-EXT-G-01 (src/core/webhook_delivery.py lines 227-230)
        """
        from tests.harness import WebhookEnv
        from tests.helpers.local_http_origin import hangs_up

        with WebhookEnv() as env:
            env.set_http_sequence([hangs_up(), (200, "OK")])

            success, result = env.call_deliver(
                max_retries=3,
                event_type="delivery.update",
                tenant_id="test_tenant",
            )

            assert success is True
            assert result["attempts"] == 2
            assert env.delivery_attempts == 2

    def test_malformed_response_body_triggers_retry(self, integration_db):
        """An endpoint whose body violates its own framing triggers retry with backoff.

        This is the third distinct network failure mode, and the one the generic
        ``RequestException`` branch exists for: the headers parse, so it is not a
        connection failure, and nothing timed out — the body simply does not
        decode. Clients report it as its own exception class
        (``ChunkedEncodingError``), which a retry policy handling only
        connection failures and timeouts would let escape.

        Covers: UC-004-EXT-G-01 (src/core/webhook_delivery.py lines 232-235)
        """
        from tests.harness import WebhookEnv
        from tests.helpers.local_http_origin import sends_malformed_body

        with WebhookEnv() as env:
            env.set_http_sequence([sends_malformed_body(), (200, "OK")])

            success, result = env.call_deliver(
                max_retries=3,
                event_type="delivery.update",
                tenant_id="test_tenant",
            )

            assert success is True
            assert result["attempts"] == 2
            assert env.delivery_attempts == 2

    def test_all_retries_timeout_reports_failure(self, integration_db):
        """When all retry attempts timeout, delivery is marked failed with attempt count.

        Covers: UC-004-EXT-G-03 (src/core/webhook_delivery.py lines 222-225, 243-274)
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.origin.delay(_STALL_SECONDS)

            success, result = env.call_deliver(
                max_retries=3,
                timeout=_TIMEOUT_SECONDS,
                event_type="delivery.update",
                tenant_id="test_tenant",
            )

            assert success is False
            assert result["status"] == "failed"
            assert result["attempts"] == 3
            # The seam collapses timeout and dropped-connection into one failure
            # class on purpose, so the old "Request timeout after Ns" wording is
            # gone. What survives is the distinction an operator can act on: the
            # endpoint never answered at all.
            assert "no response received" in result["error"]
            assert env.delivery_attempts == 3


# ---------------------------------------------------------------------------
# UC-004-EXT-G-08 (a redirect is a second destination, and is not chased)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhookRedirectNotFollowed:
    """A webhook endpoint that answers 3xx must not move the delivery elsewhere.

    Validating the URL a caller configured says nothing about where the request
    ends up: the endpoint answers, and its answer names a second address. If the
    client follows that hop, every address check made before the send was spent
    on a destination the request never reached — which is the whole SSRF hole,
    reopened by a default rather than by a decision.

    These grade ``deliver_webhook_with_retry``, not the transport underneath it.
    The seam has its own redirect test; what is unproven until this module is
    driven end to end is that delivery actually goes THROUGH the seam's decision
    instead of around it.

    Covers: UC-004-EXT-G-08
    """

    def test_redirect_to_a_second_origin_is_not_followed(self, integration_db):
        """The address a 302 names is never contacted — proven by its own hit count.

        Two real origins: the configured endpoint, and the one it points at. The
        second origin answers 200 to anything that reaches it, so if the hop is
        taken the delivery reports success and the second origin logs the
        request. ``hits == 0`` is direct observation, not inference from a
        status code.

        Covers: UC-004-EXT-G-08
        """
        from tests.harness import WebhookEnv
        from tests.helpers.local_http_origin import run_local_origin

        with WebhookEnv() as env, run_local_origin() as redirect_target:
            tenant, _principal = env.setup_default_data()
            env.origin.redirect_to(f"{redirect_target.base_url}/followed", status=302)

            success, result = env.call_deliver(
                max_retries=3,
                event_type="delivery.update",
                tenant_id=tenant.tenant_id,
            )

            # The hop was not taken.
            assert redirect_target.hits == 0
            assert redirect_target.paths == []

            # And the redirect itself is the terminal outcome, at attempt 1:
            # a 3xx is neither a delivery nor something a retry can fix.
            assert success is False
            assert result["status"] == "failed"
            assert result["response_code"] == 302
            assert result["attempts"] == 1
            assert env.delivery_attempts == 1

    def test_redirect_to_cloud_metadata_is_not_followed(self, integration_db):
        """The same refusal when the 302 names the cloud-metadata address.

        The address whose reachability is the reason this policy exists. It
        cannot be observed from the test (nothing here can serve it), so the
        proof is the pair of numbers the caller does control: the configured
        endpoint was reached exactly once, and what came back is a 302 rather
        than whatever the metadata service would have returned.

        Covers: UC-004-EXT-G-08
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            tenant, _principal = env.setup_default_data()
            env.origin.redirect_to(_METADATA_URL, status=302)

            success, result = env.call_deliver(
                max_retries=1,
                timeout=_TIMEOUT_SECONDS,
                event_type="delivery.update",
                tenant_id=tenant.tenant_id,
            )

            assert success is False
            assert result["status"] == "failed"
            assert result["response_code"] == 302
            assert result["attempts"] == 1
            assert env.delivery_attempts == 1


# ---------------------------------------------------------------------------
# UC-004-EXT-G-07 (auth rejection blocks the registered endpoint)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhookAuthRejectionBlocksEndpoint:
    """A 401 marks the buyer's registered endpoint blocked, not just this send.

    Retrying a rejected credential is how a publisher gets rate-limited by its
    own buyers, so the rejection has to outlive the delivery that discovered it.
    ``auth_blocked_at`` on the registered ``PushNotificationConfig`` is where
    that outliving is written down, and nothing else in the suite reads it back
    for this module.

    Covers: UC-004-EXT-G-07
    """

    def test_401_sets_auth_blocked_at_on_the_registered_config(self, integration_db):
        """A 401 from the endpoint stamps auth_blocked_at on its config row.

        Covers: UC-004-EXT-G-07
        """
        from src.core.database.models import PushNotificationConfig
        from tests.factories import PushNotificationConfigFactory
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            tenant, principal = env.setup_default_data()
            config = PushNotificationConfigFactory(tenant=tenant, principal=principal, url=env.webhook_url)
            config_id = config.id
            assert config.auth_blocked_at is None

            env.set_http_status(401, "Unauthorized")

            success, result = env.call_deliver(
                webhook_url=env.webhook_url,
                max_retries=3,
                event_type="delivery.update",
                tenant_id=tenant.tenant_id,
            )

            assert success is False
            assert result["response_code"] == 401
            assert result["attempts"] == 1

            # Production wrote through its own session; drop this session's
            # cached copy so the assertion reads the row and not the factory.
            env.get_session().expire_all()
            blocked = env.get_one(PushNotificationConfig, id=config_id)
            assert blocked.auth_blocked_at is not None


# ---------------------------------------------------------------------------
# UC-004-EXT-G-08 (every outcome reaches operators through its own counter)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhookOutcomeMetrics:
    """Each delivery outcome increments its own label, and only its own.

    A webhook failure never reaches the buyer synchronously, so the counter is
    the whole of what an operator sees. Four labels split the outcomes into
    classes that are acted on differently — a refused destination is a
    configuration problem, a 4xx is the buyer's, a retry exhaustion is the
    endpoint's — and a class that silently lands in a neighbouring bucket is
    an operator chasing the wrong team.

    These read the real counter rather than a patched one: a mock can only
    restate the label triple the test already typed out.

    Covers: UC-004-EXT-G-08
    """

    _LABELS = ("validation_failed", "client_error", "max_retries_exceeded", "success")

    @staticmethod
    def _counters(tenant_id: str, event_type: str) -> dict:
        from src.core.metrics import webhook_delivery_total

        return {
            status: webhook_delivery_total.labels(tenant_id=tenant_id, event_type=event_type, status=status)
            for status in TestWebhookOutcomeMetrics._LABELS
        }

    @pytest.mark.parametrize(
        ("status_code", "expected_label", "expected_success"),
        [
            (403, "client_error", False),
            (500, "max_retries_exceeded", False),
            (200, "success", True),
        ],
    )
    def test_outcome_increments_exactly_one_counter(
        self, integration_db, status_code, expected_label, expected_success
    ):
        """403 -> client_error, 500 -> max_retries_exceeded, 200 -> success; the others stay put.

        Covers: UC-004-EXT-G-08
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            tenant, _principal = env.setup_default_data()
            counters = self._counters(tenant.tenant_id, "delivery.update")
            before = {status: counter._value.get() for status, counter in counters.items()}

            env.set_http_status(status_code)

            success, _result = env.call_deliver(
                max_retries=3,
                event_type="delivery.update",
                tenant_id=tenant.tenant_id,
            )

            assert success is expected_success
            after = {status: counter._value.get() for status, counter in counters.items()}
            assert after == {status: value + (1 if status == expected_label else 0) for status, value in before.items()}

    def test_unresolvable_host_is_refused_before_any_attempt(self, integration_db):
        """A DNS-dead endpoint books as validation_failed with zero attempts, not as exhaustion.

        Worth pinning because it is the one bucket a reader expects the
        egress-seam migration to move, and it does not. The intuition is that a
        name that does not resolve is a *network* failure discovered by trying:
        retried to exhaustion, booked as ``max_retries_exceeded`` with
        ``attempts == N``, and only reclassified once resolution moves ahead of
        the send. It never worked that way here — today's address policy already
        resolves the hostname first (the seam's address validation ->
        ``socket.gethostbyname`` -> "Cannot resolve hostname"), so a DNS-dead
        customer endpoint has ALWAYS read as a policy refusal at zero attempts.
        The seam refuses it at the same point for the same reason.

        So this must stay green across the migration, and the claim that a
        lapsed customer DNS record "starts" reading as a refusal (4fya.11 R4)
        does not survive contact with the code: there is no fourth bucket shift
        to put in the commit message. What DOES change is only the wording after
        ``Invalid webhook URL:`` — asserted here as the prefix the call site
        owns, not as the cause text the address policy owns.

        Covers: UC-004-EXT-G-08
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            tenant, _principal = env.setup_default_data()
            counters = self._counters(tenant.tenant_id, "delivery.update")
            before = {status: counter._value.get() for status, counter in counters.items()}

            success, result = env.call_deliver(
                webhook_url=_UNRESOLVABLE_URL,
                max_retries=3,
                timeout=_TIMEOUT_SECONDS,
                event_type="delivery.update",
                tenant_id=tenant.tenant_id,
            )

            assert success is False
            assert result["status"] == "failed"
            assert result["attempts"] == 0
            assert result["error"].startswith("Invalid webhook URL:")
            assert env.delivery_attempts == 0

            after = {status: counter._value.get() for status, counter in counters.items()}
            assert after == {
                status: value + (1 if status == "validation_failed" else 0) for status, value in before.items()
            }


# ---------------------------------------------------------------------------
# UC-004-EXT-G-08 (the (bool, dict) contract the callers read)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhookResultShape:
    """Each arm returns a pinned key set, so a shared recorder cannot widen it silently.

    ``deliver_webhook_with_retry`` reports failure by returning, never by
    raising — that is what keeps a webhook failure off the buyer's synchronous
    path. Its three callers in ``src/services/slack_notifier.py`` read
    ``result["attempts"]`` and ``result.get("error")``, so those two keys are
    load-bearing on every failure arm; the rest of the shape is pinned here
    because nothing else grades it, and a refactor that routes all three arms
    through one recorder changes it by accident otherwise.

    The refused arm is the one that moves: today it returns before a delivery id
    exists, so it carries neither ``delivery_id`` nor ``response_code``. The
    decision (GH #1802 R5) is that all three failure arms return the
    same five keys, and that ``duration`` stays only where it already is.

    Covers: UC-004-EXT-G-08
    """

    @pytest.mark.parametrize(
        ("webhook_url", "http_status", "expected_keys"),
        [
            (_METADATA_URL, 200, _FAILURE_RESULT_KEYS),
            (None, 403, _FAILURE_RESULT_KEYS),
            (None, 500, _FAILURE_RESULT_KEYS | {"duration"}),
            (None, 200, _SUCCESS_RESULT_KEYS),
        ],
        ids=["refused", "client_error", "retry_exhaustion", "delivered"],
    )
    def test_result_key_set_per_arm(self, integration_db, webhook_url, http_status, expected_keys):
        """The result dict carries exactly the keys its arm is specified to carry.

        Covers: UC-004-EXT-G-08
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            tenant, _principal = env.setup_default_data()
            env.set_http_status(http_status)

            _success, result = env.call_deliver(
                webhook_url=webhook_url,
                max_retries=2,
                event_type="delivery.update",
                tenant_id=tenant.tenant_id,
            )

            assert set(result) == set(expected_keys)
