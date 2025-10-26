"""
Protocol-level webhook delivery service for A2A/MCP push notifications.

This service handles protocol-level push notifications (operation status updates)
as distinct from application-level webhooks (scheduled reporting delivery).

Protocol-level webhooks are configured via:
- A2A: MessageSendConfiguration.pushNotificationConfig
- MCP: (future) protocol wrapper extension

Application-level webhooks are configured via:
- AdCP: CreateMediaBuyRequest.reporting_webhook
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests

logger = logging.getLogger(__name__)


def _normalize_localhost_for_docker(url: str) -> str:
    """Replace localhost host with host.docker.internal while preserving userinfo and port."""
    try:
        parsed = urlparse(url)
        if parsed.hostname and parsed.hostname.lower() == "localhost":
            userinfo = ""
            if parsed.username:
                userinfo = parsed.username
                if parsed.password:
                    userinfo += f":{parsed.password}"
                userinfo += "@"
            port = f":{parsed.port}" if parsed.port else ""
            new_netloc = f"{userinfo}host.docker.internal{port}"
            return urlunparse(parsed._replace(netloc=new_netloc))
    except Exception:
        # If anything goes wrong, fall back to the original URL
        pass
    return url


class ProtocolWebhookService:
    """
    Service for sending protocol-level push notifications to clients.

    Supports authentication schemes:
    - HMAC-SHA256: Signs payload with shared secret
    - Bearer: Sends credentials as Bearer token
    - None: No authentication
    """

    def __init__(self):
        self._session = requests.Session()

    async def send_notification(
        self,
        webhook_config: dict[str, Any],
        task_type: str,
        task_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> bool:
        """
        Send a protocol-level push notification to the configured webhook.

        Args:
            webhook_config: Push notification configuration from protocol layer
                Expected structure:
                {
                    "url": "https://...",
                    "authentication": {
                        "schemes": ["HMAC-SHA256", "Bearer"],
                        "credentials": "secret_or_token"
                    }
                }
            task_type: Type of task ("sync_creatives", "media_buy", etc.)
            task_id: Task/operation ID
            status: Status of operation ("working", "completed", "failed")
            result: Result data if completed successfully
            error: Error message if failed

        Returns:
            True if notification sent successfully, False otherwise
        """
        if not webhook_config or not webhook_config.get("url"):
            logger.debug(f"No webhook URL configured for task {task_id}, skipping notification")
            return False

        url = _normalize_localhost_for_docker(webhook_config["url"])
        auth_config = webhook_config.get("authentication", {})
        schemes = auth_config.get("schemes", [])
        credentials = auth_config.get("credentials")

        # Build notification payload (AdCP standard format)
        payload: dict[str, Any] = {
            "task_id": task_id,
            "task_type": task_type,
            "status": status,
            "timestamp": datetime.now(UTC).isoformat(),
            "adcp_version": "2.3.0",
        }

        if result:
            payload["result"] = result
        if error:
            payload["error"] = error

        # Prepare headers
        headers = {"Content-Type": "application/json", "User-Agent": "AdCP-Sales-Agent/1.0"}

        # Apply authentication based on schemes
        if "HMAC-SHA256" in schemes and credentials:
            # Sign payload with HMAC-SHA256

            timestamp = str(int(time.time()))
            payload_str = json.dumps(payload, sort_keys=False, separators=(",", ":"))
            message = f"{timestamp}.{payload_str}"
            signature = hmac.new(credentials.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()

            headers["X-AdCP-Signature"] = f"sha256={signature}"
            headers["X-AdCP-Timestamp"] = timestamp

        elif "Bearer" in schemes and credentials:
            # Use Bearer token authentication
            headers["Authorization"] = f"Bearer {credentials}"

        # Send notification
        try:
            logger.info(f"Sending protocol-level webhook notification for task {task_id} to {url}")

            def _post() -> requests.Response:
                return self._session.post(url, json=payload, headers=headers, timeout=10.0)

            response = await asyncio.to_thread(_post)
            response.raise_for_status()

            logger.info(f"Successfully sent webhook notification for task {task_id} (status: {response.status_code})")
            return True

        except requests.HTTPError as e:
            logger.warning(
                f"Webhook notification failed for task {task_id}: HTTP {e.response.status_code} - {e.response.text}"
            )
            return False

        except requests.RequestException as e:
            logger.warning(f"Webhook notification failed for task {task_id}: {type(e).__name__} - {e}")
            return False

        except Exception as e:
            logger.error(f"Unexpected error sending webhook notification for task {task_id}: {e}")
            return False

    async def close(self):
        """Close HTTP client."""
        self._session.close()


# Global service instance
_webhook_service: ProtocolWebhookService | None = None


def get_protocol_webhook_service() -> ProtocolWebhookService:
    """Get or create global webhook service instance."""
    global _webhook_service
    if _webhook_service is None:
        _webhook_service = ProtocolWebhookService()
    return _webhook_service
