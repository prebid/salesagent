"""Business Activity Service - Shows meaningful business events.

This service generates activity feed items focused on business-relevant events:
- Product inquiries (searches, recommendations)
- Media buy lifecycle (created, approved, launched, completed)
- Actions needed (approvals, creative reviews)
- Performance alerts (underdelivering, budget concerns)

NOT raw audit logs of every API call.
"""

import json
import logging
from datetime import UTC, datetime, timedelta

from src.core.database.database_session import get_db_session
from src.core.database.models import AuditLog

logger = logging.getLogger(__name__)


def get_business_activities(tenant_id: str, limit: int = 50) -> list[dict]:
    """Get all audit log activities for the dashboard.

    Shows ALL operations from audit logs, allowing users to see real-time activity.
    Users can filter on the frontend if needed.

    Args:
        tenant_id: The tenant to get activities for
        limit: Maximum number of activities to return

    Returns:
        List of activity dictionaries with:
        - type: Type of activity (derived from operation name)
        - title: Short summary
        - description: Detailed description
        - principal_name: Who did it
        - timestamp: When it happened
        - action_required: Whether user action is needed
        - metadata: Additional context from audit log details
    """
    activities = []

    try:
        with get_db_session() as db:
            # Get ALL recent audit logs (last 7 days) - no filtering by operation
            week_ago = datetime.now(UTC) - timedelta(days=7)
            recent_logs = (
                db.query(AuditLog)
                .filter(
                    AuditLog.tenant_id == tenant_id,
                    AuditLog.timestamp >= week_ago,
                )
                .order_by(AuditLog.timestamp.desc())
                .limit(limit * 2)  # Get more than we need in case we filter some out
                .all()
            )

            for log in recent_logs:
                # Parse details if available
                details = {}
                if log.details:
                    try:
                        if isinstance(log.details, str):
                            details = json.loads(log.details)
                        elif isinstance(log.details, dict):
                            details = log.details
                    except (json.JSONDecodeError, TypeError):
                        details = {}

                # Determine activity type based on operation
                operation = log.operation or "unknown"
                if operation.startswith("A2A."):
                    activity_type = "a2a"
                    icon = "📡"
                elif operation.startswith("AdCP."):
                    activity_type = "adcp"
                    icon = "🔌"
                elif operation.startswith("MCP."):
                    activity_type = "mcp"
                    icon = "🔗"
                else:
                    activity_type = "system"
                    icon = "⚙️"

                # Build title from operation
                operation_clean = operation.replace("AdCP.", "").replace("A2A.", "").replace("MCP.", "")
                principal_name = log.principal_name or "System"

                # Create descriptive title based on operation
                if "get_products" in operation or "list_products" in operation:
                    title = f"{principal_name} searched for products"
                elif "create_media_buy" in operation:
                    title = f"{principal_name} created media buy"
                elif "upload_creative" in operation or "sync_creative" in operation:
                    title = f"{principal_name} uploaded creative"
                elif "policy_check" in operation:
                    title = f"{principal_name} ran policy check"
                elif "list_creatives" in operation:
                    title = f"{principal_name} listed creatives"
                elif "explicit_skill_invocation" in operation:
                    title = f"{principal_name} invoked skill"
                else:
                    title = f"{principal_name}: {operation_clean}"

                # Build description
                if log.success:
                    status_text = "✓ Success"
                    badge_type = "success"
                else:
                    status_text = f"✗ Failed: {log.error_message or 'Unknown error'}"
                    badge_type = "error"

                # Extract key details for description
                description_parts = [status_text]
                if details.get("product_count"):
                    description_parts.append(f"{details['product_count']} products")
                if details.get("media_buy_id"):
                    description_parts.append(f"Buy: {details['media_buy_id']}")
                if details.get("creative_id"):
                    description_parts.append(f"Creative: {details['creative_id']}")

                description = " • ".join(description_parts)

                activities.append(
                    {
                        "type": activity_type,
                        "title": title,
                        "description": description,
                        "principal_name": principal_name,
                        "timestamp": log.timestamp,
                        "action_required": False,  # Will add workflow support later
                        "metadata": {
                            "operation": operation,
                            "success": log.success,
                            "details": details,
                        },
                    }
                )

    except Exception as e:
        logger.error(f"Error getting business activities for tenant {tenant_id}: {e}", exc_info=True)
        return []

    # Sort all activities by timestamp (newest first)
    activities.sort(key=lambda x: x["timestamp"], reverse=True)

    # Add relative time formatting
    now = datetime.now(UTC)
    for activity in activities[:limit]:
        timestamp = activity["timestamp"]
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)

        delta = now - timestamp
        if delta.days > 0:
            activity["time_relative"] = f"{delta.days}d ago"
        elif delta.seconds > 3600:
            activity["time_relative"] = f"{delta.seconds // 3600}h ago"
        elif delta.seconds > 60:
            activity["time_relative"] = f"{delta.seconds // 60}m ago"
        else:
            activity["time_relative"] = "Just now"

    return activities[:limit]
