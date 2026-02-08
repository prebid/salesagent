"""Base Workflow Manager for Human-in-the-Loop operations.

This module provides a base class for workflow management across different
ad server adapters. It handles common workflow operations like:
- Creating workflow steps
- Sending notifications
- Managing workflow state

Adapters extend this base class to add platform-specific workflow logic.
"""

import logging
import uuid
from datetime import datetime
from typing import Any

from src.core.config_loader import get_tenant_config
from src.core.database.database_session import get_db_session
from src.core.database.models import Context, ObjectWorkflowMapping, WorkflowStep
from src.core.schemas import MediaPackage

logger = logging.getLogger(__name__)


class BaseWorkflowManager:
    """Base class for adapter workflow managers.

    Provides common workflow operations that can be used by any ad server adapter.
    Subclasses should implement platform-specific workflow methods.
    """

    # Override in subclasses
    platform_name: str = "Unknown"
    platform_url_base: str = ""

    def __init__(self, tenant_id: str, principal=None, audit_logger=None, log_func=None):
        """Initialize workflow manager.

        Args:
            tenant_id: Tenant identifier for configuration
            principal: Principal object for context creation
            audit_logger: Audit logging instance
            log_func: Logging function for output
        """
        self.tenant_id = tenant_id
        self.principal = principal
        self.audit_logger = audit_logger
        self.log = log_func or logger.info

    def _generate_step_id(self, prefix: str = "w") -> str:
        """Generate a unique workflow step ID.

        Args:
            prefix: Single character prefix for the step ID

        Returns:
            6-character step ID (prefix + 5 hex chars)
        """
        return f"{prefix}{uuid.uuid4().hex[:5]}"

    def _create_context(self, db_session) -> str:
        """Create a workflow context.

        Args:
            db_session: Database session

        Returns:
            Context ID
        """
        context_id = f"ctx_{uuid.uuid4().hex[:12]}"
        context = Context(
            context_id=context_id,
            tenant_id=self.tenant_id,
            principal_id=self.principal.principal_id if self.principal else None,
        )
        db_session.add(context)
        return context_id

    def create_workflow_step(
        self,
        step_type: str,
        tool_name: str,
        action_details: dict[str, Any],
        object_type: str,
        object_id: str,
        object_action: str,
        step_prefix: str = "w",
        owner: str = "publisher",
        status: str = "approval",
        assigned_to: str | None = None,
        transaction_details: dict[str, Any] | None = None,
    ) -> str | None:
        """Create a generic workflow step.

        This is the core method for creating workflow steps that can be used
        by any adapter. It handles:
        - Creating the workflow context
        - Creating the workflow step
        - Creating the object mapping
        - Sending notifications

        Args:
            step_type: Type of step (e.g., "approval", "creation", "background_task")
            tool_name: Name of the tool/operation for this step
            action_details: Details about the action to be performed
            object_type: Type of object (e.g., "media_buy", "creative")
            object_id: ID of the object
            object_action: Action being performed (e.g., "create", "activate", "approve")
            step_prefix: Prefix for the step ID
            owner: Who owns this workflow step
            status: Initial status of the step
            assigned_to: Who the step is assigned to
            transaction_details: Additional transaction-specific details

        Returns:
            str: The workflow step ID if created successfully, None otherwise
        """
        step_id = self._generate_step_id(step_prefix)

        try:
            with get_db_session() as db_session:
                # Create context
                context_id = self._create_context(db_session)

                # Create workflow step
                workflow_step = WorkflowStep(
                    step_id=step_id,
                    context_id=context_id,
                    step_type=step_type,
                    tool_name=tool_name,
                    request_data=action_details,
                    status=status,
                    owner=owner,
                    assigned_to=assigned_to,
                    transaction_details=transaction_details or {},
                )
                db_session.add(workflow_step)

                # Create object mapping
                object_mapping = ObjectWorkflowMapping(
                    object_type=object_type,
                    object_id=object_id,
                    step_id=step_id,
                    action=object_action,
                )
                db_session.add(object_mapping)

                db_session.commit()

                self.log(f"Created workflow step {step_id} for {tool_name}")
                if self.audit_logger:
                    self.audit_logger.log_success(f"Created {tool_name} workflow step: {step_id}")

                # Send notification
                self._send_workflow_notification(step_id, action_details)

                return step_id

        except Exception as e:
            error_msg = f"Failed to create workflow step for {object_type} {object_id}: {str(e)}"
            self.log(f"[red]Error: {error_msg}[/red]")
            if self.audit_logger:
                self.audit_logger.log_warning(error_msg)
            return None

    def _send_workflow_notification(self, step_id: str, action_details: dict[str, Any]) -> None:
        """Send Slack notification for workflow step if configured.

        Args:
            step_id: The workflow step ID
            action_details: Details about the workflow step
        """
        try:
            tenant_config = get_tenant_config(self.tenant_id)
            slack_webhook_url = tenant_config.get("slack", {}).get("webhook_url")

            if not slack_webhook_url:
                self.log("[yellow]No Slack webhook configured - skipping notification[/yellow]")
                return

            import requests

            # Get notification styling based on action type
            notification = self._get_notification_details(step_id, action_details)

            # Build Slack message
            slack_payload = {
                "attachments": [
                    {
                        "color": notification["color"],
                        "title": notification["title"],
                        "text": notification["description"],
                        "fields": [
                            {"title": "Step ID", "value": step_id, "short": True},
                            {
                                "title": "Platform",
                                "value": action_details.get("platform", self.platform_name),
                                "short": True,
                            },
                            {
                                "title": "Automation Mode",
                                "value": action_details.get("automation_mode", "unknown").replace("_", " ").title(),
                                "short": True,
                            },
                            {
                                "title": "Action Required",
                                "value": action_details.get("instructions", ["Check admin dashboard"])[0],
                                "short": False,
                            },
                        ],
                        "footer": "AdCP Sales Agent",
                        "ts": int(datetime.now().timestamp()),
                    }
                ]
            }

            # Send notification
            response = requests.post(
                slack_webhook_url,
                json=slack_payload,
                timeout=10,
                headers={"Content-Type": "application/json"},
            )

            if response.status_code == 200:
                self.log(f"Sent Slack notification for workflow step {step_id}")
                if self.audit_logger:
                    self.audit_logger.log_success(f"Sent Slack notification for workflow step: {step_id}")
            else:
                self.log(f"[yellow]Slack notification failed with status {response.status_code}[/yellow]")

        except Exception as e:
            self.log(f"[yellow]Failed to send Slack notification: {str(e)}[/yellow]")
            # Don't fail the workflow creation if notification fails

    def _get_notification_details(self, step_id: str, action_details: dict[str, Any]) -> dict[str, str]:
        """Get notification styling based on action type.

        Override in subclasses to customize notification appearance.

        Args:
            step_id: The workflow step ID
            action_details: Details about the workflow step

        Returns:
            Dictionary with title, description, and color
        """
        action_type = action_details.get("action_type", "workflow_step")
        automation_mode = action_details.get("automation_mode", "unknown")

        # Default notification styling
        if "manual" in automation_mode.lower() or "creation" in action_type.lower():
            return {
                "title": f"Manual {self.platform_name} Action Required",
                "description": "Manual mode activated - human intervention needed",
                "color": "#FF9500",  # Orange
            }
        elif "approval" in automation_mode.lower() or "activate" in action_type.lower():
            return {
                "title": f"{self.platform_name} Approval Required",
                "description": "Approval needed for operation",
                "color": "#FFD700",  # Gold
            }
        elif "background" in automation_mode.lower() or "working" in action_details.get("status", ""):
            return {
                "title": f"{self.platform_name} Background Task Started",
                "description": "Background processing in progress",
                "color": "#36A2EB",  # Blue
            }
        else:
            return {
                "title": "Workflow Step Requires Attention",
                "description": f"Workflow step {step_id} needs human intervention",
                "color": "#36A2EB",  # Blue
            }

    def build_packages_summary(self, packages: list[MediaPackage]) -> list[dict[str, Any]]:
        """Build a summary of packages for workflow action details.

        Args:
            packages: List of MediaPackage objects

        Returns:
            List of package summary dictionaries
        """
        return [
            {
                "name": pkg.name,
                "impressions": pkg.impressions,
                "cpm": pkg.cpm,
            }
            for pkg in packages
        ]
