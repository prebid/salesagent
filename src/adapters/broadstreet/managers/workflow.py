"""Broadstreet Workflow Manager.

Handles Human-in-the-Loop workflows for Broadstreet operations.
Extends BaseWorkflowManager with Broadstreet-specific workflow logic.
"""

from datetime import datetime
from typing import Any

from src.adapters.base_workflow import BaseWorkflowManager
from src.core.schemas import CreateMediaBuyRequest, MediaPackage


class BroadstreetWorkflowManager(BaseWorkflowManager):
    """Manages Human-in-the-Loop workflows for Broadstreet operations."""

    platform_name = "Broadstreet"
    platform_url_base = "https://broadstreetads.com"

    def create_activation_workflow_step(
        self,
        media_buy_id: str,
        packages: list[MediaPackage],
    ) -> str | None:
        """Creates a workflow step for human approval of campaign activation.

        Args:
            media_buy_id: The Broadstreet campaign ID awaiting activation
            packages: List of packages in the media buy for context

        Returns:
            str: The workflow step ID if created successfully, None otherwise
        """
        action_details = {
            "action_type": "activate_broadstreet_campaign",
            "campaign_id": media_buy_id,
            "platform": self.platform_name,
            "automation_mode": "confirmation_required",
            "instructions": [
                f"Review Broadstreet Campaign {media_buy_id}",
                "Verify zone assignments and creative placements are correct",
                "Confirm budget, flight dates, and delivery settings are acceptable",
                "Check that placements are properly targeted to zones",
                "Once verified, approve this task to automatically activate the campaign",
            ],
            "broadstreet_url": f"{self.platform_url_base}/campaigns/{media_buy_id}",
            "packages": self.build_packages_summary(packages),
            "next_action_after_approval": "automatic_activation",
        }

        return self.create_workflow_step(
            step_type="approval",
            tool_name="activate_broadstreet_campaign",
            action_details=action_details,
            object_type="media_buy",
            object_id=media_buy_id,
            object_action="activate",
            step_prefix="a",  # 'a' for activation
            transaction_details={"broadstreet_campaign_id": media_buy_id},
        )

    def create_manual_campaign_workflow_step(
        self,
        request: CreateMediaBuyRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
        media_buy_id: str,
    ) -> str | None:
        """Creates a workflow step for manual creation of Broadstreet campaign.

        Used when automation_mode is "manual" - requires human to create
        the campaign in Broadstreet directly.

        Args:
            request: The original media buy request
            packages: List of packages to be created
            start_time: Campaign start time
            end_time: Campaign end time
            media_buy_id: Generated media buy ID for tracking

        Returns:
            str: The workflow step ID if created successfully, None otherwise
        """
        # Build campaign name
        brand_name = getattr(request.brand_manifest, "name", None) or "Unknown Brand"
        campaign_name = f"{brand_name} - {start_time.strftime('%Y%m%d')}"
        if request.po_number:
            campaign_name = f"AdCP-{request.po_number}"

        # Calculate total budget
        total_budget = request.get_total_budget()

        action_details = {
            "action_type": "create_broadstreet_campaign",
            "campaign_id": media_buy_id,
            "platform": self.platform_name,
            "automation_mode": "manual_creation_required",
            "campaign_name": campaign_name,
            "total_budget": total_budget,
            "flight_start": start_time.isoformat(),
            "flight_end": end_time.isoformat(),
            "instructions": [
                "Navigate to Broadstreet and create a new campaign",
                f"Set campaign name to: {campaign_name}",
                (
                    f"Set total budget to: ${total_budget:,.2f}"
                    if total_budget
                    else "Configure budget based on package requirements"
                ),
                f"Set flight dates: {start_time.strftime('%Y-%m-%d')} to {end_time.strftime('%Y-%m-%d')}",
                "Create placements for each package according to the zone specifications below",
                "Once campaign is created, update this workflow with the Broadstreet campaign ID",
            ],
            "packages": [
                {
                    "name": pkg.name,
                    "impressions": pkg.impressions,
                    "cpm": pkg.cpm,
                    "total_budget": (pkg.impressions / 1000) * pkg.cpm if pkg.impressions and pkg.cpm else 0,
                    "targeting": pkg.targeting_overlay if pkg.targeting_overlay else {},
                }
                for pkg in packages
            ],
            "broadstreet_url": self.platform_url_base,
            "next_action_after_creation": "campaign_id_update_required",
        }

        return self.create_workflow_step(
            step_type="creation",
            tool_name="create_broadstreet_campaign",
            action_details=action_details,
            object_type="media_buy",
            object_id=media_buy_id,
            object_action="create",
            step_prefix="c",  # 'c' for creation
            transaction_details={"campaign_name": campaign_name},
        )

    def create_creative_approval_workflow_step(
        self,
        media_buy_id: str,
        creative_ids: list[str],
    ) -> str | None:
        """Creates a workflow step for creative approval.

        Args:
            media_buy_id: The Broadstreet campaign ID
            creative_ids: List of creative IDs requiring approval

        Returns:
            str: The workflow step ID if created successfully, None otherwise
        """
        action_details = {
            "action_type": "creative_approval",
            "campaign_id": media_buy_id,
            "platform": self.platform_name,
            "automation_mode": "approval_required",
            "instructions": [
                f"Review creatives for Broadstreet Campaign {media_buy_id}",
                "Verify all creative assets meet platform requirements",
                "Check that click URLs and tracking are properly configured",
                "Approve this task to proceed with creative activation",
            ],
            "creative_ids": creative_ids,
            "broadstreet_url": f"{self.platform_url_base}/campaigns/{media_buy_id}",
            "next_action_after_approval": "automatic_creative_activation",
        }

        return self.create_workflow_step(
            step_type="approval",
            tool_name="creative_approval",
            action_details=action_details,
            object_type="media_buy",
            object_id=media_buy_id,
            object_action="approve",
            step_prefix="p",  # 'p' for approval (matching GAM pattern)
            transaction_details={"broadstreet_campaign_id": media_buy_id, "creative_count": len(creative_ids)},
        )

    def _get_notification_details(self, step_id: str, action_details: dict[str, Any]) -> dict[str, str]:
        """Get Broadstreet-specific notification styling.

        Args:
            step_id: The workflow step ID
            action_details: Details about the workflow step

        Returns:
            Dictionary with title, description, and color
        """
        action_type = action_details.get("action_type", "workflow_step")

        if action_type == "create_broadstreet_campaign":
            return {
                "title": "Manual Broadstreet Campaign Creation Required",
                "description": "Manual mode activated - human intervention needed to create campaign",
                "color": "#FF9500",  # Orange
            }
        elif action_type == "activate_broadstreet_campaign":
            return {
                "title": "Broadstreet Campaign Activation Approval Required",
                "description": "Campaign created successfully - approval needed for activation",
                "color": "#FFD700",  # Gold
            }
        elif action_type == "creative_approval":
            return {
                "title": "Broadstreet Creative Approval Required",
                "description": "Creatives uploaded - approval needed before activation",
                "color": "#9B59B6",  # Purple
            }
        else:
            # Fall back to base class behavior
            return super()._get_notification_details(step_id, action_details)
