"""Activity stream blueprint for Server-Sent Events (SSE)."""

import json
import logging
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from flask import Blueprint, Response, jsonify, request
from sqlalchemy import select

from src.admin.utils import require_tenant_access
from src.core.database.database_session import get_db_session
from src.core.database.models import AuditLog

logger = logging.getLogger(__name__)

# Create blueprint
activity_stream_bp = Blueprint("activity_stream", __name__)

# Rate limiting for SSE connections
MAX_CONNECTIONS_PER_TENANT = 10
connection_counts: dict[str, int] = defaultdict(int)
connection_timestamps: dict[str, list[float]] = defaultdict(list)


def format_activity_from_audit_log(audit_log: AuditLog) -> dict:
    """Convert AuditLog database record to activity feed format with rich details."""
    # Parse operation to extract method name
    operation_parts = audit_log.operation.split(".", 1)
    adapter_name = operation_parts[0] if len(operation_parts) > 1 else "system"
    method = operation_parts[1] if len(operation_parts) > 1 else audit_log.operation

    # Determine activity type based on operation
    if "media_buy" in method.lower():
        activity_type = "media-buy"
    elif "creative" in method.lower():
        activity_type = "creative"
    elif "error" in method.lower() or not audit_log.success:
        activity_type = "error"
    elif "get_products" in method.lower():
        activity_type = "product-query"
    elif "human" in method.lower() or "approval" in method.lower():
        activity_type = "human-task"
    else:
        activity_type = "api-call"

    # Build rich activity details based on operation type
    details = {}
    full_details = {}
    action_required = False

    # Parse the details JSON if available
    parsed_details = {}
    if audit_log.details:
        try:
            # details is already a dict from JSONType, not a string
            if isinstance(audit_log.details, dict):
                parsed_details = audit_log.details
            else:
                parsed_details = json.loads(audit_log.details)
        except (json.JSONDecodeError, TypeError):
            parsed_details = {}

    # Format based on operation type
    if "get_products" in method.lower():
        details["primary"] = f"Found {parsed_details.get('product_count', 0)} products"
        if parsed_details.get("brief"):
            details["secondary"] = (
                f'Brief: "{parsed_details["brief"][:50]}..."'
                if len(parsed_details.get("brief", "")) > 50
                else f'Brief: "{parsed_details.get("brief")}"'
            )
        if parsed_details.get("products"):
            full_details["products"] = parsed_details["products"]
            full_details["promoted"] = parsed_details.get("promoted_product", "No specific promotion")

    elif "create_media_buy" in method.lower():
        if parsed_details.get("budget"):
            details["primary"] = f"Budget: ${parsed_details['budget']:,.0f}"
        if parsed_details.get("duration_days"):
            details["secondary"] = f"Duration: {parsed_details['duration_days']} days"
        if parsed_details.get("targeting"):
            full_details["targeting"] = parsed_details["targeting"]
        full_details["media_buy_id"] = parsed_details.get("media_buy_id", "N/A")

    elif "upload_creative" in method.lower():
        details["primary"] = f"Format: {parsed_details.get('format', 'Unknown')}"
        if parsed_details.get("file_size"):
            details["secondary"] = f"Size: {parsed_details['file_size']}"
        full_details["creative_id"] = parsed_details.get("creative_id", "N/A")
        full_details["status"] = parsed_details.get("status", "pending")

    elif "human" in method.lower() or "approval" in method.lower():
        details["primary"] = "⚠️ Human approval required"
        details["secondary"] = parsed_details.get("task_type", "Review required")
        full_details["task_id"] = parsed_details.get("task_id")
        full_details["task_details"] = parsed_details.get("details", {})
        action_required = True

    elif adapter_name == "A2A" or audit_log.operation.startswith("A2A."):
        # Handle A2A operations with rich details
        details["primary"] = "🔄 A2A Protocol"
        if parsed_details.get("query"):
            details["secondary"] = (
                f'Query: "{parsed_details["query"][:60]}..."'
                if len(parsed_details.get("query", "")) > 60
                else f'Query: "{parsed_details.get("query")}"'
            )

        # Include all A2A details for expansion
        full_details = parsed_details.copy()  # Show all A2A details when expanded

    elif not audit_log.success:
        details["primary"] = "❌ Failed"
        if audit_log.error_message:
            details["secondary"] = (
                audit_log.error_message[:75] + "..." if len(audit_log.error_message) > 75 else audit_log.error_message
            )
        full_details["error_details"] = audit_log.error_message
    else:
        # Default success case
        details["primary"] = "✅ Success"
        if parsed_details:
            # Show first interesting field from details
            for key in ["message", "result", "count", "status"]:
                if key in parsed_details:
                    details["secondary"] = str(parsed_details[key])[:75]
                    break

    # Calculate relative time
    from typing import cast

    now = datetime.now(UTC)
    timestamp = cast(datetime, audit_log.timestamp)
    if timestamp.tzinfo is None:
        # Handle naive datetime (assume UTC)
        audit_timestamp = timestamp.replace(tzinfo=UTC)
    else:
        audit_timestamp = timestamp

    delta = now - audit_timestamp
    if delta.days > 0:
        time_relative = f"{delta.days}d ago"
    elif delta.seconds > 3600:
        time_relative = f"{delta.seconds // 3600}h ago"
    elif delta.seconds > 60:
        time_relative = f"{delta.seconds // 60}m ago"
    else:
        time_relative = "Just now"

    return {
        "id": audit_log.log_id,
        "type": activity_type,
        "principal_name": audit_log.principal_name or "System",
        "action": f"Called {method}",
        "details": details,
        "full_details": full_details,
        "timestamp": timestamp.isoformat(),
        "time_relative": time_relative,
        "action_required": action_required,
        "operation": audit_log.operation,
        "success": audit_log.success,
    }


def get_recent_activities(tenant_id: str, since: datetime = None, limit: int = 50) -> list[dict]:
    """Get recent activities for a tenant from the database."""
    # Validate input parameters
    if not tenant_id or not isinstance(tenant_id, str) or len(tenant_id) > 50:
        logger.warning(f"Invalid tenant_id provided: {tenant_id}")
        return []

    # Enforce reasonable limits
    limit = max(1, min(limit, 100))  # Between 1 and 100

    try:
        with get_db_session() as db_session:
            stmt = select(AuditLog).filter(AuditLog.tenant_id == tenant_id)

            if since:
                stmt = stmt.filter(AuditLog.timestamp > since)

            # Order by timestamp descending and limit results
            audit_logs = db_session.scalars(stmt.order_by(AuditLog.timestamp.desc()).limit(limit)).all()

            # Convert to activity format
            activities = []
            for audit_log in audit_logs:
                try:
                    activity = format_activity_from_audit_log(audit_log)
                    activities.append(activity)
                except Exception as e:
                    logger.warning(f"Failed to format activity from audit log {audit_log.log_id}: {e}")

            return activities

    except Exception as e:
        logger.error(f"Failed to query activities for tenant {tenant_id}: {e}")
        return []


@activity_stream_bp.route("/tenant/<tenant_id>/activity", methods=["GET"])
@require_tenant_access(api_mode=False)  # Use normal redirect auth for polling
def activity_feed(tenant_id, **kwargs):
    """JSON endpoint for polling recent activities."""

    # Validate tenant_id
    if not tenant_id or not isinstance(tenant_id, str) or len(tenant_id) > 50:
        logger.error(f"Invalid tenant_id for activity endpoint: {tenant_id}")
        return jsonify({"error": "Invalid tenant ID"}), 400

    try:
        # Get recent activities (last 50)
        activities = get_recent_activities(tenant_id, limit=50)

        logger.info(f"Activity polling request - tenant: {tenant_id}, activities: {len(activities)}")

        return jsonify({"activities": activities, "timestamp": datetime.now(UTC).isoformat(), "count": len(activities)})

    except Exception as e:
        logger.error(f"Error getting activities for tenant {tenant_id}: {e}")
        return jsonify({"error": "Failed to get activities"}), 500


@activity_stream_bp.route("/tenant/<tenant_id>/events", methods=["GET", "HEAD"])
@require_tenant_access(api_mode=True)
def activity_events(tenant_id, **kwargs):
    """Server-Sent Events endpoint for real-time activity updates."""

    # Handle HEAD requests for authentication checks
    if request.method == "HEAD":
        logger.info(f"HEAD request for SSE endpoint - tenant: {tenant_id}, authenticated: True")
        return Response(status=200)

    # Validate tenant_id
    if not tenant_id or not isinstance(tenant_id, str) or len(tenant_id) > 50:
        logger.error(f"Invalid tenant_id for SSE endpoint: {tenant_id}")
        return Response("Invalid tenant ID", status=400)

    # Rate limiting: Clean up old timestamps (older than 1 minute)
    now = datetime.now(UTC)
    connection_timestamps[tenant_id] = [
        ts for ts in connection_timestamps[tenant_id] if (now - ts).total_seconds() < 60
    ]

    # Check rate limit
    active_connections = len(connection_timestamps[tenant_id])
    if active_connections >= MAX_CONNECTIONS_PER_TENANT:
        logger.warning(
            f"RATE LIMIT HIT - tenant: {tenant_id}, active_connections: {active_connections}, max: {MAX_CONNECTIONS_PER_TENANT}"
        )
        return Response("Too many connections. Please wait before reconnecting.", status=429)

    # Record this connection
    connection_timestamps[tenant_id].append(now)
    connection_counts[tenant_id] += 1

    logger.info(
        f"SSE GET request starting - tenant: {tenant_id}, active_connections: {active_connections + 1}, total_connections: {connection_counts[tenant_id]}"
    )

    def generate():
        """Generator function that yields SSE formatted data."""
        # Track resources for cleanup
        cleanup_needed = False

        try:
            # Send initial historical data
            logger.info(f"Starting SSE stream for tenant {tenant_id}")
            recent_activities = get_recent_activities(tenant_id, limit=50)

            for activity in reversed(recent_activities):  # Reverse to show oldest first
                data = json.dumps(activity)
                yield f"data: {data}\n\n"

            # Keep track of last check time
            last_check = datetime.now(UTC)
            cleanup_needed = True

            # Poll for new activities every 2 seconds
            while True:
                try:
                    # Check for new activities since last check
                    new_activities = get_recent_activities(
                        tenant_id,
                        since=last_check - timedelta(seconds=1),  # Small overlap to avoid missing events
                        limit=10,
                    )

                    # Send new activities
                    for activity in reversed(new_activities):  # Newest activities last
                        data = json.dumps(activity)
                        yield f"data: {data}\n\n"

                    # Update last check time
                    if new_activities:
                        # Use timestamp of newest activity
                        newest_timestamp_str = new_activities[0]["timestamp"]
                        newest_timestamp = datetime.fromisoformat(newest_timestamp_str.replace("Z", "+00:00"))
                        last_check = max(last_check, newest_timestamp)
                    else:
                        last_check = datetime.now(UTC)

                    # Send heartbeat to keep connection alive
                    yield ": heartbeat\n\n"

                    # Wait before next poll
                    time.sleep(2)

                except GeneratorExit:
                    logger.info(f"SSE client disconnected for tenant {tenant_id}")
                    cleanup_needed = True
                    break
                except Exception as e:
                    logger.error(f"Error in SSE stream for tenant {tenant_id}: {e}")
                    cleanup_needed = True
                    # Send error event
                    error_data = json.dumps(
                        {
                            "type": "error",
                            "message": "Stream error occurred",
                            "timestamp": datetime.now(UTC).isoformat(),
                        }
                    )
                    yield f"event: error\ndata: {error_data}\n\n"
                    time.sleep(5)  # Wait longer after error

        except Exception as e:
            logger.error(f"Failed to start SSE stream for tenant {tenant_id}: {e}")
            cleanup_needed = True
            error_data = json.dumps(
                {
                    "type": "error",
                    "message": "Failed to start activity stream",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
            yield f"event: error\ndata: {error_data}\n\n"
        finally:
            # Clean up resources
            if cleanup_needed:
                old_count = connection_counts[tenant_id]
                connection_counts[tenant_id] = max(0, connection_counts[tenant_id] - 1)
                logger.info(
                    f"Cleaning up SSE stream resources for tenant {tenant_id} - connection count: {old_count} -> {connection_counts[tenant_id]}"
                )
                # Force garbage collection for any lingering resources
                import gc

                gc.collect()

    # Set appropriate headers for SSE
    response = Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )

    return response


@activity_stream_bp.route("/tenant/<tenant_id>/activities", methods=["GET"])
@require_tenant_access()
def get_activities_api(tenant_id, **kwargs):
    """REST API endpoint to get recent activities (fallback for non-SSE clients)."""
    try:
        # Get optional since parameter
        since_param = request.args.get("since")
        since = None
        if since_param:
            try:
                since = datetime.fromisoformat(since_param.replace("Z", "+00:00"))
            except ValueError:
                logger.warning(f"Invalid since parameter: {since_param}")

        # Get optional limit parameter
        limit = min(int(request.args.get("limit", 50)), 100)  # Max 100 activities

        activities = get_recent_activities(tenant_id, since=since, limit=limit)

        return {"activities": activities, "count": len(activities), "timestamp": datetime.now(UTC).isoformat()}

    except Exception as e:
        logger.error(f"Failed to get activities for tenant {tenant_id}: {e}")
        return {"error": "Failed to retrieve activities"}, 500
