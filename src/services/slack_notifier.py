"""
Slack notification system for AdCP Sales Agent.
Sends notifications for new tasks and approvals via Slack webhooks.
"""

import logging
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class SlackNotifier:
    """Handles sending notifications to Slack channels via webhooks."""

    def __init__(
        self,
        webhook_url: str | None = None,
        audit_webhook_url: str | None = None,
        tenant_config: dict[str, Any] | None = None,
    ):
        """
        Initialize Slack notifier.

        Args:
            webhook_url: Slack webhook URL. If not provided, checks tenant config then SLACK_WEBHOOK_URL env var.
            audit_webhook_url: Separate webhook for audit logs. If not provided, checks tenant config then SLACK_AUDIT_WEBHOOK_URL env var.
            tenant_config: Tenant configuration dict to check for webhook URLs
        """
        # Only use tenant config - no fallback to env vars
        if tenant_config:
            # Support both nested features dict and top-level keys
            if "features" in tenant_config and isinstance(tenant_config["features"], dict):
                features = tenant_config["features"]
                self.webhook_url = webhook_url or features.get("slack_webhook_url")
                self.audit_webhook_url = audit_webhook_url or features.get("slack_audit_webhook_url")
            else:
                # Top-level keys (from tenant_utils.tenant_to_dict)
                self.webhook_url = webhook_url or tenant_config.get("slack_webhook_url")
                self.audit_webhook_url = audit_webhook_url or tenant_config.get("slack_audit_webhook_url")
        else:
            # If no tenant config, disable Slack
            self.webhook_url = webhook_url
            self.audit_webhook_url = audit_webhook_url

        self.enabled = bool(self.webhook_url)
        self.audit_enabled = bool(self.audit_webhook_url)

        if self.enabled:
            # Validate webhook URL format
            parsed = urlparse(self.webhook_url)
            if not all([parsed.scheme, parsed.netloc]):
                logger.error(f"Invalid Slack webhook URL format: {self.webhook_url}")
                self.enabled = False
        else:
            logger.info("Slack notifications disabled (no webhook URL configured)")

        if self.audit_enabled:
            # Validate audit webhook URL format
            parsed = urlparse(self.audit_webhook_url)
            if not all([parsed.scheme, parsed.netloc]):
                logger.error(f"Invalid Slack audit webhook URL format: {self.audit_webhook_url}")
                self.audit_enabled = False
            else:
                logger.info("Slack audit logging enabled")

    def send_message(self, text: str, blocks: list[dict[str, Any]] | None = None, tenant_id: str | None = None) -> bool:
        """
        Send a message to Slack with retry logic.

        Args:
            text: Plain text message (fallback for notifications)
            blocks: Rich Block Kit blocks for formatted messages
            tenant_id: Optional tenant ID for tracking delivery

        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            return False

        payload = {"text": text}
        if blocks:
            payload["blocks"] = blocks

        # Use webhook delivery service with retry logic
        from src.core.webhook_delivery import WebhookDelivery, deliver_webhook_with_retry

        delivery = WebhookDelivery(
            webhook_url=self.webhook_url,
            payload=payload,
            headers={"Content-Type": "application/json"},
            max_retries=3,
            timeout=10,
            event_type="slack.notification",
            tenant_id=tenant_id,
        )

        success, result = deliver_webhook_with_retry(delivery)

        if not success:
            logger.error(
                f"Failed to send Slack notification after {result['attempts']} attempts: "
                f"{result.get('error', 'Unknown error')}"
            )

        return success

    def notify_new_task(
        self,
        task_id: str,
        task_type: str,
        principal_name: str,
        media_buy_id: str | None = None,
        details: dict[str, Any] | None = None,
        tenant_name: str | None = None,
        tenant_id: str | None = None,
    ) -> bool:
        """
        Send notification for a new task requiring approval.

        Args:
            task_id: Unique task identifier
            task_type: Type of task (e.g., 'create_media_buy', 'update_media_buy')
            principal_name: Name of the principal requesting the action
            media_buy_id: Associated media buy ID if applicable
            details: Additional task details
            tenant_name: Tenant/publisher name
            tenant_id: Tenant ID for tenant-specific URL routing

        Returns:
            True if notification sent successfully
        """
        # Create formatted message with blocks
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": "🔔 New Task Requires Approval"}},
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Task ID:*\n`{task_id}`"},
                    {"type": "mrkdwn", "text": f"*Type:*\n{task_type.replace('_', ' ').title()}"},
                    {"type": "mrkdwn", "text": f"*Principal:*\n{principal_name}"},
                    {"type": "mrkdwn", "text": f"*Tenant:*\n{tenant_name or 'Default'}"},
                ],
            },
        ]

        # Add media buy info if available
        if media_buy_id:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Media Buy:* `{media_buy_id}`"}})

        # Add details if provided
        if details:
            detail_text = self._format_details(details)
            if detail_text:
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Details:*\n{detail_text}"}})

        # Add action buttons with tenant-specific URL
        admin_url = os.getenv("ADMIN_UI_URL", "http://localhost:8001")
        script_name = "/admin" if os.environ.get("PRODUCTION") == "true" else ""
        if tenant_id:
            # Tenant-specific workflows page
            operations_url = f"{admin_url}{script_name}/tenant/{tenant_id}/workflows"
        else:
            # Global workflows page (fallback)
            operations_url = f"{admin_url}{script_name}/workflows"

        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View in Admin UI"},
                        "url": operations_url,
                        "style": "primary",
                    }
                ],
            }
        )

        # Add timestamp
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Created at {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
                    }
                ],
            }
        )

        # Fallback text for notifications
        fallback_text = f"New task {task_id} ({task_type}) from {principal_name} requires approval"

        return self.send_message(fallback_text, blocks)

    def notify_task_completed(
        self, task_id: str, task_type: str, completed_by: str, success: bool = True, error_message: str | None = None
    ) -> bool:
        """
        Send notification when a task is completed.

        Args:
            task_id: Task identifier
            task_type: Type of task
            completed_by: User who completed the task
            success: Whether task completed successfully
            error_message: Error message if task failed

        Returns:
            True if notification sent successfully
        """
        emoji = "✅" if success else "❌"
        status = "Completed" if success else "Failed"

        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": f"{emoji} Task {status}"}},
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Task ID:*\n`{task_id}`"},
                    {"type": "mrkdwn", "text": f"*Type:*\n{task_type.replace('_', ' ').title()}"},
                    {"type": "mrkdwn", "text": f"*Completed By:*\n{completed_by}"},
                    {"type": "mrkdwn", "text": f"*Status:*\n{status}"},
                ],
            },
        ]

        if error_message:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Error:*\n```{error_message}```"}})

        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Completed at {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
                    }
                ],
            }
        )

        fallback_text = f"Task {task_id} {status.lower()} by {completed_by}"

        return self.send_message(fallback_text, blocks)

    def notify_creative_pending(
        self,
        creative_id: str,
        principal_name: str,
        format_type: str,
        media_buy_id: str | None = None,
        tenant_id: str | None = None,
        ai_review_reason: str | None = None,
    ) -> bool:
        """
        Send notification for a creative pending approval.

        Args:
            creative_id: Creative identifier
            principal_name: Principal who submitted the creative
            format_type: Creative format (e.g., 'video', 'display_300x250')
            media_buy_id: Associated media buy if applicable
            tenant_id: Tenant ID for building correct URL
            ai_review_reason: AI review reasoning if available

        Returns:
            True if notification sent successfully
        """
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": "🎨 New Creative Pending Approval"}},
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Creative ID:*\n`{creative_id}`"},
                    {"type": "mrkdwn", "text": f"*Format:*\n{format_type}"},
                    {"type": "mrkdwn", "text": f"*Principal:*\n{principal_name}"},
                ],
            },
        ]

        if media_buy_id:
            blocks[1]["fields"].append({"type": "mrkdwn", "text": f"*Media Buy:*\n`{media_buy_id}`"})

        # Add AI review reason if available
        if ai_review_reason:
            blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": f"🤖 *AI Review:*\n{ai_review_reason}"}}
            )

        # Build correct URL to specific creative
        admin_url = os.getenv("ADMIN_UI_URL", "http://localhost:8001")
        script_name = "/admin" if os.environ.get("PRODUCTION") == "true" else ""
        if tenant_id:
            # Link directly to the specific creative using anchor
            # Correct URL pattern: /tenant/{tenant_id}/creatives/review#{creative_id}
            review_url = f"{admin_url}{script_name}/tenant/{tenant_id}/creatives/review#{creative_id}"
        else:
            # Fallback to workflows page if tenant_id not provided
            review_url = f"{admin_url}{script_name}/workflows"

        blocks.extend(
            [
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Review Creative"},
                            "url": review_url,
                            "style": "primary",
                        }
                    ],
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"Submitted at {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
                        }
                    ],
                },
            ]
        )

        fallback_text = f"New {format_type} creative from {principal_name} pending approval"

        return self.send_message(fallback_text, blocks)

    def notify_audit_log(
        self,
        operation: str,
        principal_name: str,
        success: bool,
        adapter_id: str,
        tenant_name: str | None = None,
        error_message: str | None = None,
        details: dict[str, Any] | None = None,
        security_alert: bool = False,
    ) -> bool:
        """
        Send audit log entry to Slack audit channel.

        Args:
            operation: Operation performed (e.g., 'create_media_buy', 'update_media_buy')
            principal_name: Principal who performed the operation
            success: Whether operation succeeded
            adapter_id: Adapter used for the operation
            tenant_name: Tenant/publisher name
            error_message: Error message if operation failed
            details: Additional operation details
            security_alert: Whether this is a security-related event

        Returns:
            True if notification sent successfully
        """
        if not self.audit_enabled:
            return False

        # Determine emoji and color based on event type
        if security_alert:
            emoji = "🚨"
            color = "danger"
            header_text = "Security Alert"
        elif not success:
            emoji = "❌"
            color = "danger"
            header_text = "Operation Failed"
        else:
            emoji = "📝"
            color = "good"
            header_text = "Audit Log"

        # Create message blocks
        blocks = [{"type": "header", "text": {"type": "plain_text", "text": f"{emoji} {header_text}"}}]

        # Add main info section
        fields = [
            {"type": "mrkdwn", "text": f"*Operation:*\n{operation}"},
            {"type": "mrkdwn", "text": f"*Principal:*\n{principal_name}"},
        ]

        if tenant_name:
            fields.append({"type": "mrkdwn", "text": f"*Tenant:*\n{tenant_name}"})

        fields.append({"type": "mrkdwn", "text": f"*Status:*\n{'✅ Success' if success else '❌ Failed'}"})

        blocks.append({"type": "section", "fields": fields})

        # Add error message if present
        if error_message:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Error:*\n```{error_message}```"}})

        # Add details if present
        if details:
            detail_text = self._format_audit_details(details)
            if detail_text:
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Details:*\n{detail_text}"}})

        # Add color attachment for visual indicator
        attachments = [{"color": color, "blocks": blocks}]

        # Add timestamp
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Logged at {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')} | Adapter: {adapter_id}",
                    }
                ],
            }
        )

        # Fallback text
        fallback_text = f"{emoji} {operation} by {principal_name} - {'Success' if success else 'Failed'}"

        # Send to audit webhook with retry logic
        payload = {"text": fallback_text, "attachments": attachments}

        from src.core.webhook_delivery import WebhookDelivery, deliver_webhook_with_retry

        delivery = WebhookDelivery(
            webhook_url=self.audit_webhook_url,
            payload=payload,
            headers={"Content-Type": "application/json"},
            max_retries=3,
            timeout=10,
            event_type="slack.audit_log",
            tenant_id=tenant_name,  # Use tenant_name as identifier
        )

        success_delivery, result = deliver_webhook_with_retry(delivery)

        if not success_delivery:
            logger.error(
                f"Failed to send Slack audit notification after {result['attempts']} attempts: "
                f"{result.get('error', 'Unknown error')}"
            )

        return success_delivery

    def _format_details(self, details: dict[str, Any]) -> str:
        """Format task details for Slack message."""
        formatted_parts = []

        # Common fields to highlight
        highlight_fields = [
            "budget",
            "daily_budget",
            "total_budget",
            "start_date",
            "end_date",
            "flight_start_date",
            "flight_end_date",
            "targeting_overlay",
            "performance_goal",
        ]

        for field in highlight_fields:
            if field in details:
                value = details[field]
                if "budget" in field and isinstance(value, int | float):
                    value = f"${value:,.2f}"
                elif "date" in field:
                    value = str(value)
                field_name = field.replace("_", " ").title()
                formatted_parts.append(f"• {field_name}: {value}")

        return "\n".join(formatted_parts) if formatted_parts else None

    def _format_audit_details(self, details: dict[str, Any]) -> str:
        """Format audit details for Slack message."""
        formatted_parts = []

        # Important audit fields
        important_fields = [
            "media_buy_id",
            "creative_id",
            "task_id",
            "budget",
            "total_budget",
            "daily_budget",
            "action",
            "resolution",
            "package_id",
        ]

        for field in important_fields:
            if field in details:
                value = details[field]
                if "budget" in field and isinstance(value, int | float):
                    value = f"${value:,.2f}"
                field_name = field.replace("_", " ").title()
                formatted_parts.append(f"• {field_name}: `{value}`")

        # Add any custom fields not in the important list
        for field, value in details.items():
            if field not in important_fields and not field.startswith("_"):
                field_name = field.replace("_", " ").title()
                formatted_parts.append(f"• {field_name}: {value}")

        return "\n".join(formatted_parts[:5]) if formatted_parts else None  # Limit to 5 items

    def notify_media_buy_event(
        self,
        event_type: str,
        media_buy_id: str | None,
        principal_name: str,
        details: dict[str, Any],
        tenant_name: str | None = None,
        tenant_id: str | None = None,
        success: bool = True,
        error_message: str | None = None,
    ) -> bool:
        """
        Send notification for media buy events (creation, update, activation, etc.).

        Args:
            event_type: Type of event ('created', 'approval_required', 'failed', 'activated', etc.)
            media_buy_id: Media buy identifier (can be pending ID for approval cases)
            principal_name: Principal who initiated the action
            details: Event-specific details
            tenant_name: Tenant/publisher name
            tenant_id: Tenant ID for tenant-specific URL routing
            success: Whether the event was successful
            error_message: Error message if event failed

        Returns:
            True if notification sent successfully
        """
        # Define event-specific formatting
        event_configs = {
            "created": {
                "emoji": "🎉",
                "title": "Media Buy Created Successfully",
                "color": "good",
                "button_text": "View Campaign",
                "button_style": "primary",
            },
            "approval_required": {
                "emoji": "🔔",
                "title": "Media Buy Requires Approval",
                "color": "warning",
                "button_text": "Review Request",
                "button_style": "primary",
            },
            "config_approval_required": {
                "emoji": "⚙️",
                "title": "Media Buy Requires Configuration Approval",
                "color": "warning",
                "button_text": "Review Configuration",
                "button_style": "primary",
            },
            "failed": {
                "emoji": "❌",
                "title": "Media Buy Creation Failed",
                "color": "danger",
                "button_text": "View Error Details",
                "button_style": "danger",
            },
            "activated": {
                "emoji": "🚀",
                "title": "Media Buy Activated",
                "color": "good",
                "button_text": "View Performance",
                "button_style": "primary",
            },
        }

        config = event_configs.get(
            event_type,
            {
                "emoji": "📢",
                "title": f"Media Buy {event_type.replace('_', ' ').title()}",
                "color": "good" if success else "danger",
                "button_text": "View Details",
                "button_style": "primary",
            },
        )

        # Build main fields
        fields = [
            {"type": "mrkdwn", "text": f"*Principal:*\n{principal_name}"},
        ]

        if media_buy_id:
            fields.append({"type": "mrkdwn", "text": f"*Media Buy ID:*\n`{media_buy_id}`"})

        if details.get("po_number"):
            fields.append({"type": "mrkdwn", "text": f"*Campaign:*\n{details['po_number']}"})

        if details.get("total_budget"):
            budget = details["total_budget"]
            fields.append({"type": "mrkdwn", "text": f"*Budget:*\n${budget:,.2f}"})

        if tenant_name:
            fields.append({"type": "mrkdwn", "text": f"*Publisher:*\n{tenant_name}"})

        # Create blocks
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": f"{config['emoji']} {config['title']}"}},
            {"type": "section", "fields": fields},
        ]

        # Add campaign details section
        detail_parts = []
        if details.get("product_ids"):
            products = details["product_ids"][:3]  # Limit to first 3
            product_text = ", ".join(products)
            if len(details["product_ids"]) > 3:
                product_text += f" (and {len(details['product_ids']) - 3} more)"
            detail_parts.append(f"*Products:* {product_text}")

        if details.get("start_time") and details.get("end_time"):
            try:
                start = datetime.fromisoformat(details["start_time"].replace("Z", "+00:00"))
                end = datetime.fromisoformat(details["end_time"].replace("Z", "+00:00"))
                duration = (end - start).days + 1
                detail_parts.append(f"*Duration:* {duration} days")
                detail_parts.append(f"*Flight:* {start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}")
            except Exception:
                pass  # Skip if date parsing fails

        if details.get("approval_reason"):
            detail_parts.append(f"*Approval Required:* {details['approval_reason']}")

        if detail_parts:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(detail_parts)}})

        # Add error section if applicable
        if error_message:
            error_text = error_message[:500] + ("..." if len(error_message) > 500 else "")
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Error:*\n```{error_text}```"}})

        # Add action button with tenant-specific URL
        admin_url = os.getenv("ADMIN_UI_URL", "http://localhost:8001")
        script_name = "/admin" if os.environ.get("PRODUCTION") == "true" else ""
        if tenant_id and media_buy_id:
            # Link to specific media buy in tenant context
            operations_url = f"{admin_url}{script_name}/tenant/{tenant_id}/workflows#{media_buy_id}"
        elif tenant_id:
            # Tenant-specific workflows page
            operations_url = f"{admin_url}{script_name}/tenant/{tenant_id}/workflows"
        else:
            # Global workflows page (fallback)
            operations_url = f"{admin_url}{script_name}/workflows"

        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": config["button_text"]},
                        "url": operations_url,
                        "style": config["button_style"],
                    }
                ],
            }
        )

        # Add timestamp
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Event at {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
                    }
                ],
            }
        )

        # Create fallback text
        fallback_text = f"{config['emoji']} {config['title']}: {media_buy_id or 'pending'} by {principal_name}"

        # Use attachment for color coding
        attachments = [{"color": config["color"], "blocks": blocks}] if config["color"] != "good" else None

        # Build payload
        payload = {"text": fallback_text}
        if attachments:
            payload["attachments"] = attachments
        else:
            payload["blocks"] = blocks

        # Send message with retry logic
        from src.core.webhook_delivery import WebhookDelivery, deliver_webhook_with_retry

        delivery = WebhookDelivery(
            webhook_url=self.webhook_url,
            payload=payload,
            headers={"Content-Type": "application/json"},
            max_retries=3,
            timeout=10,
            event_type="slack.media_buy_event",
            tenant_id=tenant_name,
            object_id=media_buy_id,
        )

        success_delivery, result = deliver_webhook_with_retry(delivery)

        if not success_delivery:
            logger.error(
                f"Failed to send media buy event notification after {result['attempts']} attempts: "
                f"{result.get('error', 'Unknown error')}"
            )

        return success_delivery


# Global instance (will be overridden per-tenant in actual usage)
slack_notifier = SlackNotifier()


def get_slack_notifier(tenant_config: dict[str, Any] | None = None) -> SlackNotifier:
    """Get a Slack notifier instance configured for the specific tenant."""
    return SlackNotifier(tenant_config=tenant_config)
