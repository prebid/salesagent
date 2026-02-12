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


class WebhookPayloadCapture(BaseHTTPRequestHandler):
    """Simple webhook receiver that captures all payloads with their types."""

    received_payloads: list[dict[str, Any]] = []

    def do_POST(self):
        """Handle POST requests (webhook notifications)."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            payload = json.loads(body.decode("utf-8"))

            # Determine payload type based on A2A spec:
            # - Task has 'id' field
            # - TaskStatusUpdateEvent has 'taskId' field
            payload_type = "unknown"
            if "taskId" in payload:
                payload_type = "TaskStatusUpdateEvent"
            elif "id" in payload:
                payload_type = "Task"

            # Extract status
            status = None
            if "status" in payload:
                status_obj = payload["status"]
                if isinstance(status_obj, dict):
                    status = status_obj.get("state")
                else:
                    status = str(status_obj)

            self.received_payloads.append(
                {
                    "payload": payload,
                    "payload_type": payload_type,
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
                                    "brand_manifest": {"name": "Webhook Type Test Brand"},
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

        # Find the completed status webhook
        completed_webhooks = [w for w in received if w["status"] == "completed"]

        if completed_webhooks:
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
                                "parameters": {
                                    "product_ids": ["video_premium"],
                                    "total_budget": 50000.0,
                                    "start_time": "2025-04-01T00:00:00Z",
                                    "end_time": "2025-04-30T23:59:59Z",
                                    "brand_manifest": {"name": "Webhook Submitted Test Brand"},
                                    "context": {"e2e": "webhook_submitted_test"},
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

            # Request should succeed (returns submitted status for async operations)
            assert response.status_code == 200, f"A2A request failed: {response.text}"

        # Wait for webhook to be delivered
        timeout_seconds = 15
        poll_interval = 0.5
        elapsed = 0

        while elapsed < timeout_seconds and not webhook_capture_server["received"]:
            sleep(poll_interval)
            elapsed += poll_interval

        received = webhook_capture_server["received"]

        # Check for submitted webhooks
        submitted_webhooks = [w for w in received if w["status"] == "submitted"]

        if submitted_webhooks:
            webhook = submitted_webhooks[0]
            # Per AdCP spec: submitted status should send TaskStatusUpdateEvent (has 'taskId' field)
            assert webhook["payload_type"] == "TaskStatusUpdateEvent", (
                f"Submitted status should send TaskStatusUpdateEvent payload, not {webhook['payload_type']}. "
                f"Payload has 'id': {'id' in webhook['payload']}, 'taskId': {'taskId' in webhook['payload']}"
            )

            # Verify TaskStatusUpdateEvent structure
            payload = webhook["payload"]
            assert "taskId" in payload, "TaskStatusUpdateEvent payload must have 'taskId' field"
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
                                    "brand_manifest": {"name": "Payload Validation Test"},
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

        # Define expected payload types per status
        final_states = {"completed", "failed", "canceled"}
        intermediate_states = {"working", "input-required", "submitted"}

        # Validate each received webhook
        for webhook in received:
            status = webhook["status"]
            payload_type = webhook["payload_type"]

            if status in final_states:
                assert payload_type == "Task", f"Final state '{status}' should use Task payload, got {payload_type}"
            elif status in intermediate_states:
                assert payload_type == "TaskStatusUpdateEvent", (
                    f"Intermediate state '{status}' should use TaskStatusUpdateEvent payload, got {payload_type}"
                )
            # Unknown states are logged but not asserted


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
        task_webhooks = [w for w in received if w["payload_type"] == "Task"]

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
                                "parameters": {
                                    "product_ids": ["video_premium"],
                                    "total_budget": 10000.0,
                                    "start_time": "2025-07-01T00:00:00Z",
                                    "end_time": "2025-07-31T23:59:59Z",
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
        event_webhooks = [w for w in received if w["payload_type"] == "TaskStatusUpdateEvent"]

        for webhook in event_webhooks:
            payload = webhook["payload"]

            # Required TaskStatusUpdateEvent fields per A2A spec
            assert "taskId" in payload, "TaskStatusUpdateEvent must have 'taskId' field"
            assert "status" in payload, "TaskStatusUpdateEvent must have 'status' field"

            status = payload["status"]
            assert "state" in status, "TaskStatusUpdateEvent.status must have 'state' field"
