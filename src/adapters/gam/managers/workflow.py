"""
GAM Workflow Manager - Human-in-the-Loop Workflow Management

This module handles workflow step creation, notification, and management
for Google Ad Manager operations requiring human intervention.

Extends BaseWorkflowManager with GAM-specific workflow logic.
"""

import logging
import uuid
from datetime import datetime
from typing import Any

from src.adapters.base_workflow import BaseWorkflowManager
from src.core.database.database_session import get_db_session
from src.core.database.models import Context, ObjectWorkflowMapping, WorkflowStep
from src.core.schemas import CreateMediaBuyRequest, MediaPackage

logger = logging.getLogger(__name__)


class GAMWorkflowManager(BaseWorkflowManager):
    """Manages Human-in-the-Loop workflows for Google Ad Manager operations.

    Extends BaseWorkflowManager with GAM-specific workflow logic.
    """

    platform_name = "Google Ad Manager"
    platform_url_base = "https://admanager.google.com"

    def __init__(self, tenant_id: str, principal=None, audit_logger=None, log_func=None):
        """Initialize workflow manager.

        Args:
            tenant_id: Tenant identifier for configuration
            principal: Principal object for context creation
            audit_logger: Audit logging instance
            log_func: Logging function for output
        """
        super().__init__(tenant_id, principal, audit_logger, log_func)

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

        # Use naming template from adapter config, or fallback to default
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import AdapterConfig
        from src.core.utils.naming import apply_naming_template, build_order_name_context

        order_name_template = "{campaign_name|brand_name} - {date_range}"  # Default
        tenant_gemini_key = None
        with get_db_session() as db_session:
            from src.core.database.models import Tenant

            stmt = select(AdapterConfig).filter_by(tenant_id=self.tenant_id)
            adapter_config = db_session.scalars(stmt).first()
            if adapter_config and adapter_config.gam_order_name_template:
                order_name_template = adapter_config.gam_order_name_template

            # Get tenant's Gemini key for auto_name generation
            tenant_stmt = select(Tenant).filter_by(tenant_id=self.tenant_id)
            tenant = db_session.scalars(tenant_stmt).first()
            if tenant:
                tenant_gemini_key = tenant.gemini_api_key

        naming_context = build_order_name_context(
            request, packages, start_time, end_time, tenant_gemini_key=tenant_gemini_key
        )
        order_name = apply_naming_template(order_name_template, naming_context)

        # Build detailed action list for humans to manually create the order
        # Calculate total budget from package budgets (AdCP v2.2.0)
        total_budget_amount = request.get_total_budget()

        action_details = {
            "action_type": "create_gam_order",
            "order_id": media_buy_id,
            "platform": "Google Ad Manager",
            "automation_mode": "manual_creation_required",
            "campaign_name": order_name,
            "total_budget": total_budget_amount,
            "flight_start": start_time.isoformat(),
            "flight_end": end_time.isoformat(),
            "instructions": [
                "Navigate to Google Ad Manager and create a new order",
                f"Set order name to: {order_name}",
                f"Set total budget to: ${total_budget_amount:,.2f}",
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
                    "targeting": pkg.targeting_overlay if pkg.targeting_overlay else {},
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
                    transaction_details={"campaign_name": order_name},
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

    def create_approval_polling_workflow_step(
        self, media_buy_id: str, packages: list[MediaPackage], operation: str = "order_approval"
    ) -> str | None:
        """Creates a workflow step for background approval polling (NO_FORECAST_YET).

        This workflow step tracks background polling of GAM order approval status.
        When forecasting is ready, the order will be automatically approved and
        a webhook notification will be sent.

        Args:
            media_buy_id: The GAM order ID awaiting approval
            packages: List of packages in the media buy for context
            operation: Type of approval operation (e.g., "order_approval")

        Returns:
            str: The workflow step ID if created successfully, None otherwise
        """
        step_id = f"b{uuid.uuid4().hex[:5]}"  # 6 chars total, 'b' prefix for background

        # Build detailed action for background polling
        action_details = {
            "action_type": operation,
            "order_id": media_buy_id,
            "platform": "Google Ad Manager",
            "automation_mode": "background_polling",
            "status": "working",
            "instructions": [
                "GAM order approval is pending - forecasting not ready yet",
                "Background task is polling GAM for forecasting completion",
                "Order will be automatically approved when forecasting is ready",
                "Webhook notification will be sent when approval completes",
            ],
            "gam_order_url": f"https://admanager.google.com/orders/{media_buy_id}",
            "packages": [{"name": pkg.name, "impressions": pkg.impressions, "cpm": pkg.cpm} for pkg in packages],
            "next_action": "automatic_approval_when_ready",
            "polling_interval_seconds": 30,
            "max_polling_duration_minutes": 15,
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

                # Create workflow step with "working" status
                workflow_step = WorkflowStep(
                    step_id=step_id,
                    context_id=context_id,
                    step_type="background_task",
                    tool_name=operation,
                    request_data=action_details,
                    status="working",  # Indicates background processing in progress
                    owner="system",  # System owns background tasks
                    assigned_to="background_approval_service",
                    transaction_details={"gam_order_id": media_buy_id, "polling_started": datetime.now().isoformat()},
                )

                db_session.add(workflow_step)

                # Create object mapping to link this step with the media buy
                object_mapping = ObjectWorkflowMapping(
                    object_type="media_buy",
                    object_id=media_buy_id,
                    step_id=step_id,
                    action="approve",
                )

                db_session.add(object_mapping)
                db_session.commit()

                self.log(f"✓ Created background approval polling workflow step {step_id}")
                if self.audit_logger:
                    self.audit_logger.log_success(f"Created background approval polling workflow step: {step_id}")

                # Send Slack notification if configured
                self._send_workflow_notification(step_id, action_details)

                return step_id

        except Exception as e:
            error_msg = f"Failed to create approval polling workflow step for order {media_buy_id}: {str(e)}"
            self.log(f"[red]Error: {error_msg}[/red]")
            if self.audit_logger:
                self.audit_logger.log_warning(error_msg)
            return None

    def _get_notification_details(self, step_id: str, action_details: dict[str, Any]) -> dict[str, str]:
        """Get GAM-specific notification styling.

        Args:
            step_id: The workflow step ID
            action_details: Details about the workflow step

        Returns:
            Dictionary with title, description, and color
        """
        action_type = action_details.get("action_type", "workflow_step")

        if action_type == "create_gam_order":
            return {
                "title": "Manual GAM Order Creation Required",
                "description": "Manual mode activated - human intervention needed to create GAM order",
                "color": "#FF9500",  # Orange
            }
        elif action_type == "activate_gam_order":
            return {
                "title": "GAM Order Activation Approval Required",
                "description": "Order created successfully - approval needed for activation",
                "color": "#FFD700",  # Gold
            }
        else:
            # Fall back to base class behavior
            return super()._get_notification_details(step_id, action_details)
