#!/usr/bin/env python3
"""
E2E tests for A2A webhook payload type compliance.

Per AdCP A2A spec (https://docs.adcontextprotocol.org/docs/protocols/a2a-guide#push-notifications-a2a-specific):
- Final states (completed, failed, canceled): Send full Task object with artifacts
- Intermediate states (working, input-required, submitted): Send TaskStatusUpdateEvent

This test validates that our A2A server sends the correct payload type based on status.
"""

import json
import socket
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from time import sleep
from typing import Any

import httpx
import psycopg2
import pytest
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

from tests.e2e.adcp_request_builder import build_adcp_media_buy_request, get_test_date_range, parse_tool_result


async def _discover_product_and_pricing(live_server: dict, test_auth_token: str) -> tuple[str, str]:
    """Discover a real product_id + pricing_option_id via get_products.

    The A2A create_media_buy skill handler only accepts the AdCP-spec
    ``packages[]`` format — legacy ``product_ids``/``total_budget`` is rejected
    with VALIDATION_ERROR before the manual-approval path runs, so a legacy
    request can never yield a ``submitted`` TaskStatusUpdateEvent webhook
    (salesagent-18h.3). Building a valid packages request needs a real
    pricing_option_id; discover it like test_adcp_full_lifecycle does.
    """
    headers = {"x-adcp-auth": test_auth_token, "x-adcp-tenant": "ci-test"}
    transport = StreamableHttpTransport(url=f"{live_server['mcp']}/mcp/", headers=headers)
    async with Client(transport=transport) as client:
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


class WebhookPayloadCapture(BaseHTTPRequestHandler):
    """Simple webhook receiver that captures all payloads with their types."""

    received_payloads: list[dict[str, Any]] = []

    def do_POST(self):
        """Handle POST requests (webhook notifications)."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            payload = json.loads(body.decode("utf-8"))

            # Extract status
            status = None
            if "status" in payload:
                status_obj = payload["status"]
                if isinstance(status_obj, dict):
                    status = status_obj.get("state")
                else:
                    status = str(status_obj)

            # A2A wire contract is camelCase (proto json_name): taskId, contextId,
            # messageId. snake_case (task_id, context_id) is a spec violation — the
            # a2a-sdk protobuf descriptor declares the JSON names explicitly. Record
            # the classification (or its failure) BEFORE responding so a regression
            # in protocol_webhook_service is observable to the test instead of being
            # swallowed by an "unknown" classification (gh-#1299 follow-up).
            classification_error = None
            payload_type = None
            try:
                payload_type = classify_a2a_payload(payload)
            except AssertionError as classify_exc:
                classification_error = str(classify_exc)

            self.received_payloads.append(
                {
                    "payload": payload,
                    "payload_type": payload_type,
                    "classification_error": classification_error,
                    "status": status,
                    "path": self.path,
                }
            )

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "received"}')
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def log_message(self, format, *args):
        """Silence HTTP server logs during tests."""
        pass


@pytest.fixture
def webhook_capture_server():
    """Start a local HTTP server to capture webhook payloads."""
    # Clear any previous captures
    WebhookPayloadCapture.received_payloads.clear()

    # Find an available port
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("0.0.0.0", 0))
    port = s.getsockname()[1]
    s.close()

    # Start server on all interfaces so it's reachable from Docker container
    server = HTTPServer(("0.0.0.0", port), WebhookPayloadCapture)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Use localhost in URL - the MCP server's protocol_webhook_service
    # rewrites it to host.docker.internal
    webhook_url = f"http://localhost:{port}/webhook"

    yield {
        "url": webhook_url,
        "server": server,
        "received": WebhookPayloadCapture.received_payloads,
    }

    server.shutdown()
    server.server_close()
    WebhookPayloadCapture.received_payloads.clear()


class TestA2AWebhookPayloadTypes:
    """Test A2A webhook payload type compliance with AdCP spec."""

    def setup_auto_approval(self, live_server):
        """Configure adapter for auto-approval to get completed webhooks."""
        try:
            conn = psycopg2.connect(live_server["postgres"])
            cursor = conn.cursor()

            cursor.execute("SELECT tenant_id FROM tenants WHERE subdomain = 'ci-test'")
            tenant_row = cursor.fetchone()
            if tenant_row:
                tenant_id = tenant_row[0]
                cursor.execute(
                    """
                    INSERT INTO adapter_config (tenant_id, adapter_type, mock_manual_approval_required)
                    VALUES (%s, 'mock', false)
                    ON CONFLICT (tenant_id)
                    DO UPDATE SET mock_manual_approval_required = false, adapter_type = 'mock'
                    """,
                    (tenant_id,),
                )
                conn.commit()
                print(f"Updated adapter config for tenant {tenant_id}: auto-approval enabled")

            cursor.close()
            conn.close()
        except Exception as e:
            print(f"Failed to update adapter config: {e}")

    def setup_manual_approval(self, live_server):
        """Configure adapter for manual approval to get submitted webhooks."""
        try:
            conn = psycopg2.connect(live_server["postgres"])
            cursor = conn.cursor()

            cursor.execute("SELECT tenant_id FROM tenants WHERE subdomain = 'ci-test'")
            tenant_row = cursor.fetchone()
            if tenant_row:
                tenant_id = tenant_row[0]
                cursor.execute(
                    """
                    INSERT INTO adapter_config (tenant_id, adapter_type, mock_manual_approval_required)
                    VALUES (%s, 'mock', true)
                    ON CONFLICT (tenant_id)
                    DO UPDATE SET mock_manual_approval_required = true, adapter_type = 'mock'
                    """,
                    (tenant_id,),
                )
                conn.commit()
                print(f"Updated adapter config for tenant {tenant_id}: manual approval required")

            cursor.close()
            conn.close()
        except Exception as e:
            print(f"Failed to update adapter config: {e}")

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
        self.setup_auto_approval(live_server)

        a2a_url = f"{live_server['a2a']}/a2a"
        context_id = str(uuid.uuid4())

        # Send A2A create_media_buy message with push notification config
        message = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": str(uuid.uuid4()),
                    "contextId": context_id,
                    "role": "user",  # Required by A2A spec
                    "parts": [
                        {
                            "data": {
                                "skill": "create_media_buy",
                                "parameters": {
                                    "product_ids": ["video_premium"],
                                    "total_budget": 5000.0,
                                    "start_time": "2025-03-01T00:00:00Z",
                                    "end_time": "2025-03-31T23:59:59Z",
                                    "brand": {"domain": "testbrand.com"},
                                    "context": {"e2e": "webhook_completed_test"},
                                },
                            }
                        }
                    ],
                },
                "configuration": {
                    "pushNotificationConfig": {
                        "url": webhook_capture_server["url"],
                        "authentication": {
                            "schemes": ["Bearer"],
                            "credentials": "test-webhook-token",
                        },
                    }
                },
            },
        }

        headers = {
            "Authorization": f"Bearer {test_auth_token}",
            "Content-Type": "application/json",
            "x-adcp-tenant": "ci-test",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(a2a_url, json=message, headers=headers)

            # Request should succeed
            assert response.status_code == 200, f"A2A request failed: {response.text}"
            result = response.json()
            assert "error" not in result, f"A2A error: {result.get('error')}"

        # Wait for webhook to be delivered
        timeout_seconds = 15
        poll_interval = 0.5
        elapsed = 0

        while elapsed < timeout_seconds and not webhook_capture_server["received"]:
            sleep(poll_interval)
            elapsed += poll_interval

        # Verify webhook was received
        received = webhook_capture_server["received"]
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
        # Enable manual approval so create_media_buy returns submitted state
        self.setup_manual_approval(live_server)

        a2a_url = f"{live_server['a2a']}/a2a"
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
        message = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": str(uuid.uuid4()),
                    "contextId": context_id,
                    "role": "user",  # Required by A2A spec
                    "parts": [
                        {
                            "data": {
                                "skill": "create_media_buy",
                                "parameters": media_buy_params,
                            }
                        }
                    ],
                },
                "configuration": {
                    "pushNotificationConfig": {
                        "url": webhook_capture_server["url"],
                        "authentication": {
                            "schemes": ["Bearer"],
                            "credentials": "test-webhook-token",
                        },
                    }
                },
            },
        }

        headers = {
            "Authorization": f"Bearer {test_auth_token}",
            "Content-Type": "application/json",
            "x-adcp-tenant": "ci-test",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(a2a_url, json=message, headers=headers)

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
            w["status"] == "submitted" for w in webhook_capture_server["received"]
        ):
            sleep(poll_interval)
            elapsed += poll_interval

        received = webhook_capture_server["received"]
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
        self.setup_auto_approval(live_server)

        a2a_url = f"{live_server['a2a']}/a2a"
        context_id = str(uuid.uuid4())

        # Send create_media_buy request
        message = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": str(uuid.uuid4()),
                    "contextId": context_id,
                    "role": "user",  # Required by A2A spec
                    "parts": [
                        {
                            "data": {
                                "skill": "create_media_buy",
                                "parameters": {
                                    "product_ids": ["video_premium"],
                                    "total_budget": 8000.0,
                                    "start_time": "2025-05-01T00:00:00Z",
                                    "end_time": "2025-05-31T23:59:59Z",
                                    "brand": {"domain": "testbrand.com"},
                                },
                            }
                        }
                    ],
                },
                "configuration": {
                    "pushNotificationConfig": {
                        "url": webhook_capture_server["url"],
                    }
                },
            },
        }

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

        while elapsed < timeout_seconds and not webhook_capture_server["received"]:
            sleep(0.5)
            elapsed += 0.5

        received = webhook_capture_server["received"]
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


class TestWebhookPayloadStructure:
    """Test webhook payload structure compliance."""

    def setup_auto_approval(self, live_server):
        """Configure adapter for auto-approval."""
        try:
            conn = psycopg2.connect(live_server["postgres"])
            cursor = conn.cursor()

            cursor.execute("SELECT tenant_id FROM tenants WHERE subdomain = 'ci-test'")
            tenant_row = cursor.fetchone()
            if tenant_row:
                tenant_id = tenant_row[0]
                cursor.execute(
                    """
                    INSERT INTO adapter_config (tenant_id, adapter_type, mock_manual_approval_required)
                    VALUES (%s, 'mock', false)
                    ON CONFLICT (tenant_id)
                    DO UPDATE SET mock_manual_approval_required = false, adapter_type = 'mock'
                    """,
                    (tenant_id,),
                )
                conn.commit()

            cursor.close()
            conn.close()
        except Exception as e:
            print(f"Failed to update adapter config: {e}")

    @pytest.mark.asyncio
    async def test_task_payload_has_required_fields(
        self,
        docker_services_e2e,
        live_server,
        test_auth_token,
        webhook_capture_server,
    ):
        """Test that Task payload has all required A2A fields."""
        self.setup_auto_approval(live_server)

        a2a_url = f"{live_server['a2a']}/a2a"

        message = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": str(uuid.uuid4()),
                    "contextId": str(uuid.uuid4()),
                    "role": "user",  # Required by A2A spec
                    "parts": [
                        {
                            "data": {
                                "skill": "create_media_buy",
                                "parameters": {
                                    "product_ids": ["video_premium"],
                                    "total_budget": 3000.0,
                                    "start_time": "2025-06-01T00:00:00Z",
                                    "end_time": "2025-06-30T23:59:59Z",
                                },
                            }
                        }
                    ],
                },
                "configuration": {"pushNotificationConfig": {"url": webhook_capture_server["url"]}},
            },
        }

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
        while elapsed < timeout_seconds and not webhook_capture_server["received"]:
            sleep(0.5)
            elapsed += 0.5

        received = webhook_capture_server["received"]
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
        # Enable manual approval to get submitted status
        try:
            conn = psycopg2.connect(live_server["postgres"])
            cursor = conn.cursor()
            cursor.execute("SELECT tenant_id FROM tenants WHERE subdomain = 'ci-test'")
            tenant_row = cursor.fetchone()
            if tenant_row:
                tenant_id = tenant_row[0]
                cursor.execute(
                    """
                    INSERT INTO adapter_config (tenant_id, adapter_type, mock_manual_approval_required)
                    VALUES (%s, 'mock', true)
                    ON CONFLICT (tenant_id)
                    DO UPDATE SET mock_manual_approval_required = true, adapter_type = 'mock'
                    """,
                    (tenant_id,),
                )
                conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            print(f"Failed to update adapter config: {e}")

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
        message = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": str(uuid.uuid4()),
                    "contextId": str(uuid.uuid4()),
                    "role": "user",  # Required by A2A spec
                    "parts": [
                        {
                            "data": {
                                "skill": "create_media_buy",
                                "parameters": media_buy_params,
                            }
                        }
                    ],
                },
                "configuration": {"pushNotificationConfig": {"url": webhook_capture_server["url"]}},
            },
        }

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
        while elapsed < timeout_seconds and not webhook_capture_server["received"]:
            sleep(0.5)
            elapsed += 0.5

        received = webhook_capture_server["received"]
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
        from src.core.database.models import PushNotificationConfig
        from src.services.protocol_webhook_service import ProtocolWebhookService

        WebhookPayloadCapture.received_payloads.clear()

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        server = HTTPServer(("127.0.0.1", port), WebhookPayloadCapture)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = PushNotificationConfig(
                id="pnc-test",
                tenant_id="t-test",
                principal_id="p-test",
                url=f"http://127.0.0.1:{port}/webhook",
                authentication_type=None,
                authentication_token=None,
            )
            service = ProtocolWebhookService()
            import asyncio

            sent = asyncio.run(service.send_notification(config, payload, metadata={"task_type": "create_media_buy"}))
            assert sent is True, "ProtocolWebhookService.send_notification should report success"
        finally:
            server.shutdown()
            server.server_close()

        received = WebhookPayloadCapture.received_payloads
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
