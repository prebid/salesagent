#!/usr/bin/env python3
"""
E2E tests for A2A webhook payload type compliance.

Per AdCP A2A spec (https://docs.adcontextprotocol.org/docs/protocols/a2a-guide#push-notifications-a2a-specific):
- Final states (completed, failed, canceled): Send full Task object with artifacts
- Intermediate states (working, input-required, submitted): Send TaskStatusUpdateEvent

This test validates that our A2A server sends the correct payload type based on status.
"""

import json
import uuid
from time import sleep
from typing import Any

import httpx
import pytest

from tests.e2e._signing_e2e import origin, resolvable_signing_counterparty
from tests.e2e._tenant_state import set_mock_approval
from tests.e2e._webhook_capture import WebhookCaptureHandler, run_webhook_capture_server, tls_capture
from tests.e2e.adcp_request_builder import (
    build_a2a_message_send,
    build_adcp_media_buy_request,
    get_test_date_range,
    parse_tool_result,
)
from tests.e2e.conftest import e2e_in_network
from tests.e2e.utils import make_mcp_client
from tests.e2e.webhook_capture_service import decode_body
from tests.helpers.signing import signed_headers

#: The A2A JSON-RPC endpoint, as the SIGNATURE covers it: ``@target-uri`` is
#: ``origin + path``, so this string and the URL posted to must agree exactly.
_A2A_PATH = "/a2a"

#: The tenant hint every request in this module carries. The stack's own seeded
#: buyer lives here (``scripts/setup/init_database_ci.py``).
_TENANT_SUBDOMAIN = "ci-test"


async def _post_signed_a2a(live_server: dict, *, token: str, message: dict[str, Any]) -> httpx.Response:
    """POST *message* to ``/a2a`` under a REAL RFC 9421 signature, and return the answer.

    WHY the two credential-carrying tests in this module sign and the three others do
    not. ``push_notification_config.authentication.credentials`` is the secret the
    SELLER will present when it calls the buyer BACK, and security.mdx @ v3.1.1
    :1462-1465 makes a seller that supports request signing REQUIRE the inbound request
    carrying one to be 9421-signed — ":1375, regardless of ``required_for`` membership".
    The buyer's own ``Authorization: Bearer`` does not satisfy that and never did: the
    rule exists precisely because the registering request is normally bearer-authed and
    an on-path mutator can strip or inject the ``authentication`` block.

    Those two legs used to be ACCEPTED unsigned only because the escalation could not
    see the A2A PROTOCOL envelope — ``params.configuration.pushNotificationConfig``,
    which is where ``adcp_a2a_server.on_message_send`` READS the config it persists.
    Closing that is SF-4 (``salesagent-n78j0.2``), and a credentialed registration
    SUCCEEDING when signed is the half of that claim a refusal test cannot make.

    Two rules are load-bearing and are stated here once rather than at each call site:

    1. SERIALIZE ONCE and send exactly those bytes. httpx's ``json=`` re-serializes
       with its own separators, so a signature over a different rendering of the same
       object covers bytes the wire does not carry and is refused as
       ``request_signature_digest_mismatch`` — a fixture bug wearing a verifier bug's
       clothes.
    2. The signature covers ``@target-uri``, and ``_verify_url``
       (``src/core/signing/request_verifier_middleware.py``) rebuilds the authority from
       the ``Host`` header as received — PORT INCLUDED. :func:`origin` is the one
       definition of that value.
    """
    with resolvable_signing_counterparty(live_server, access_token=token) as counterparty:
        raw = json.dumps(message).encode()
        headers = signed_headers(
            counterparty.private_key,
            counterparty.token,
            method="POST",
            path=_A2A_PATH,
            body=raw,
            # ``request_headers`` otherwise injects the in-process signing suite's own
            # tenant hint, which names no tenant in this database.
            extra={"x-adcp-tenant": _TENANT_SUBDOMAIN, "Content-Type": "application/json"},
            key_id=counterparty.key_id,
            origin=origin(live_server["a2a"]),
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await client.post(f"{live_server['a2a']}{_A2A_PATH}", content=raw, headers=headers)


async def _discover_product_and_pricing(live_server: dict, test_auth_token: str) -> tuple[str, str]:
    """Discover a real product_id + pricing_option_id via get_products.

    The A2A create_media_buy skill handler only accepts the AdCP-spec
    ``packages[]`` format — legacy ``product_ids``/``total_budget`` is rejected
    with VALIDATION_ERROR before the manual-approval path runs, so a legacy
    request can never yield a ``submitted`` TaskStatusUpdateEvent webhook
    (salesagent-18h.3). Building a valid packages request needs a real
    pricing_option_id; discover it like test_adcp_full_lifecycle does.
    """
    async with make_mcp_client(live_server, token=test_auth_token) as client:
        products_result = await client.call_tool(
            "get_products",
            {"brand": {"domain": "testbrand.com"}, "brief": "video advertising"},
        )
        products_data = parse_tool_result(products_result)
    products = products_data["products"]
    assert products, "ci-test tenant must expose at least one product"
    product = products[0]
    pricing_options = product.get("pricing_options", [])
    assert pricing_options, f"Product {product['product_id']} must expose pricing_options"
    return product["product_id"], pricing_options[0]["pricing_option_id"]


class SnakeCaseWireViolation(AssertionError):
    """Raised when an A2A webhook payload uses snake_case keys instead of camelCase.

    The A2A v0.3 protobuf descriptor declares explicit JSON names (json_name) for
    every field: task_id -> "taskId", context_id -> "contextId", message_id ->
    "messageId". google.protobuf.json_format.MessageToDict() emits these camelCase
    names by default. Passing preserving_proto_field_name=True overrides them with
    snake_case, which silently breaks every spec-compliant A2A consumer. This
    classifier fails loudly so that regression cannot pass as an "unknown" payload.
    """


# Proto fields whose snake_case form on the wire is a spec violation. The value is
# the spec-compliant camelCase wire name (proto json_name).
_SNAKE_CASE_WIRE_VIOLATIONS = {
    "task_id": "taskId",
    "context_id": "contextId",
    "message_id": "messageId",
}


def classify_a2a_payload(payload: dict[str, Any]) -> str:
    """Classify an A2A webhook payload as 'Task' or 'TaskStatusUpdateEvent'.

    Per A2A spec:
    - Task has an 'id' field (final states: completed, failed, canceled)
    - TaskStatusUpdateEvent has a 'taskId' field (intermediate states)

    Raises:
        SnakeCaseWireViolation: if the payload carries snake_case proto field names
            (task_id/context_id/message_id) — a wire contract violation that must
            never be silently classified as 'unknown'.
        AssertionError: if the payload matches neither Task nor TaskStatusUpdateEvent.
    """
    snake_keys_present = sorted(k for k in _SNAKE_CASE_WIRE_VIOLATIONS if k in payload)
    if snake_keys_present:
        expected = {k: _SNAKE_CASE_WIRE_VIOLATIONS[k] for k in snake_keys_present}
        raise SnakeCaseWireViolation(
            f"A2A webhook payload uses snake_case wire keys {snake_keys_present}; "
            f"the A2A spec requires camelCase {expected}. Payload keys: {sorted(payload)}"
        )

    if "taskId" in payload:
        return "TaskStatusUpdateEvent"
    if "id" in payload:
        return "Task"
    raise AssertionError(
        f"A2A webhook payload is neither Task (has 'id') nor "
        f"TaskStatusUpdateEvent (has 'taskId'). Payload keys: {sorted(payload)}"
    )


def assert_no_classification_errors(received: list[dict[str, Any]]) -> None:
    """Fail if any captured webhook could not be classified as a valid A2A payload.

    A non-None ``classification_error`` means the payload used snake_case wire keys
    (the gh-#1299 bug) or matched neither Task nor TaskStatusUpdateEvent. Either way
    it is a spec violation that must fail the test loudly — never pass as 'unknown'.
    """
    errors = [(w["status"], w["classification_error"]) for w in received if w["classification_error"] is not None]
    assert not errors, (
        f"{len(errors)} webhook payload(s) failed A2A wire classification (snake_case or unrecognised shape): {errors}"
    )


def classified_capture(payload: dict[str, Any], path: str) -> dict[str, Any]:
    """One capture, classified — the shape every assertion in this module reads.

    A module-level function rather than a handler method so the SAME classification
    runs whether the payload came from the in-process receiver (unit-style leg) or
    was read back from the TLS capture service, instead of the two legs grading
    subtly different things.

    A2A wire contract is camelCase (proto json_name): taskId, contextId, messageId.
    snake_case (task_id, context_id) is a spec violation — the a2a-sdk protobuf
    descriptor declares the JSON names explicitly. The classification (or its
    failure) is recorded rather than raised so a regression in
    protocol_webhook_service is observable to the test instead of being swallowed
    by an "unknown" classification (gh-#1299 follow-up).
    """
    status = None
    if "status" in payload:
        status_obj = payload["status"]
        status = status_obj.get("state") if isinstance(status_obj, dict) else str(status_obj)

    classification_error = None
    payload_type = None
    try:
        payload_type = classify_a2a_payload(payload)
    except AssertionError as classify_exc:
        classification_error = str(classify_exc)

    return {
        "payload": payload,
        "payload_type": payload_type,
        "classification_error": classification_error,
        "status": status,
        "path": path,
    }


class WebhookPayloadCapture(WebhookCaptureHandler):
    """Webhook receiver that captures each payload with its A2A classification.

    Extends the shared capture handler via the ``record`` hook — only the
    classification logic lives here, never a copied ``do_POST``.
    """

    received_webhooks: list[dict[str, Any]] = []

    def record(self, payload):
        return classified_capture(payload, self.path)


class _ClassifiedCaptureHandle:
    """A :class:`CaptureHandle` whose ``received`` is CLASSIFIED, not raw.

    Keeps this module's assertions ("payload_type", "status", "classification_error")
    reading the same shape they always did, while the bytes now come from the TLS
    capture origin. ``received()`` re-reads on every call — the capture service is a
    separate process, so a poll loop must call it each turn rather than hold a list.
    """

    def __init__(self, handle) -> None:
        self._handle = handle
        self.url = handle.url

    def received(self) -> list[dict[str, Any]]:
        return [classified_capture(json.loads(decode_body(entry)), entry["path"]) for entry in self._handle.raw()]


@pytest.fixture
def webhook_capture_server():
    """A capture key on the TLS receiver, yielding classified captures."""
    with tls_capture("a2a-payload-e2e") as handle:
        yield _ClassifiedCaptureHandle(handle)


#: Only the classes that capture through the TLS receiver are in-network gated.
#: ``TestProtocolWebhookWireFormat`` below runs the service IN-PROCESS against a
#: loopback callback and needs no compose network, so a module-level mark would
#: wrongly skip it.
_IN_NETWORK_ONLY = pytest.mark.skipif(
    not e2e_in_network(),
    reason=(
        "in-network only: the webhook-capture service publishes no host port, so its readback "
        "control plane is reachable by compose service name alone (set ADCP_TEST_HOST)"
    ),
)


@_IN_NETWORK_ONLY
class TestA2AWebhookPayloadTypes:
    """Test A2A webhook payload type compliance with AdCP spec."""

    @pytest.mark.asyncio
    async def test_completed_status_sends_task_payload(
        self,
        docker_services_e2e,
        live_server,
        test_auth_token,
        webhook_capture_server,
    ):
        """
        Test that completed status sends a Task payload (not TaskStatusUpdateEvent).

        Per AdCP spec:
        - Completed is a final state
        - Final states should send Task object with artifacts
        """
        # Enable auto-approval so create_media_buy completes immediately
        set_mock_approval(live_server, manual=False)

        context_id = str(uuid.uuid4())

        product_id, pricing_option_id = await _discover_product_and_pricing(live_server, test_auth_token)
        start_time, end_time = get_test_date_range(days_from_now=1, duration_days=30)
        media_buy_params = build_adcp_media_buy_request(
            product_ids=[product_id],
            total_budget=5000.0,
            start_time=start_time,
            end_time=end_time,
            brand={"domain": "testbrand.com"},
            pricing_option_id=pricing_option_id,
            context={"e2e": "webhook_completed_test"},
        )

        message = build_a2a_message_send(
            skill="create_media_buy",
            parameters=media_buy_params,
            context_id=context_id,
            push_notification_config={
                "url": webhook_capture_server.url,
                "authentication": {"schemes": ["Bearer"], "credentials": "test-webhook-token"},
            },
        )

        # SIGNED, and that is not incidental to this test's subject — it is what makes
        # the registration above legal at all. See ``_post_signed_a2a``.
        response = await _post_signed_a2a(live_server, token=test_auth_token, message=message)

        # Request should succeed
        assert response.status_code == 200, f"A2A request failed: {response.text}"
        result = response.json()
        assert "error" not in result, f"A2A error: {result.get('error')}"

        # Wait for webhook to be delivered
        timeout_seconds = 15
        poll_interval = 0.5
        elapsed = 0

        while elapsed < timeout_seconds and not webhook_capture_server.received():
            sleep(poll_interval)
            elapsed += poll_interval

        # Verify webhook was received
        received = webhook_capture_server.received()
        assert received, "Expected at least one webhook delivery"

        # No received webhook may carry a snake_case wire violation (gh-#1299).
        assert_no_classification_errors(received)

        # The completed-status webhook MUST be present and MUST be a Task. No
        # `if completed_webhooks:` guard — a missing or misclassified webhook is
        # a failure, not a silent pass.
        completed_webhooks = [w for w in received if w["status"] == "completed"]
        assert completed_webhooks, (
            f"Expected a 'completed' status webhook. Received statuses: {[w['status'] for w in received]}"
        )

        webhook = completed_webhooks[0]
        # Per AdCP spec: completed status should send Task (has 'id' field)
        assert webhook["payload_type"] == "Task", (
            f"Completed status should send Task payload, not {webhook['payload_type']}. "
            f"Payload has 'id': {'id' in webhook['payload']}, 'taskId': {'taskId' in webhook['payload']}"
        )

        # Verify Task structure
        payload = webhook["payload"]
        assert "id" in payload, "Task payload must have 'id' field"
        assert "status" in payload, "Task payload must have 'status' field"

        # Per AdCP spec: completed status MUST have result in artifacts[0].parts[]
        assert "artifacts" in payload, "Completed Task must have 'artifacts' field"
        assert len(payload["artifacts"]) > 0, "Completed Task must have at least one artifact"
        artifact = payload["artifacts"][0]
        assert "parts" in artifact, "Artifact must have 'parts' field"
        assert len(artifact["parts"]) > 0, "Artifact must have at least one part"

    @pytest.mark.asyncio
    async def test_submitted_status_sends_task_status_update_event(
        self,
        docker_services_e2e,
        live_server,
        test_auth_token,
        webhook_capture_server,
    ):
        """
        Test that submitted status sends a TaskStatusUpdateEvent payload.

        Per AdCP spec:
        - Submitted is an intermediate state
        - Intermediate states should send TaskStatusUpdateEvent
        """
        # Enable manual approval so create_media_buy returns submitted state.
        # MUST be restored in the finally below: adapter_config is SHARED
        # tenant state — leaving manual approval on leaks into every later
        # e2e test (pytest-randomly ordering), turning their creates into
        # spec-3.1.1 submitted envelopes with no media_buy_id.
        set_mock_approval(live_server, manual=True)
        try:
            context_id = str(uuid.uuid4())

            # AdCP-spec packages[] format (the A2A skill rejects legacy
            # product_ids/total_budget before the manual-approval path).
            product_id, pricing_option_id = await _discover_product_and_pricing(live_server, test_auth_token)
            start_time, end_time = get_test_date_range(days_from_now=1, duration_days=30)
            media_buy_params = build_adcp_media_buy_request(
                product_ids=[product_id],
                total_budget=50000.0,
                start_time=start_time,
                end_time=end_time,
                brand={"domain": "testbrand.com"},
                pricing_option_id=pricing_option_id,
                context={"e2e": "webhook_submitted_test"},
            )

            # Send A2A create_media_buy message that triggers approval workflow
            message = build_a2a_message_send(
                skill="create_media_buy",
                parameters=media_buy_params,
                context_id=context_id,
                push_notification_config={
                    "url": webhook_capture_server.url,
                    "authentication": {"schemes": ["Bearer"], "credentials": "test-webhook-token"},
                },
            )

            # Signed for the same reason as the sibling test above: the registration
            # carries the callback secret, and security.mdx @ v3.1.1 :1462-1465 makes a
            # signature mandatory on the request that hands one over.
            response = await _post_signed_a2a(live_server, token=test_auth_token, message=message)

            # Request should succeed (returns submitted status for async operations)
            assert response.status_code == 200, f"A2A request failed: {response.text}"

            # Wait for webhook to be delivered
            timeout_seconds = 15
            poll_interval = 0.5
            elapsed = 0

            # A manual-approval media buy emits the intermediate `submitted`
            # TaskStatusUpdateEvent first, then (mock auto-approval simulation) a
            # terminal `completed` Task. Breaking on merely the first delivery
            # races against that ordering — poll until the submitted webhook is
            # actually captured (or timeout).
            while elapsed < timeout_seconds and not any(
                w["status"] == "submitted" for w in webhook_capture_server.received()
            ):
                sleep(poll_interval)
                elapsed += poll_interval

            received = webhook_capture_server.received()
            assert received, "Expected at least one webhook delivery"

            # No received webhook may carry a snake_case wire violation (gh-#1299).
            assert_no_classification_errors(received)

            # The submitted-status webhook MUST be present and MUST be a
            # TaskStatusUpdateEvent. No `if submitted_webhooks:` guard — a missing or
            # misclassified webhook is a failure, not a silent pass.
            submitted_webhooks = [w for w in received if w["status"] == "submitted"]
            assert submitted_webhooks, (
                f"Expected a 'submitted' status webhook. Received statuses: {[w['status'] for w in received]}"
            )

            webhook = submitted_webhooks[0]
            # Per AdCP spec: submitted status should send TaskStatusUpdateEvent (has 'taskId' field)
            assert webhook["payload_type"] == "TaskStatusUpdateEvent", (
                f"Submitted status should send TaskStatusUpdateEvent payload, not {webhook['payload_type']}. "
                f"Payload has 'id': {'id' in webhook['payload']}, 'taskId': {'taskId' in webhook['payload']}"
            )

            # Verify TaskStatusUpdateEvent structure (camelCase per A2A wire contract)
            payload = webhook["payload"]
            assert "taskId" in payload, "TaskStatusUpdateEvent payload must have 'taskId' field"
            assert "task_id" not in payload, "TaskStatusUpdateEvent must NOT use snake_case 'task_id'"
            assert "status" in payload, "TaskStatusUpdateEvent payload must have 'status' field"
            assert "state" in payload["status"], "TaskStatusUpdateEvent.status must have 'state' field"
        finally:
            # Restore shared tenant state for subsequent e2e tests.
            set_mock_approval(live_server, manual=False)

    @pytest.mark.asyncio
    async def test_webhook_payload_type_matches_status(
        self,
        docker_services_e2e,
        live_server,
        test_auth_token,
        webhook_capture_server,
    ):
        """
        Test that all received webhooks use correct payload type for their status.

        Per AdCP spec:
        - Final states (completed, failed, canceled): Task
        - Intermediate states (working, input-required, submitted): TaskStatusUpdateEvent
        """
        # Enable auto-approval
        set_mock_approval(live_server, manual=False)

        a2a_url = f"{live_server['a2a']}/a2a"
        context_id = str(uuid.uuid4())

        product_id, pricing_option_id = await _discover_product_and_pricing(live_server, test_auth_token)
        start_time, end_time = get_test_date_range(days_from_now=1, duration_days=30)
        media_buy_params = build_adcp_media_buy_request(
            product_ids=[product_id],
            total_budget=8000.0,
            start_time=start_time,
            end_time=end_time,
            brand={"domain": "testbrand.com"},
            pricing_option_id=pricing_option_id,
            context={"e2e": "webhook_payload_type_match_test"},
        )

        message = build_a2a_message_send(
            skill="create_media_buy",
            parameters=media_buy_params,
            context_id=context_id,
            push_notification_config={"url": webhook_capture_server.url},
        )

        headers = {
            "Authorization": f"Bearer {test_auth_token}",
            "Content-Type": "application/json",
            "x-adcp-tenant": "ci-test",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.post(a2a_url, json=message, headers=headers)

        # Wait for webhooks
        timeout_seconds = 15
        elapsed = 0

        while elapsed < timeout_seconds and not webhook_capture_server.received():
            sleep(0.5)
            elapsed += 0.5

        received = webhook_capture_server.received()
        assert received, "Expected at least one webhook delivery"

        # No received webhook may carry a snake_case wire violation (gh-#1299).
        assert_no_classification_errors(received)

        # Define expected payload types per status
        final_states = {"completed", "failed", "canceled"}
        intermediate_states = {"working", "input-required", "submitted"}

        # Every webhook with a known status must map to the spec-mandated payload
        # type. A webhook whose status is neither final nor intermediate is itself
        # a contract violation — it is asserted, not silently skipped.
        asserted = 0
        for webhook in received:
            status = webhook["status"]
            payload_type = webhook["payload_type"]

            if status in final_states:
                assert payload_type == "Task", f"Final state '{status}' should use Task payload, got {payload_type}"
                asserted += 1
            elif status in intermediate_states:
                assert payload_type == "TaskStatusUpdateEvent", (
                    f"Intermediate state '{status}' should use TaskStatusUpdateEvent payload, got {payload_type}"
                )
                asserted += 1
            else:
                raise AssertionError(
                    f"Webhook has unrecognised status '{status}' (not a final or "
                    f"intermediate A2A state). Payload keys: {sorted(webhook['payload'])}"
                )

        assert asserted > 0, "No webhook with a classifiable status was received"


@_IN_NETWORK_ONLY
class TestWebhookPayloadStructure:
    """Test webhook payload structure compliance."""

    @pytest.mark.asyncio
    async def test_task_payload_has_required_fields(
        self,
        docker_services_e2e,
        live_server,
        test_auth_token,
        webhook_capture_server,
    ):
        """Test that Task payload has all required A2A fields."""
        set_mock_approval(live_server, manual=False)

        a2a_url = f"{live_server['a2a']}/a2a"

        product_id, pricing_option_id = await _discover_product_and_pricing(live_server, test_auth_token)
        start_time, end_time = get_test_date_range(days_from_now=1, duration_days=30)
        media_buy_params = build_adcp_media_buy_request(
            product_ids=[product_id],
            total_budget=3000.0,
            start_time=start_time,
            end_time=end_time,
            brand={"domain": "testbrand.com"},
            pricing_option_id=pricing_option_id,
            context={"e2e": "webhook_task_required_fields_test"},
        )

        message = build_a2a_message_send(
            skill="create_media_buy",
            parameters=media_buy_params,
            push_notification_config={"url": webhook_capture_server.url},
        )

        headers = {
            "Authorization": f"Bearer {test_auth_token}",
            "Content-Type": "application/json",
            "x-adcp-tenant": "ci-test",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.post(a2a_url, json=message, headers=headers)

        # Wait for webhook
        timeout_seconds = 15
        elapsed = 0
        while elapsed < timeout_seconds and not webhook_capture_server.received():
            sleep(0.5)
            elapsed += 0.5

        received = webhook_capture_server.received()
        assert received, "Expected at least one webhook delivery"
        assert_no_classification_errors(received)

        task_webhooks = [w for w in received if w["payload_type"] == "Task"]
        assert task_webhooks, (
            f"Expected at least one Task webhook. Received payload types: {[w['payload_type'] for w in received]}"
        )

        for webhook in task_webhooks:
            payload = webhook["payload"]

            # Required Task fields per A2A spec
            assert "id" in payload, "Task must have 'id' field"
            assert "status" in payload, "Task must have 'status' field"

            status = payload["status"]
            assert "state" in status, "Task.status must have 'state' field"

            # Per AdCP spec: completed/failed MUST have result in artifacts[0].parts[]
            if status["state"] in ("completed", "failed"):
                assert "artifacts" in payload, f"Task with status '{status['state']}' must have 'artifacts'"
                assert isinstance(payload["artifacts"], list), "artifacts must be a list"
                assert len(payload["artifacts"]) > 0, "artifacts must have at least one item"
                assert "parts" in payload["artifacts"][0], "artifact must have 'parts'"
                assert len(payload["artifacts"][0]["parts"]) > 0, "artifact.parts must have at least one part"

    @pytest.mark.asyncio
    async def test_task_status_update_event_has_required_fields(
        self,
        docker_services_e2e,
        live_server,
        test_auth_token,
        webhook_capture_server,
    ):
        """Test that TaskStatusUpdateEvent payload has all required A2A fields."""
        # Enable manual approval to get submitted status.
        # MUST be restored in the finally below: adapter_config is SHARED
        # tenant state — leaving manual approval on leaks into every later
        # e2e test (pytest-randomly ordering), turning their creates into
        # spec-3.1.1 submitted envelopes with no media_buy_id.
        set_mock_approval(live_server, manual=True)
        try:
            a2a_url = f"{live_server['a2a']}/a2a"

            # AdCP-spec packages[] format (legacy product_ids/total_budget is
            # rejected before the manual-approval path → no submitted webhook).
            product_id, pricing_option_id = await _discover_product_and_pricing(live_server, test_auth_token)
            start_time, end_time = get_test_date_range(days_from_now=1, duration_days=30)
            media_buy_params = build_adcp_media_buy_request(
                product_ids=[product_id],
                total_budget=10000.0,
                start_time=start_time,
                end_time=end_time,
                brand={"domain": "testbrand.com"},
                pricing_option_id=pricing_option_id,
                context={"e2e": "webhook_tsue_required_fields"},
            )

            # Trigger an async operation that sends intermediate status
            message = build_a2a_message_send(
                skill="create_media_buy",
                parameters=media_buy_params,
                push_notification_config={"url": webhook_capture_server.url},
            )

            headers = {
                "Authorization": f"Bearer {test_auth_token}",
                "Content-Type": "application/json",
                "x-adcp-tenant": "ci-test",
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                await client.post(a2a_url, json=message, headers=headers)

            # Wait for webhook
            timeout_seconds = 15
            elapsed = 0
            while elapsed < timeout_seconds and not webhook_capture_server.received():
                sleep(0.5)
                elapsed += 0.5

            received = webhook_capture_server.received()
            assert received, "Expected at least one webhook delivery"
            assert_no_classification_errors(received)

            event_webhooks = [w for w in received if w["payload_type"] == "TaskStatusUpdateEvent"]
            assert event_webhooks, (
                f"Expected at least one TaskStatusUpdateEvent webhook. Received payload "
                f"types: {[w['payload_type'] for w in received]}"
            )

            for webhook in event_webhooks:
                payload = webhook["payload"]

                # Required TaskStatusUpdateEvent fields per A2A spec (camelCase wire contract)
                assert "taskId" in payload, "TaskStatusUpdateEvent must have 'taskId' field"
                assert "task_id" not in payload, "TaskStatusUpdateEvent must NOT use snake_case 'task_id'"
                assert "status" in payload, "TaskStatusUpdateEvent must have 'status' field"

                status = payload["status"]
                assert "state" in status, "TaskStatusUpdateEvent.status must have 'state' field"
        finally:
            # Restore shared tenant state for subsequent e2e tests.
            set_mock_approval(live_server, manual=False)


class TestProtocolWebhookWireFormat:
    """Hermetic wire-format contract tests for ProtocolWebhookService.

    These exercise the real ``ProtocolWebhookService.send_notification`` code path
    against a local HTTP capture server — no Docker stack, no database. They are
    the regression guard for gh-#1299: dropping ``preserving_proto_field_name=True``
    so A2A protobuf payloads serialize with the spec-mandated camelCase wire names.

    Mutation contract: re-adding ``preserving_proto_field_name=True`` to
    ``protocol_webhook_service.py`` makes
    ``test_task_status_update_event_serializes_camelcase`` FAIL (snake_case keys
    raise SnakeCaseWireViolation in the capture classifier).
    """

    def _send_and_capture(self, payload) -> dict[str, Any]:
        """Send `payload` via the real service and return the classified capture."""
        import asyncio

        from src.core.database.models import PushNotificationConfig
        from src.services.protocol_webhook_service import ProtocolWebhookService

        # host='127.0.0.1': this class is unit-style (no Docker) — the service
        # runs in-process, so loopback is always the right callback host.
        #
        # ADCP_TESTING is NOT set here. The autouse fixture (tests/conftest.py)
        # already sets it for every test, so a second setenv only obscured which
        # posture this test actually grades — it read as "this test deliberately
        # opts into leniency" when in fact it inherits it like everything else
        # (salesagent-og9k.4). Removing it changes no behaviour and stops the
        # redundant spelling from being copied.
        #
        # The SSRF gate itself is never patched: that would hide regressions.
        with run_webhook_capture_server(
            WebhookPayloadCapture, WebhookPayloadCapture.received_webhooks, host="127.0.0.1"
        ) as info:
            config = PushNotificationConfig(
                id="pnc-test",
                tenant_id="t-test",
                principal_id="p-test",
                url=info["url"],
                authentication_type=None,
                authentication_token=None,
            )
            service = ProtocolWebhookService()
            sent = asyncio.run(service.send_notification(config, payload, metadata={"task_type": "create_media_buy"}))
            assert sent is True, "ProtocolWebhookService.send_notification should report success"

            received = list(info["received"])

        assert len(received) == 1, f"Expected exactly one captured webhook, got {len(received)}"
        return received[0]

    def test_task_status_update_event_serializes_camelcase(self):
        """TaskStatusUpdateEvent must hit the wire as camelCase (taskId, not task_id).

        This is the gh-#1299 regression guard and the mutation-test target.
        """
        from a2a.types import TaskState, TaskStatus, TaskStatusUpdateEvent

        event = TaskStatusUpdateEvent(
            task_id="t-123",
            context_id="c-456",
            status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
        )

        capture = self._send_and_capture(event)
        payload = capture["payload"]

        assert capture["classification_error"] is None, (
            f"Wire payload failed A2A classification (snake_case regression?): {capture['classification_error']}"
        )
        assert capture["payload_type"] == "TaskStatusUpdateEvent"
        assert payload["taskId"] == "t-123", f"Expected camelCase 'taskId', got payload keys {sorted(payload)}"
        assert payload["contextId"] == "c-456", f"Expected camelCase 'contextId', got payload keys {sorted(payload)}"
        assert "task_id" not in payload, "snake_case 'task_id' must not appear on the A2A wire"
        assert "context_id" not in payload, "snake_case 'context_id' must not appear on the A2A wire"

    def test_task_serializes_camelcase(self):
        """Final-state Task must serialize with camelCase contextId and classify as Task."""
        from a2a.types import Task, TaskState, TaskStatus

        task = Task(
            id="t-789",
            context_id="c-789",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        )

        capture = self._send_and_capture(task)
        payload = capture["payload"]

        assert capture["classification_error"] is None, (
            f"Wire payload failed A2A classification: {capture['classification_error']}"
        )
        assert capture["payload_type"] == "Task"
        assert payload["id"] == "t-789"
        assert payload["contextId"] == "c-789", f"Expected camelCase 'contextId', got payload keys {sorted(payload)}"
        assert "context_id" not in payload, "snake_case 'context_id' must not appear on the A2A wire"

    def test_classifier_rejects_snake_case_wire_payload(self):
        """The capture classifier must fail loudly on a snake_case payload.

        Guards the test infrastructure itself: a future snake_case regression can
        never be silently absorbed as an 'unknown' payload type.
        """
        with pytest.raises(SnakeCaseWireViolation):
            classify_a2a_payload({"task_id": "t-1", "context_id": "c-1", "status": {"state": "submitted"}})

    def test_classifier_accepts_camelcase_task_status_update_event(self):
        """The camelCase TaskStatusUpdateEvent shape classifies without error."""
        result = classify_a2a_payload({"taskId": "t-1", "contextId": "c-1", "status": {"state": "submitted"}})
        assert result == "TaskStatusUpdateEvent"

    def test_classifier_accepts_camelcase_task(self):
        """The camelCase Task shape classifies as Task."""
        result = classify_a2a_payload({"id": "t-1", "contextId": "c-1", "status": {"state": "completed"}})
        assert result == "Task"
