"""Service for managing GAM product implementation configurations."""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class GAMProductConfigService:
    """Handles GAM-specific product configuration management."""

    @staticmethod
    def generate_default_config(delivery_type: str, formats: list[str] | None = None) -> dict[str, Any]:
        """Generate default GAM implementation config based on product delivery type.

        Args:
            delivery_type: "guaranteed" or "non_guaranteed"
            formats: List of format IDs to derive creative placeholders

        Returns:
            Dictionary of GAM implementation config with sensible defaults
        """
        # Base configuration shared across all types
        base_config = {
            "cost_type": "CPM",
            "creative_rotation_type": "EVEN",
            "primary_goal_unit_type": "IMPRESSIONS",
            "include_descendants": True,
        }

        # Delivery-type-specific defaults
        if delivery_type == "guaranteed":
            base_config.update(
                {
                    "line_item_type": "STANDARD",
                    "priority": 6,
                    "primary_goal_type": "DAILY",
                    "delivery_rate_type": "EVENLY",
                    "non_guaranteed_automation": "manual",  # Guaranteed always manual
                }
            )
        else:  # non_guaranteed
            base_config.update(
                {
                    "line_item_type": "PRICE_PRIORITY",
                    "priority": 10,
                    "primary_goal_type": "NONE",
                    "delivery_rate_type": "AS_FAST_AS_POSSIBLE",
                    "creative_rotation_type": "OPTIMIZED",
                    "non_guaranteed_automation": "confirmation_required",  # Safe default
                }
            )

        # Generate creative placeholders from formats if provided
        if formats:
            base_config["creative_placeholders"] = GAMProductConfigService._generate_creative_placeholders(formats)
        else:
            # Default fallback placeholder
            base_config["creative_placeholders"] = [
                {"width": 300, "height": 250, "expected_creative_count": 1, "is_native": False}
            ]

        return base_config

    @staticmethod
    def _generate_creative_placeholders(formats: list[str]) -> list[dict[str, Any]]:
        """Generate creative placeholders from format IDs.

        Args:
            formats: List of format IDs (e.g., ["display_300x250", "display_728x90"])

        Returns:
            List of creative placeholder dictionaries for GAM
        """
        placeholders = []

        # Common format mappings (format_id -> dimensions)
        format_dimensions = {
            "display_300x250": (300, 250),
            "display_728x90": (728, 90),
            "display_160x600": (160, 600),
            "display_300x600": (300, 600),
            "display_970x250": (970, 250),
            "display_320x50": (320, 50),
            "display_320x100": (320, 100),
            "display_300x50": (300, 50),
        }

        for format_item in formats:
            # Handle both format storage patterns:
            # 1. Simple string: "display_300x250"
            # 2. Dict with format_id: {"format_id": "display_300x250", "name": "...", ...}
            if isinstance(format_item, dict):
                format_id = format_item.get("format_id", "")
            else:
                format_id = format_item

            if format_id in format_dimensions:
                width, height = format_dimensions[format_id]
                placeholders.append(
                    {
                        "width": width,
                        "height": height,
                        "expected_creative_count": 1,
                        "is_native": False,
                    }
                )
            elif format_id.startswith("native_"):
                # Native format - use default dimensions
                placeholders.append(
                    {
                        "width": 1,
                        "height": 1,
                        "expected_creative_count": 1,
                        "is_native": True,
                    }
                )
            elif format_id.startswith("video_"):
                # Video format - use standard video dimensions
                placeholders.append(
                    {
                        "width": 640,
                        "height": 480,
                        "expected_creative_count": 1,
                        "is_native": False,
                    }
                )

        # If no placeholders generated, return default
        if not placeholders:
            placeholders = [{"width": 300, "height": 250, "expected_creative_count": 1, "is_native": False}]

        return placeholders

    @staticmethod
    def validate_config(config: dict[str, Any]) -> tuple[bool, str | None]:
        """Validate GAM implementation config for required fields.

        Args:
            config: GAM implementation configuration dictionary

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not config:
            return False, "implementation_config is required for GAM products"

        # Required fields
        required_fields = ["priority", "creative_placeholders"]

        for field in required_fields:
            if field not in config:
                return False, f"Missing required GAM field: {field}"

        # Validate priority range
        priority = config.get("priority")
        if not isinstance(priority, int) or priority < 1 or priority > 16:
            return False, "priority must be an integer between 1 and 16"

        # Validate line item type (optional - if not provided, will be auto-selected based on pricing)
        if "line_item_type" in config:
            valid_line_item_types = ["STANDARD", "SPONSORSHIP", "NETWORK", "BULK", "PRICE_PRIORITY", "HOUSE"]
            if config.get("line_item_type") not in valid_line_item_types:
                return False, f"line_item_type must be one of: {', '.join(valid_line_item_types)}"

        # Validate creative placeholders
        placeholders = config.get("creative_placeholders", [])
        if not placeholders:
            return False, "At least one creative placeholder is required"

        for placeholder in placeholders:
            if "width" not in placeholder or "height" not in placeholder:
                return False, "Creative placeholders must have width and height"

        # Warn if no inventory targeting specified (will use network root as fallback)
        has_inventory_targeting = config.get("targeted_ad_unit_ids") or config.get("targeted_placement_ids")
        if not has_inventory_targeting:
            logger.warning(
                "No inventory targeting specified (targeted_ad_unit_ids or targeted_placement_ids). "
                "Will use network root ad unit as fallback."
            )

        return True, None

    @staticmethod
    def parse_form_config(form_data) -> dict[str, Any]:
        """Parse GAM configuration form data into implementation_config format.

        Args:
            form_data: Flask request.form object (ImmutableMultiDict)

        Returns:
            Parsed implementation_config dictionary
        """
        config = {}

        # Basic line item settings
        config["line_item_type"] = form_data.get("line_item_type", "STANDARD")
        priority = form_data.get("priority", "8")
        config["priority"] = int(priority) if priority else 8
        config["cost_type"] = form_data.get("cost_type", "CPM")

        # Delivery settings
        config["creative_rotation_type"] = form_data.get("creative_rotation_type", "EVEN")
        config["delivery_rate_type"] = form_data.get("delivery_rate_type", "EVENLY")
        config["primary_goal_type"] = form_data.get("primary_goal_type", "DAILY")
        config["primary_goal_unit_type"] = form_data.get("primary_goal_unit_type", "IMPRESSIONS")

        # Automation settings
        config["non_guaranteed_automation"] = form_data.get("non_guaranteed_automation", "confirmation_required")

        # Inventory targeting
        ad_unit_ids_text = form_data.get("targeted_ad_unit_ids", "").strip()
        if ad_unit_ids_text:
            config["targeted_ad_unit_ids"] = [id.strip() for id in ad_unit_ids_text.split("\n") if id.strip()]

        placement_ids_text = form_data.get("targeted_placement_ids", "").strip()
        if placement_ids_text:
            config["targeted_placement_ids"] = [id.strip() for id in placement_ids_text.split("\n") if id.strip()]

        config["include_descendants"] = "include_descendants" in form_data

        # Creative placeholders (arrays)
        widths = form_data.getlist("placeholder_width[]")
        heights = form_data.getlist("placeholder_height[]")
        counts = form_data.getlist("placeholder_count[]")
        is_natives = form_data.getlist("placeholder_is_native[]")

        placeholders = []
        for i in range(len(widths)):
            placeholders.append(
                {
                    "width": int(widths[i]),
                    "height": int(heights[i]),
                    "expected_creative_count": int(counts[i]) if i < len(counts) else 1,
                    "is_native": str(i) in is_natives,  # Checkboxes send index as value
                }
            )
        config["creative_placeholders"] = placeholders

        # Frequency caps (arrays)
        cap_max_imps = form_data.getlist("cap_max_impressions[]")
        cap_time_units = form_data.getlist("cap_time_unit[]")
        cap_time_ranges = form_data.getlist("cap_time_range[]")

        if cap_max_imps:
            frequency_caps = []
            for i in range(len(cap_max_imps)):
                frequency_caps.append(
                    {
                        "max_impressions": int(cap_max_imps[i]),
                        "time_unit": cap_time_units[i] if i < len(cap_time_units) else "DAY",
                        "time_range": int(cap_time_ranges[i]) if i < len(cap_time_ranges) else 1,
                    }
                )
            config["frequency_caps"] = frequency_caps

        # Competition & exclusions
        comp_labels = form_data.get("competitive_exclusion_labels", "").strip()
        if comp_labels:
            config["competitive_exclusion_labels"] = [
                label.strip() for label in comp_labels.split(",") if label.strip()
            ]

        # Custom targeting keys (JSON)
        custom_targeting = form_data.get("custom_targeting_keys", "{}").strip()
        if custom_targeting and custom_targeting != "{}":
            try:
                config["custom_targeting_keys"] = json.loads(custom_targeting)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON in custom_targeting_keys, skipping")

        # Advanced settings
        config["environment_type"] = form_data.get("environment_type", "BROWSER")

        discount_type = form_data.get("discount_type")
        if discount_type:
            config["discount_type"] = discount_type
            discount_value = form_data.get("discount_value")
            if discount_value:
                config["discount_value"] = float(discount_value)

        config["allow_overbook"] = "allow_overbook" in form_data
        config["skip_inventory_check"] = "skip_inventory_check" in form_data
        config["disable_viewability_avg_revenue_optimization"] = (
            "disable_viewability_avg_revenue_optimization" in form_data
        )

        # Video settings
        if config["environment_type"] == "VIDEO_PLAYER":
            companion = form_data.get("companion_delivery_option")
            if companion:
                config["companion_delivery_option"] = companion

            video_duration = form_data.get("video_max_duration")
            if video_duration:
                config["video_max_duration"] = int(video_duration) * 1000  # Convert seconds to milliseconds

            skip_offset = form_data.get("skip_offset")
            if skip_offset:
                config["skip_offset"] = int(skip_offset) * 1000  # Convert seconds to milliseconds

        # Native settings
        native_style = form_data.get("native_style_id")
        if native_style:
            config["native_style_id"] = native_style

        # Team IDs
        team_ids = form_data.get("applied_team_ids", "").strip()
        if team_ids:
            config["applied_team_ids"] = [id.strip() for id in team_ids.split(",") if id.strip()]

        return config
