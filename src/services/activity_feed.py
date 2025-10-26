"""Activity feed WebSocket handler for real-time MCP activity monitoring."""

import asyncio
import json
import logging
import weakref
from collections import deque
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


class ActivityFeed:
    """Manages real-time activity feed for tenant dashboards."""

    def __init__(self):
        # Store active WebSocket connections per tenant
        self.connections: dict[str, set[weakref.ref]] = {}
        # Store recent activities per tenant (for new connections)
        self.recent_activities: dict[str, deque] = {}
        self.max_recent = 50

    def add_connection(self, tenant_id: str, websocket):
        """Add a new WebSocket connection for a tenant."""
        if tenant_id not in self.connections:
            self.connections[tenant_id] = set()
            self.recent_activities[tenant_id] = deque(maxlen=self.max_recent)

        # Use weak reference to avoid memory leaks
        self.connections[tenant_id].add(weakref.ref(websocket))

        # Send recent activities to new connection
        for activity in self.recent_activities[tenant_id]:
            try:
                asyncio.create_task(websocket.send(json.dumps(activity)))
            except:
                pass

    def remove_connection(self, tenant_id: str, websocket):
        """Remove a WebSocket connection."""
        if tenant_id in self.connections:
            # Remove dead references
            self.connections[tenant_id] = {
                ref for ref in self.connections[tenant_id] if ref() is not None and ref() != websocket
            }

            # Clean up empty tenant entries
            if not self.connections[tenant_id]:
                del self.connections[tenant_id]

    async def broadcast_activity(self, tenant_id: str, activity: dict):
        """Broadcast an activity to all connections for a tenant."""
        # Add timestamp if not present
        if "timestamp" not in activity:
            activity["timestamp"] = datetime.now(UTC).isoformat()

        # Calculate relative time
        activity["time_relative"] = self._get_relative_time(activity["timestamp"])

        # Store in recent activities
        if tenant_id not in self.recent_activities:
            self.recent_activities[tenant_id] = deque(maxlen=self.max_recent)
        self.recent_activities[tenant_id].append(activity)

        # If we have a Flask-SocketIO callback, use it
        if hasattr(self, "broadcast_to_websocket"):
            try:
                self.broadcast_to_websocket(tenant_id, activity)
            except Exception as e:
                logger.debug(f"Failed to broadcast via Socket.IO: {e}")

        # Also broadcast to any raw WebSocket connections (backward compatibility)
        if tenant_id in self.connections:
            dead_refs = []
            for ref in self.connections[tenant_id]:
                ws = ref()
                if ws is None:
                    dead_refs.append(ref)
                else:
                    try:
                        await ws.send(json.dumps(activity))
                    except Exception as e:
                        logger.debug(f"Failed to send to WebSocket: {e}")
                        dead_refs.append(ref)

            # Clean up dead references
            for ref in dead_refs:
                self.connections[tenant_id].discard(ref)

    def log_api_call(
        self,
        tenant_id: str,
        principal_name: str,
        method: str,
        status_code: int | None = None,
        response_time_ms: int | None = None,
    ):
        """Log an API call activity."""
        activity: dict[str, Any] = {
            "type": "api-call",
            "principal_name": principal_name,
            "action": f"Called {method}",
            "details": {},
        }

        if status_code:
            activity["details"]["primary"] = f"{status_code} {'OK' if status_code == 200 else 'ERROR'}"
        if response_time_ms:
            activity["details"]["secondary"] = f"{response_time_ms}ms"

        # Try to create task if event loop is running, otherwise skip
        try:
            asyncio.create_task(self.broadcast_activity(tenant_id, activity))
        except RuntimeError:
            # No event loop running - skip broadcast (not in async context)
            logger.debug(f"Skipping activity broadcast - no event loop available for {method}")
            pass

    def log_media_buy(
        self,
        tenant_id: str,
        principal_name: str,
        media_buy_id: str,
        budget: float | None = None,
        duration_days: int | None = None,
        action: str = "created",
    ):
        """Log a media buy activity."""
        activity: dict[str, Any] = {
            "type": "media-buy",
            "principal_name": principal_name,
            "action": f"{action.capitalize()} media buy {media_buy_id}",
            "details": {},
        }

        if budget:
            activity["details"]["primary"] = f"${budget:,.0f}"
        if duration_days:
            activity["details"]["secondary"] = f"{duration_days} days"

        # Try to create task if event loop is running, otherwise skip
        try:
            asyncio.create_task(self.broadcast_activity(tenant_id, activity))
        except RuntimeError:
            # No event loop running - skip broadcast (not in async context)
            logger.debug(f"Skipping activity broadcast - no event loop available for media buy {media_buy_id}")
            pass

    def log_creative(
        self,
        tenant_id: str,
        principal_name: str,
        creative_id: str,
        format_name: str | None = None,
        status: str | None = None,
    ):
        """Log a creative activity."""
        activity: dict[str, Any] = {
            "type": "creative",
            "principal_name": principal_name,
            "action": f"Uploaded creative {creative_id}",
            "details": {},
        }

        if format_name:
            activity["details"]["primary"] = format_name
        if status:
            activity["details"]["secondary"] = status

        # Try to create task if event loop is running, otherwise skip
        try:
            asyncio.create_task(self.broadcast_activity(tenant_id, activity))
        except RuntimeError:
            # No event loop running - skip broadcast (not in async context)
            logger.debug(f"Skipping activity broadcast - no event loop available for creative {creative_id}")
            pass

    def log_error(self, tenant_id: str, principal_name: str, error_message: str, error_code: str | None = None):
        """Log an error activity."""
        activity: dict[str, Any] = {
            "type": "error",
            "principal_name": principal_name,
            "action": error_message,
            "details": {},
        }

        if error_code:
            activity["details"]["primary"] = f"Error {error_code}"

        # Try to create task if event loop is running, otherwise skip
        try:
            asyncio.create_task(self.broadcast_activity(tenant_id, activity))
        except RuntimeError:
            # No event loop running - skip broadcast (not in async context)
            logger.debug(f"Skipping activity broadcast - no event loop available for error: {error_message}")
            pass

    def _get_relative_time(self, timestamp: str) -> str:
        """Convert timestamp to relative time string."""
        try:
            if isinstance(timestamp, str):
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            else:
                dt = timestamp

            now = datetime.now(UTC)
            delta = now - dt

            if delta.days > 0:
                return f"{delta.days}d ago"
            elif delta.seconds > 3600:
                return f"{delta.seconds // 3600}h ago"
            elif delta.seconds > 60:
                return f"{delta.seconds // 60}m ago"
            else:
                return "Just now"
        except:
            return "Unknown"


# Global activity feed instance
activity_feed = ActivityFeed()
