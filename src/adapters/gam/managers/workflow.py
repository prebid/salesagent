"""
GAM Workflow Manager - Human-in-the-Loop Workflow Management

This module handles workflow step creation, notification, and management
for Google Ad Manager operations requiring human intervention.
"""

import logging
import uuid
from datetime import datetime
from typing import Any

from src.core.config_loader import get_tenant_config
from src.core.database.database_session import get_db_session
from src.core.database.models import Context, ObjectWorkflowMapping, WorkflowStep
from src.core.schemas import CreateMediaBuyRequest, MediaPackage

logger = logging.getLogger(__name__)


class GAMWorkflowManager:
    """Manages Human-in-the-Loop workflows for Google Ad Manager operations."""

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

    def create_activation_workflow_step(self, media_buy_id: str, packages: list[MediaPackage]) -> str | None:
        """Creates a workflow step for human approval of order activation.

        Args:
            media_buy_id: The GAM order ID awaiting activation
            packages: List of packages in the media buy for context

        Returns:
            str: The workflow step ID if created successfully, None otherwise
        """
        step_id = f"a{uuid.uuid4().hex[:5]}"  # 6 chars total

        # Build detailed action list for humans
        action_details = {
            "action_type": "activate_gam_order",
            "order_id": media_buy_id,
            "platform": "Google Ad Manager",
            "automation_mode": "confirmation_required",
            "instructions": [
                f"Review GAM Order {media_buy_id} in your GAM account",
                "Verify line item settings, targeting, and creative placeholders are correct",
                "Confirm budget, flight dates, and delivery settings are acceptable",
                "Check that ad units and placements are properly targeted",
                "Once verified, approve this task to automatically activate the order and line items",
            ],
            "gam_order_url": f"https://admanager.google.com/orders/{media_buy_id}",
            "packages": [{"name": pkg.name, "impressions": pkg.impressions, "cpm": pkg.cpm} for pkg in packages],
            "next_action_after_approval": "automatic_activation",
        }

        try:
            with get_db_session() as db_session:
                # Create a context for this workflow
                context_id = f"ctx_{uuid.uuid4().hex[:12]}"
                context = Context(
                    context_id=context_id,
                    tenant_id=self.tenant_id,
                    principal_id=self.principal.principal_id,
                )
                db_session.add(context)

                # Create workflow step
                workflow_step = WorkflowStep(
                    step_id=step_id,
                    context_id=context_id,
                    step_type="approval",
                    tool_name="activate_gam_order",
                    request_data=action_details,
                    status="approval",  # Shortened to fit database field
                    owner="publisher",  # Publisher needs to approve GAM order activation
                    assigned_to=None,  # Will be assigned by admin
                    transaction_details={"gam_order_id": media_buy_id},
                )

                db_session.add(workflow_step)

                # Create object mapping to link this step with the media buy
                object_mapping = ObjectWorkflowMapping(
                    object_type="media_buy",
                    object_id=media_buy_id,
                    step_id=step_id,
                    action="activate",
                )

                db_session.add(object_mapping)
                db_session.commit()

                self.log(f"✓ Created workflow step {step_id} for GAM order activation approval")
                if self.audit_logger:
                    self.audit_logger.log_success(f"Created activation approval workflow step: {step_id}")

                # Send Slack notification if configured
                self._send_workflow_notification(step_id, action_details)

                return step_id

        except Exception as e:
            error_msg = f"Failed to create activation workflow step for order {media_buy_id}: {str(e)}"
            self.log(f"[red]Error: {error_msg}[/red]")
            if self.audit_logger:
                self.audit_logger.log_warning(error_msg)
            return None

    def create_manual_order_workflow_step(
        self,
        request: CreateMediaBuyRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
        media_buy_id: str,
    ) -> str | None:
        """Creates a workflow step for manual creation of GAM order (manual mode).

        Args:
            request: The original media buy request
            packages: List of packages to be created
            start_time: Campaign start time
            end_time: Campaign end time
            media_buy_id: Generated media buy ID for tracking

        Returns:
            str: The workflow step ID if created successfully, None otherwise
        """
        step_id = f"c{uuid.uuid4().hex[:5]}"  # 6 chars total

        # Build detailed action list for humans to manually create the order
        action_details = {
            "action_type": "create_gam_order",
            "order_id": media_buy_id,
            "platform": "Google Ad Manager",
            "automation_mode": "manual_creation_required",
            "campaign_name": request.campaign_name,
            "total_budget": request.budget.total,
            "flight_start": start_time.isoformat(),
            "flight_end": end_time.isoformat(),
            "instructions": [
                "Navigate to Google Ad Manager and create a new order",
                f"Set order name to: {request.campaign_name}",
                f"Set total budget to: ${request.budget.total:,.2f}",
                f"Set flight dates: {start_time.strftime('%Y-%m-%d')} to {end_time.strftime('%Y-%m-%d')}",
                "Create line items for each package according to the specifications below",
                "Once order is created, update this workflow with the GAM order ID",
            ],
            "packages": [
                {
                    "name": pkg.name,
                    "impressions": pkg.impressions,
                    "cpm": pkg.cpm,
                    "total_budget": (pkg.impressions / 1000) * pkg.cpm,
                    "targeting": pkg.targeting_overlay.model_dump() if pkg.targeting_overlay else {},
                }
                for pkg in packages
            ],
            "gam_network_url": "https://admanager.google.com/",
            "next_action_after_creation": "order_id_update_required",
        }

        try:
            with get_db_session() as db_session:
                # Create a context for this workflow
                context_id = f"ctx_{uuid.uuid4().hex[:12]}"
                context = Context(
                    context_id=context_id,
                    tenant_id=self.tenant_id,
                    principal_id=self.principal.principal_id,
                )
                db_session.add(context)

                # Create workflow step
                workflow_step = WorkflowStep(
                    step_id=step_id,
                    context_id=context_id,
                    step_type="creation",
                    tool_name="create_gam_order",
                    request_data=action_details,
                    status="approval",  # Shortened to fit database field
                    owner="publisher",  # Publisher needs to create GAM order manually
                    assigned_to=None,  # Will be assigned by admin
                    transaction_details={"campaign_name": request.campaign_name},
                )

                db_session.add(workflow_step)

                # Create object mapping to link this step with the media buy
                object_mapping = ObjectWorkflowMapping(
                    object_type="media_buy",
                    object_id=media_buy_id,
                    step_id=step_id,
                    action="create",
                )

                db_session.add(object_mapping)
                db_session.commit()

                self.log(f"✓ Created workflow step {step_id} for manual GAM order creation")
                if self.audit_logger:
                    self.audit_logger.log_success(f"Created manual order creation workflow step: {step_id}")

                # Send Slack notification if configured
                self._send_workflow_notification(step_id, action_details)

                return step_id

        except Exception as e:
            error_msg = f"Failed to create manual order workflow step for {media_buy_id}: {str(e)}"
            self.log(f"[red]Error: {error_msg}[/red]")
            if self.audit_logger:
                self.audit_logger.log_warning(error_msg)
            return None

    def create_approval_workflow_step(self, media_buy_id: str, approval_type: str = "creative_approval") -> str | None:
        """Creates a workflow step for human approval of creative assets.

        Args:
            media_buy_id: The GAM order ID requiring approval
            approval_type: Type of approval needed

        Returns:
            str: The workflow step ID if created successfully, None otherwise
        """
        step_id = f"p{uuid.uuid4().hex[:5]}"  # 6 chars total

        action_details = {
            "action_type": approval_type,
            "order_id": media_buy_id,
            "platform": "Google Ad Manager",
            "automation_mode": "approval_required",
            "instructions": [
                f"Review {approval_type.replace('_', ' ')} for GAM Order {media_buy_id}",
                "Check that all requirements are met",
                "Approve this task to proceed with the operation",
            ],
            "gam_order_url": f"https://admanager.google.com/orders/{media_buy_id}",
            "next_action_after_approval": "automatic_processing",
        }

        try:
            with get_db_session() as db_session:
                # Create a context for this workflow
                context_id = f"ctx_{uuid.uuid4().hex[:12]}"
                context = Context(
                    context_id=context_id,
                    tenant_id=self.tenant_id,
                    principal_id=self.principal.principal_id,
                )
                db_session.add(context)

                workflow_step = WorkflowStep(
                    step_id=step_id,
                    context_id=context_id,
                    step_type="approval",
                    tool_name=approval_type,
                    request_data=action_details,
                    status="approval",
                    owner="publisher",
                    assigned_to=None,
                    transaction_details={"gam_order_id": media_buy_id},
                )

                db_session.add(workflow_step)

                object_mapping = ObjectWorkflowMapping(
                    object_type="media_buy",
                    object_id=media_buy_id,
                    step_id=step_id,
                    action="approve",
                )

                db_session.add(object_mapping)
                db_session.commit()

                self.log(f"✓ Created workflow step {step_id} for {approval_type}")
                if self.audit_logger:
                    self.audit_logger.log_success(f"Created {approval_type} workflow step: {step_id}")

                # Send Slack notification if configured
                self._send_workflow_notification(step_id, action_details)

                return step_id

        except Exception as e:
            error_msg = f"Failed to create {approval_type} workflow step for {media_buy_id}: {str(e)}"
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

            action_type = action_details.get("action_type", "workflow_step")
            automation_mode = action_details.get("automation_mode", "unknown")

            if action_type == "create_gam_order":
                title = "🔨 Manual GAM Order Creation Required"
                color = "#FF9500"  # Orange
                description = "Manual mode activated - human intervention needed to create GAM order"
            elif action_type == "activate_gam_order":
                title = "✅ GAM Order Activation Approval Required"
                color = "#FFD700"  # Gold
                description = "Order created successfully - approval needed for activation"
            else:
                title = "🔔 Workflow Step Requires Attention"
                color = "#36A2EB"  # Blue
                description = f"Workflow step {step_id} needs human intervention"

            # Build Slack message
            slack_payload = {
                "attachments": [
                    {
                        "color": color,
                        "title": title,
                        "text": description,
                        "fields": [
                            {"title": "Step ID", "value": step_id, "short": True},
                            {
                                "title": "Automation Mode",
                                "value": automation_mode.replace("_", " ").title(),
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
                self.log(f"✓ Sent Slack notification for workflow step {step_id}")
                if self.audit_logger:
                    self.audit_logger.log_success(f"Sent Slack notification for workflow step: {step_id}")
            else:
                self.log(f"[yellow]Slack notification failed with status {response.status_code}[/yellow]")

        except Exception as e:
            self.log(f"[yellow]Failed to send Slack notification: {str(e)}[/yellow]")
            # Don't fail the workflow creation if notification fails
