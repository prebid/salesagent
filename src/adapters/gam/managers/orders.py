"""
GAM Orders Manager

Handles order creation, management, status checking, and lifecycle operations
for Google Ad Manager orders.
"""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from googleads import ad_manager

from src.adapters.gam.utils.timeout_handler import timeout

logger = logging.getLogger(__name__)

# Line item type constants for GAM automation
GUARANTEED_LINE_ITEM_TYPES = {"STANDARD", "SPONSORSHIP"}
NON_GUARANTEED_LINE_ITEM_TYPES = {"NETWORK", "BULK", "PRICE_PRIORITY", "HOUSE"}


class GAMOrdersManager:
    """Manages Google Ad Manager order operations."""

    def __init__(
        self, client_manager, advertiser_id: str | None = None, trafficker_id: str | None = None, dry_run: bool = False
    ):
        """Initialize orders manager.

        Args:
            client_manager: GAMClientManager instance
            advertiser_id: GAM advertiser ID (required for order creation operations)
            trafficker_id: GAM trafficker ID (required for order creation operations)
            dry_run: Whether to run in dry-run mode
        """
        self.client_manager = client_manager
        self.advertiser_id = advertiser_id
        self.trafficker_id = trafficker_id
        self.dry_run = dry_run

    @timeout(seconds=60)  # 1 minute timeout for order creation
    def create_order(
        self,
        order_name: str,
        total_budget: float,
        start_time: datetime,
        end_time: datetime,
        applied_team_ids: list[str] | None = None,
        po_number: str | None = None,
    ) -> str:
        """Create a new GAM order.

        Args:
            order_name: Name for the order
            total_budget: Total budget in USD
            start_time: Order start datetime
            end_time: Order end datetime
            applied_team_ids: Optional list of team IDs to apply
            po_number: Optional PO number

        Returns:
            Created order ID as string

        Raises:
            ValueError: If advertiser_id or trafficker_id not configured
            Exception: If order creation fails
        """
        # Validate required configuration for order creation
        if not self.advertiser_id or not self.trafficker_id:
            raise ValueError(
                "Order creation requires both advertiser_id and trafficker_id. "
                "These must be provided when initializing GAMOrdersManager for order operations."
            )

        # Create Order object
        order = {
            "name": order_name,
            "advertiserId": self.advertiser_id,
            "traffickerId": self.trafficker_id,
            "status": "DRAFT",  # Start as DRAFT - will approve after line items are created
            "totalBudget": {"currencyCode": "USD", "microAmount": int(total_budget * 1_000_000)},
            "startDateTime": {
                "date": {"year": start_time.year, "month": start_time.month, "day": start_time.day},
                "hour": start_time.hour,
                "minute": start_time.minute,
                "second": start_time.second,
            },
            "endDateTime": {
                "date": {"year": end_time.year, "month": end_time.month, "day": end_time.day},
                "hour": end_time.hour,
                "minute": end_time.minute,
                "second": end_time.second,
            },
        }

        # Add PO number if provided
        if po_number:
            order["poNumber"] = po_number

        # Add team IDs if configured
        if applied_team_ids:
            order["appliedTeamIds"] = applied_team_ids

        if self.dry_run:
            logger.info(f"Would call: order_service.createOrders([{order['name']}])")
            logger.info(f"  Advertiser ID: {self.advertiser_id}")
            logger.info(f"  Total Budget: ${total_budget:,.2f}")
            logger.info(f"  Flight Dates: {start_time.date()} to {end_time.date()}")
            # Return a mock order ID for dry run
            return f"dry_run_order_{int(datetime.now().timestamp())}"
        else:
            order_service = self.client_manager.get_service("OrderService")
            created_orders = order_service.createOrders([order])
            if created_orders:
                order_id = str(created_orders[0]["id"])
                logger.info(f"✓ Created GAM Order ID: {order_id}")
                return order_id
            else:
                raise Exception("Failed to create order - no orders returned")

    @timeout(seconds=30)  # 30 seconds timeout for status check
    def get_order_status(self, order_id: str) -> str:
        """Get the status of a GAM order.

        Args:
            order_id: GAM order ID

        Returns:
            Order status string
        """
        if self.dry_run:
            logger.info(f"Would call: order_service.getOrdersByStatement(WHERE id={order_id})")
            return "DRAFT"

        try:
            order_service = self.client_manager.get_service("OrderService")
            statement_builder = ad_manager.StatementBuilder()
            statement_builder.Where("id = :orderId")
            statement_builder.WithBindVariable("orderId", int(order_id))
            statement = statement_builder.ToStatement()

            result = order_service.getOrdersByStatement(statement)
            if result and result.get("results"):
                return result["results"][0].get("status", "UNKNOWN")
            else:
                return "NOT_FOUND"
        except Exception as e:
            logger.error(f"Error getting order status for {order_id}: {e}")
            return "ERROR"

    @timeout(seconds=120)  # 2 minutes timeout for archiving order
    def archive_order(self, order_id: str) -> bool:
        """Archive a GAM order for cleanup purposes.

        Args:
            order_id: The GAM order ID to archive

        Returns:
            True if archival succeeded, False otherwise
        """
        logger.info(f"Archiving GAM Order {order_id} for cleanup")

        if self.dry_run:
            logger.info(f"Would call: order_service.performOrderAction(ArchiveOrders, {order_id})")
            return True

        try:
            order_service = self.client_manager.get_service("OrderService")

            # Use ArchiveOrders action
            archive_action = {"xsi_type": "ArchiveOrders"}

            statement_builder = ad_manager.StatementBuilder()
            statement_builder.Where("id = :orderId")
            statement_builder.WithBindVariable("orderId", int(order_id))
            statement = statement_builder.ToStatement()

            result = order_service.performOrderAction(archive_action, statement)

            if result and result.get("numChanges", 0) > 0:
                logger.info(f"✓ Successfully archived GAM Order {order_id}")
                return True
            else:
                logger.warning(f"No changes made when archiving Order {order_id} (may already be archived)")
                return True  # Consider this successful

        except Exception as e:
            logger.error(f"Failed to archive GAM Order {order_id}: {str(e)}")
            return False

    @timeout(seconds=620)  # 10+ minutes timeout for approving order (with retries)
    def approve_order(self, order_id: str, max_retries: int = 40, poll_interval: int = 15) -> bool:
        """Approve a GAM order after line items have been created.

        GAM requires time to run inventory forecasting on line items before an order
        can be approved. This method will retry if it receives a NO_FORECAST_YET error.

        Per GAM documentation, forecasting can take up to 60 minutes after creating
        new line items. We poll every 15 seconds for up to 10 minutes (40 attempts).

        Args:
            order_id: The GAM order ID to approve
            max_retries: Maximum number of retry attempts for NO_FORECAST_YET errors (default 40)
            poll_interval: Time in seconds between polling attempts (default 15s)

        Returns:
            True if approval succeeded, False otherwise
        """
        import time

        logger.info(f"[APPROVAL] Approving GAM Order {order_id} (dry_run={self.dry_run})")

        if self.dry_run:
            logger.info(
                f"[APPROVAL] DRY-RUN MODE: Would call order_service.performOrderAction(ApproveOrders, {order_id})"
            )
            return True

        # Retry logic for NO_FORECAST_YET errors
        for attempt in range(max_retries):
            try:
                order_service = self.client_manager.get_service("OrderService")

                # Try ApproveAndOverbookOrders - allows approval even if forecast shows insufficient inventory
                # This can sometimes work when ApproveOrders fails with NO_FORECAST_YET
                approve_action = {"xsi_type": "ApproveAndOverbookOrders"}

                statement_builder = ad_manager.StatementBuilder()
                statement_builder.Where("id = :orderId")
                statement_builder.WithBindVariable("orderId", int(order_id))
                statement = statement_builder.ToStatement()

                logger.info(f"[APPROVAL] Attempting ApproveAndOverbookOrders for Order {order_id}")
                result = order_service.performOrderAction(approve_action, statement)

                # Result is a Zeep object (UpdateResult), use getattr instead of .get()
                num_changes = getattr(result, "numChanges", 0) if result else 0
                if num_changes > 0:
                    logger.info(f"✓ Successfully approved GAM Order {order_id} ({num_changes} changes)")
                    return True
                else:
                    logger.warning(f"No changes made when approving Order {order_id} (may already be approved)")
                    return True  # Consider this successful if already approved

            except Exception as e:
                error_str = str(e)

                # Check if this is a NO_FORECAST_YET error (GAM needs time to run forecasting)
                if "NO_FORECAST_YET" in error_str or "ForecastingError" in error_str:
                    if attempt < max_retries - 1:
                        elapsed_time = (attempt + 1) * poll_interval
                        total_time = max_retries * poll_interval
                        logger.warning(
                            f"[APPROVAL] GAM forecasting not ready for Order {order_id}, "
                            f"retrying in {poll_interval}s (attempt {attempt + 1}/{max_retries}, "
                            f"elapsed: {elapsed_time}s, max: {total_time}s)"
                        )
                        time.sleep(poll_interval)
                        continue  # Retry
                    else:
                        logger.error(
                            f"[APPROVAL] Failed to approve Order {order_id} after {max_retries} attempts "
                            f"({max_retries * poll_interval}s total): "
                            f"GAM forecasting still not ready. Order remains in DRAFT status."
                        )
                        return False
                else:
                    # Other errors - don't retry
                    logger.error(f"Failed to approve GAM Order {order_id}: {error_str}")
                    return False

        # Should not reach here, but just in case
        return False

    @timeout(seconds=120)  # 2 minutes timeout for fetching line items
    def get_order_line_items(self, order_id: str) -> list[dict]:
        """Get all line items associated with an order.

        Args:
            order_id: GAM order ID

        Returns:
            List of line item dictionaries
        """
        if self.dry_run:
            logger.info(f"Would call: lineitem_service.getLineItemsByStatement(WHERE orderId={order_id})")
            return []

        try:
            lineitem_service = self.client_manager.get_service("LineItemService")
            statement_builder = ad_manager.StatementBuilder()
            statement_builder.Where("orderId = :orderId")
            statement_builder.WithBindVariable("orderId", int(order_id))
            statement = statement_builder.ToStatement()

            result = lineitem_service.getLineItemsByStatement(statement)
            return result.get("results", []) if isinstance(result, dict) else getattr(result, "results", [])
        except Exception as e:
            logger.error(f"Error getting line items for order {order_id}: {e}")
            return []

    def check_order_has_guaranteed_items(self, order_id: str) -> tuple[bool, list[str]]:
        """Check if order has guaranteed line items.

        Args:
            order_id: GAM order ID

        Returns:
            Tuple of (has_guaranteed_items, list_of_guaranteed_types)
        """
        line_items = self.get_order_line_items(order_id)
        guaranteed_types = []

        for line_item in line_items:
            # Handle both dict and Zeep object formats
            line_item_type = (
                line_item.get("lineItemType")
                if isinstance(line_item, dict)
                else getattr(line_item, "lineItemType", None)
            )
            if line_item_type in GUARANTEED_LINE_ITEM_TYPES:
                guaranteed_types.append(line_item_type)

        return len(guaranteed_types) > 0, guaranteed_types

    def create_order_statement(self, order_id: int):
        """Helper method to create a GAM statement for order filtering.

        Args:
            order_id: GAM order ID as integer

        Returns:
            GAM statement object for order queries
        """
        statement_builder = ad_manager.StatementBuilder()
        statement_builder.Where("orderId = :orderId")
        statement_builder.WithBindVariable("orderId", order_id)
        return statement_builder.ToStatement()

    @timeout(seconds=300)  # 5 minutes timeout for batch line item creation
    def create_line_items(
        self,
        order_id: str,
        packages: list,
        start_time: datetime,
        end_time: datetime,
        targeting: dict[str, Any],
        products_map: dict[str, Any],
        log_func: Callable | None = None,
        tenant_id: str | None = None,
        order_name: str | None = None,
        targeting_overlay: Any | None = None,
        package_pricing_info: dict[str, dict] | None = None,
    ) -> list[str]:
        """Create line items for an order.

        Args:
            order_id: GAM order ID
            packages: List of MediaPackage objects
            start_time: Flight start datetime
            end_time: Flight end datetime
            targeting: Base targeting dict (built from targeting overlay)
            products_map: Map of product_id to product config
            log_func: Optional logging function
            tenant_id: Tenant ID for fetching naming templates
            order_name: Order name for line item naming context
            targeting_overlay: Original AdCP targeting overlay for frequency caps, etc.
            package_pricing_info: Optional pricing info per package (AdCP PR #88)
                Maps package_id → {pricing_model, rate, currency, is_fixed, bid_price}

        Returns:
            List of created line item IDs

        Raises:
            ValueError: If required configuration missing
            Exception: If line item creation fails
        """
        if not self.advertiser_id or not self.trafficker_id:
            raise ValueError(
                "Line item creation requires both advertiser_id and trafficker_id. "
                "These must be provided when initializing GAMOrdersManager."
            )

        def log(msg):
            if log_func:
                log_func(msg)
            else:
                logger.info(msg)

        # Get line item naming template from adapter config
        line_item_name_template = "{product_name}"  # Default
        if tenant_id:
            from sqlalchemy import select

            from src.core.database.database_session import get_db_session
            from src.core.database.models import AdapterConfig

            with get_db_session() as db_session:
                stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id)
                adapter_config = db_session.scalars(stmt).first()
                if adapter_config and adapter_config.gam_line_item_name_template:
                    line_item_name_template = adapter_config.gam_line_item_name_template  # type: ignore[assignment]

        created_line_item_ids: list[str] = []
        flight_duration_days = (end_time - start_time).days

        for package_index, package in enumerate(packages, start=1):
            # Get product-specific configuration
            product = products_map.get(package.package_id)
            impl_config = product.get("implementation_config", {}) if product else {}

            # Build line item targeting (merge base targeting with product config)
            line_item_targeting = dict(targeting)  # Copy base targeting

            # Add ad unit/placement targeting from product config
            if impl_config.get("targeted_ad_unit_ids"):
                if "inventoryTargeting" not in line_item_targeting:
                    line_item_targeting["inventoryTargeting"] = {}

                # Validate ad unit IDs are numeric (GAM requires numeric IDs, not codes/names)
                ad_unit_ids = impl_config["targeted_ad_unit_ids"]
                invalid_ids = [id for id in ad_unit_ids if not str(id).isdigit()]
                if invalid_ids:
                    error_msg = (
                        f"Product '{package.package_id}' has invalid ad unit IDs: {invalid_ids}. "
                        f"GAM requires numeric ad unit IDs (e.g., '23312403859'), not ad unit codes or names. "
                        f"\n\nInvalid values found: {', '.join(str(id) for id in invalid_ids)}"
                        f"\n\nTo fix: Update the product's targeted_ad_unit_ids to use numeric IDs from GAM."
                        f"\nFind IDs in GAM Admin UI → Inventory → Ad Units (the numeric ID column)."
                    )
                    log(f"[red]Error: {error_msg}[/red]")
                    raise ValueError(error_msg)

                line_item_targeting["inventoryTargeting"]["targetedAdUnits"] = [
                    {"adUnitId": ad_unit_id, "includeDescendants": impl_config.get("include_descendants", True)}
                    for ad_unit_id in ad_unit_ids
                ]

            if impl_config.get("targeted_placement_ids"):
                if "inventoryTargeting" not in line_item_targeting:
                    line_item_targeting["inventoryTargeting"] = {}
                line_item_targeting["inventoryTargeting"]["targetedPlacements"] = [
                    {"placementId": placement_id} for placement_id in impl_config["targeted_placement_ids"]
                ]

            # Require inventory targeting - no fallback
            if "inventoryTargeting" not in line_item_targeting or not line_item_targeting["inventoryTargeting"]:
                error_msg = (
                    f"Product '{package.package_id}' is not configured with inventory targeting. "
                    f"GAM requires all line items to target specific ad units or placements. "
                    f"\n\nTo fix this product, add one of the following to implementation_config:"
                    f"\n  - 'targeted_ad_unit_ids': ['your_ad_unit_id'] (list of GAM ad unit IDs)"
                    f"\n  - 'targeted_placement_ids': ['your_placement_id'] (list of GAM placement IDs)"
                    f"\n\nYou can find ad unit IDs in GAM Admin UI → Inventory → Ad Units"
                    f"\n\nFor testing, you can use Mock adapter instead of GAM (set ad_server='mock' on tenant)."
                )
                log(f"[red]Error: {error_msg}[/red]")
                raise ValueError(error_msg)

            # Add custom targeting from product config
            # IMPORTANT: Merge without overwriting buyer's targeting (e.g., AEE signals from key_value_pairs)
            if impl_config.get("custom_targeting_keys"):
                if "customTargeting" not in line_item_targeting:
                    line_item_targeting["customTargeting"] = {}
                # Add product custom targeting, but don't overwrite existing keys from buyer
                for key, value in impl_config["custom_targeting_keys"].items():
                    if key not in line_item_targeting["customTargeting"]:
                        line_item_targeting["customTargeting"][key] = value
                    else:
                        log(
                            f"[yellow]Product config custom targeting key '{key}' conflicts with buyer targeting, keeping buyer value[/yellow]"
                        )

            # Build creative placeholders from format_ids
            # First try to get from package.format_ids (buyer-specified)
            creative_placeholders: list[dict[str, Any]] = []

            if package.format_ids:
                from src.core.format_resolver import get_format

                # Validate format types against product supported types
                supported_format_types = impl_config.get("supported_format_types", ["display", "video", "native"])

                for format_id_obj in package.format_ids:
                    # format_id_obj is a FormatId object with agent_url and id fields
                    # Extract the ID string and agent_url for format lookup
                    format_id_str = format_id_obj.id
                    agent_url = format_id_obj.agent_url

                    # Use format resolver to support custom formats and product overrides
                    # Include agent_url in format strings for clarity (different agents may have same format IDs)
                    format_display = f"{agent_url}/{format_id_str}" if agent_url else format_id_str

                    try:
                        # Pass product_id (not package_id) to enable format_overrides lookup
                        product_id_for_format = (
                            package.product_id if hasattr(package, "product_id") else package.package_id
                        )
                        format_obj = get_format(
                            format_id_str, agent_url=agent_url, tenant_id=tenant_id, product_id=product_id_for_format
                        )
                    except ValueError as e:
                        error_msg = f"Format lookup failed for '{format_display}': {e}"
                        log(f"[red]Error: {error_msg}[/red]")
                        raise ValueError(error_msg)

                    # Check if format type is supported by product
                    # Convert enum to string for comparison (adcp 2.5.0 uses Type enum)
                    format_type_str = (
                        format_obj.type.value if hasattr(format_obj.type, "value") else str(format_obj.type)
                    )
                    if format_type_str not in supported_format_types:
                        error_msg = (
                            f"Format '{format_display}' (type: {format_type_str}) is not supported by product {package.package_id}. "
                            f"Product supports: {', '.join(supported_format_types)}. "
                            f"Configure 'supported_format_types' in product implementation_config if this should be supported."
                        )
                        log(f"[red]Error: {error_msg}[/red]")
                        raise ValueError(error_msg)

                    # Audio formats are not supported in GAM (no creative placeholders)
                    if format_type_str == "audio":
                        error_msg = (
                            f"Audio format '{format_display}' is not supported. "
                            f"GAM does not support standalone audio line items. "
                            f"Audio can only be used as companion creatives to video ads. "
                            f"To deliver audio ads, use a different ad server (e.g., Triton, Kevel) that supports audio."
                        )
                        log(f"[red]Error: {error_msg}[/red]")
                        raise ValueError(error_msg)

                    # Check if format has GAM-specific config
                    platform_cfg = format_obj.platform_config or {}
                    gam_cfg = platform_cfg.get("gam", {})
                    placeholder_cfg = gam_cfg.get("creative_placeholder", {})

                    # Build creative placeholder
                    placeholder: dict[str, Any] = {
                        "expectedCreativeCount": 1,
                    }

                    # Check for GAM custom creative template (1x1 placeholder)
                    if "creative_template_id" in placeholder_cfg:
                        # Use 1x1 placeholder with custom template
                        placeholder["size"] = {
                            "width": 1,
                            "height": 1,
                            "isAspectRatio": False,
                        }
                        placeholder["creativeTemplateId"] = placeholder_cfg["creative_template_id"]
                        log(
                            f"  Custom template placeholder: 1x1 with template_id={placeholder_cfg['creative_template_id']}"
                        )

                    else:
                        # Use platform config if available, otherwise fall back to requirements
                        if placeholder_cfg:
                            width = placeholder_cfg.get("width")
                            height = placeholder_cfg.get("height")
                            creative_size_type = placeholder_cfg.get("creative_size_type", "PIXEL")
                        else:
                            # Fallback to requirements (legacy formats)
                            requirements = format_obj.requirements or {}
                            width = requirements.get("width")
                            height = requirements.get("height")
                            creative_size_type = "NATIVE" if format_obj.type == "native" else "PIXEL"

                        # Last resort: Try to extract dimensions from format_id (e.g., "display_970x250_image")
                        if not (width and height):
                            import re

                            match = re.search(r"(\d+)x(\d+)", format_id_str)
                            if match:
                                width = int(match.group(1))
                                height = int(match.group(2))
                                log(f"  [yellow]Extracted dimensions from format ID: {width}x{height}[/yellow]")

                        if width and height:
                            placeholder["size"] = {"width": width, "height": height}
                            placeholder["creativeSizeType"] = creative_size_type

                            # Log video-specific info
                            if format_obj.type == "video":
                                aspect_ratio = (
                                    format_obj.requirements.get("aspect_ratio", "unknown")
                                    if format_obj.requirements
                                    else "unknown"
                                )
                                log(f"  Video placeholder: {width}x{height} ({aspect_ratio} aspect ratio)")
                        else:
                            # For formats without dimensions
                            error_msg = (
                                f"Format '{format_display}' has no width/height configuration for GAM. "
                                f"Add 'platform_config.gam.creative_placeholder' to format definition or "
                                f"ensure format has width/height in requirements."
                            )
                            log(f"[red]Error: {error_msg}[/red]")
                            raise ValueError(error_msg)

                    creative_placeholders.append(placeholder)

            # Fall back to product config only if no valid placeholders from format_ids
            if not creative_placeholders and impl_config.get("creative_placeholders"):
                for placeholder in impl_config["creative_placeholders"]:
                    creative_placeholders.append(
                        {
                            "size": {"width": placeholder["width"], "height": placeholder["height"]},
                            "expectedCreativeCount": placeholder.get("expected_creative_count", 1),
                            "creativeSizeType": "NATIVE" if placeholder.get("is_native") else "PIXEL",
                        }
                    )

            # If package has creatives, filter placeholders to match actual creative sizes
            # This prevents "X out of Y expected" issues and ensures one placeholder per unique size
            if package.creative_ids:
                from sqlalchemy import select

                from src.core.database.database_session import get_db_session
                from src.core.database.models import Creative as DBCreative

                # Collect unique creative sizes from uploaded creatives
                creative_sizes = set()

                # Get creative sizes from database if using creative_ids
                if package.creative_ids:
                    with get_db_session() as session:
                        creative_stmt = select(DBCreative).where(DBCreative.creative_id.in_(package.creative_ids))
                        db_creatives = session.scalars(creative_stmt).all()

                        for db_creative in db_creatives:
                            creative_data = db_creative.data or {}
                            # Try to get dimensions from creative data
                            width = creative_data.get("width")
                            height = creative_data.get("height")

                            # Also check in assets (AdCP v2.4 structure)
                            if not (width and height) and creative_data.get("assets"):
                                for _asset_id, asset in creative_data["assets"].items():
                                    if isinstance(asset, dict):
                                        width = asset.get("width")
                                        height = asset.get("height")
                                        if width and height:
                                            break

                            if width and height:
                                creative_sizes.add((int(width), int(height)))

                # Build or filter placeholders based on actual creative sizes
                if creative_sizes:
                    if not creative_placeholders:
                        # No format_ids specified - create placeholders directly from creative sizes
                        # One placeholder per unique size
                        for width, height in sorted(creative_sizes):
                            creative_placeholders.append(
                                {
                                    "size": {"width": width, "height": height},
                                    "expectedCreativeCount": 1,
                                    "creativeSizeType": "PIXEL",
                                }
                            )
                        log(
                            f"  [blue]Created {len(creative_placeholders)} placeholders from actual creative sizes (no format_ids)[/blue]"
                        )
                    else:
                        # Filter existing placeholders to only include sizes that have creatives
                        filtered_placeholders = []
                        for placeholder in creative_placeholders:
                            placeholder_width = placeholder.get("size", {}).get("width")
                            placeholder_height = placeholder.get("size", {}).get("height")

                            # 1x1 placeholders are special (templates, native) - always include
                            if placeholder_width == 1 and placeholder_height == 1:
                                filtered_placeholders.append(placeholder)
                            # Include if we have creatives of this size
                            elif (placeholder_width, placeholder_height) in creative_sizes:
                                filtered_placeholders.append(placeholder)

                        if filtered_placeholders:
                            original_count = len(creative_placeholders)
                            creative_placeholders = filtered_placeholders
                            if len(creative_placeholders) < original_count:
                                log(
                                    f"  [blue]Filtered placeholders from {original_count} to {len(creative_placeholders)} based on actual creative sizes[/blue]"
                                )
                        # If filtering removed all placeholders, keep originals (fail-safe)
                        # This shouldn't happen if creatives have valid dimensions
            else:
                # No creatives in package - placeholders are optional
                # Allow empty array if no format_ids and no creatives
                if not creative_placeholders:
                    log(
                        "  [yellow]No creatives and no format_ids - line item will have no creative placeholders[/yellow]"
                    )

            # Determine goal type and units
            goal_type = impl_config.get("primary_goal_type", "LIFETIME")
            goal_unit_type = impl_config.get("primary_goal_unit_type", "IMPRESSIONS")

            if goal_type == "LIFETIME":
                goal_units = package.impressions
            elif goal_type == "DAILY":
                # For DAILY goals, divide total impressions by flight days
                goal_units = int(package.impressions / max(flight_duration_days, 1))
            else:
                # For other goal types (NONE, etc), use package impressions
                goal_units = package.impressions

            # Apply line item naming template
            from src.adapters.gam.utils.constants import GAM_NAME_LIMITS
            from src.adapters.gam.utils.naming import (
                apply_naming_template,
                build_line_item_name_context,
                truncate_name_with_suffix,
            )

            # Get product name from database for template
            product_name = product.get("product_id", package.name) if product else package.name

            line_item_name_context = build_line_item_name_context(
                order_name=order_name or f"Order {order_id}",
                product_name=product_name,
                package_name=package.name,
                package_index=package_index,
            )
            full_line_item_name = apply_naming_template(line_item_name_template, line_item_name_context)

            # Truncate to GAM's 255-character limit
            line_item_name = truncate_name_with_suffix(
                full_line_item_name, GAM_NAME_LIMITS["max_line_item_name_length"]
            )

            # Determine pricing configuration - use package_pricing_info if available, else fallback
            pricing_info = package_pricing_info.get(package.package_id) if package_pricing_info else None

            if pricing_info:
                # Use pricing info from AdCP request (AdCP PR #88)
                from src.adapters.gam.pricing_compatibility import PricingCompatibility

                pricing_model = pricing_info["pricing_model"]
                is_fixed_price = pricing_info["is_fixed"]  # Fixed vs auction pricing (pricing type)
                currency = pricing_info["currency"]

                # Determine delivery guarantee from product (not pricing)
                # IMPORTANT: delivery_type (guaranteed/non-guaranteed inventory) is SEPARATE from
                # is_fixed (fixed vs auction pricing). A product can be:
                # - guaranteed inventory with fixed pricing (STANDARD with CPM)
                # - guaranteed inventory with auction pricing (STANDARD with bid_price)
                # - non-guaranteed inventory with fixed pricing (PRICE_PRIORITY with CPM)
                # - non-guaranteed inventory with auction pricing (PRICE_PRIORITY with bid_price)
                delivery_type = product.get("delivery_type") if product else None
                is_guaranteed = delivery_type == "guaranteed"

                # Determine rate based on pricing type (fixed vs auction)
                if is_fixed_price:
                    # Fixed pricing: use rate directly
                    rate = pricing_info["rate"]
                else:
                    # Auction pricing: bid_price is REQUIRED
                    rate = pricing_info.get("bid_price")
                    if rate is None:
                        # No fallback - this is a client error
                        error_msg = (
                            f"Package '{package.package_id}' has auction pricing but no bid_price provided. "
                            f"Auction pricing (is_fixed=False) requires explicit bid_price in the request. "
                            f"Either provide bid_price or use fixed pricing."
                        )
                        log(f"[red]Error: {error_msg}[/red]")
                        raise ValueError(error_msg)

                # Validate rate is not None
                if rate is None:
                    error_msg = f"Package '{package.package_id}' has no valid rate. " f"Pricing info: {pricing_info}"
                    log(f"[red]Error: {error_msg}[/red]")
                    raise ValueError(error_msg)

                # Map AdCP pricing model to GAM cost type
                gam_cost_type = PricingCompatibility.get_gam_cost_type(pricing_model)

                # Handle FLAT_RATE → CPD conversion
                # AdCP FLAT_RATE rate = total campaign cost (e.g., $100 for entire campaign)
                # GAM CPD = cost per day (e.g., $10/day for 10-day campaign)
                if pricing_model == "flat_rate":
                    campaign_days = (end_time - start_time).days
                    if campaign_days < 1:
                        campaign_days = 1  # Minimum 1 day for same-day campaigns

                    cpd_rate = rate / campaign_days
                    log(f"  FLAT_RATE: ${rate:,.2f} total cost / {campaign_days} days → ${cpd_rate:,.2f} CPD")
                    cost_type = "CPD"
                    cost_per_unit_micro = int(cpd_rate * 1_000_000)
                else:
                    # For other pricing models, use rate directly (CPM, CPC, VCPM)
                    cost_type = gam_cost_type
                    cost_per_unit_micro = int(rate * 1_000_000)
                    log(f"  Pricing: {pricing_model.upper()} @ ${rate:,.2f} {currency} → GAM {cost_type}")

                # Select appropriate line item type based on pricing + guarantees
                # Automatically select based on pricing model and product's delivery guarantee
                # The select_line_item_type method ensures compatibility between pricing and line item type
                line_item_type = PricingCompatibility.select_line_item_type(pricing_model, is_guaranteed)
                priority = PricingCompatibility.get_default_priority(line_item_type)

                # Set goal type based on line item type (per GAM API v202411 documentation)
                # SPONSORSHIP: Only supports DAILY goal type (percentage-based)
                # NETWORK: Only supports DAILY goal type (percentage-based)
                # STANDARD: Supports LIFETIME goal type (impression-based)
                # PRICE_PRIORITY: Supports NONE, LIFETIME, DAILY goal types (impression-based)
                if line_item_type == "SPONSORSHIP":
                    # SPONSORSHIP line items require DAILY goals with percentage-based units
                    goal_type = "DAILY"

                    # For FLAT_RATE (CPD pricing), use 100% impression share
                    # This means: serve on 100% of matching ad requests
                    if pricing_model == "flat_rate":
                        goal_unit_type = "IMPRESSIONS"
                        goal_units = 100  # 100% impression share for flat rate sponsorships
                        log(
                            "  SPONSORSHIP (FLAT_RATE): DAILY goal with 100% impression share (serves on all matching requests)"
                        )
                    else:
                        log(f"  {line_item_type} line item: Using DAILY goal type (required by GAM API)")
                elif line_item_type == "STANDARD":
                    # STANDARD line items use LIFETIME goals for guaranteed delivery
                    goal_type = "LIFETIME"
                else:
                    # PRICE_PRIORITY, BULK, HOUSE can use configured goal type or default to LIFETIME
                    pass  # Keep goal_type from impl_config (set earlier)

                # Update goal units based on pricing model (for non-SPONSORSHIP or non-FLAT_RATE)
                if pricing_model != "flat_rate":
                    if pricing_model == "cpc":
                        # CPC: goal should be in clicks, not impressions
                        goal_unit_type = "CLICKS"
                        # Keep goal_units as-is (package.impressions serves as click goal)
                    elif pricing_model == "vcpm":
                        # VCPM: goal should be in viewable impressions
                        goal_unit_type = "VIEWABLE_IMPRESSIONS"
                        # Keep goal_units as-is (package.impressions serves as viewable impression goal)
                    else:
                        # CPM: use impressions (already set above)
                        pass

                log(
                    f"  Package pricing: {pricing_model.upper()} @ ${rate:,.2f} {currency} "
                    f"→ GAM {cost_type} @ ${rate:,.2f}, line_item_type={line_item_type}, priority={priority}"
                )
            else:
                # No pricing info provided - this is an error
                # We require explicit pricing info (either from request or product pricing options)
                error_msg = (
                    f"Package '{package.package_id}' has no pricing information. "
                    f"Pricing info must be provided either:\n"
                    f"  1. In the request (bid_price for auction pricing)\n"
                    f"  2. Via product pricing_options in database\n"
                    f"This package has no valid pricing configuration."
                )
                log(f"[red]Error: {error_msg}[/red]")
                raise ValueError(error_msg)

            # Build line item object
            # In dry-run mode, order_id is a string like 'dry_run_order_123'; use a dummy numeric ID
            # In real mode, order_id is numeric string that can be converted
            order_id_int = 999999999 if (self.dry_run and not order_id.isdigit()) else int(order_id)
            line_item = {
                "name": line_item_name,
                "orderId": order_id_int,
                "targeting": line_item_targeting,
                "creativePlaceholders": creative_placeholders,
                "lineItemType": line_item_type,
                "priority": priority,
                "costType": cost_type,
                "costPerUnit": {"currencyCode": currency, "microAmount": cost_per_unit_micro},
                "primaryGoal": {
                    "goalType": goal_type,
                    "unitType": goal_unit_type,
                    "units": goal_units,
                },
                "creativeRotationType": impl_config.get("creative_rotation_type", "EVEN"),
                "deliveryRateType": impl_config.get("delivery_rate_type", "EVENLY"),
                "startDateTime": {
                    "date": {"year": start_time.year, "month": start_time.month, "day": start_time.day},
                    "hour": start_time.hour,
                    "minute": start_time.minute,
                    "second": start_time.second,
                    "timeZoneId": impl_config.get("time_zone", "America/New_York"),
                },
                "endDateTime": {
                    "date": {"year": end_time.year, "month": end_time.month, "day": end_time.day},
                    "hour": end_time.hour,
                    "minute": end_time.minute,
                    "second": end_time.second,
                    "timeZoneId": impl_config.get("time_zone", "America/New_York"),
                },
                # Set status based on whether manual approval is required
                # DRAFT = needs manual approval, READY = ready to serve (when creatives added)
                "status": "READY",  # Always create as READY since creatives will be added
            }

            # Add frequency caps - merge buyer's frequency cap with product config
            frequency_caps = []

            # First, add product-level frequency caps from impl_config
            if impl_config.get("frequency_caps"):
                for cap in impl_config["frequency_caps"]:
                    frequency_caps.append(
                        {
                            "maxImpressions": cap["max_impressions"],
                            "numTimeUnits": cap["time_range"],
                            "timeUnit": cap["time_unit"],
                        }
                    )

            # Then, add buyer's frequency cap from targeting_overlay if present
            if targeting_overlay and targeting_overlay.frequency_cap:
                freq_cap = targeting_overlay.frequency_cap
                # Convert AdCP FrequencyCap (suppress_minutes) to GAM format
                # AdCP: suppress_minutes (e.g., 60 = 1 hour)
                # GAM: maxImpressions=1, numTimeUnits=X, timeUnit="MINUTE"/"HOUR"/"DAY"

                # Determine best GAM time unit
                if freq_cap.suppress_minutes < 60:
                    time_unit = "MINUTE"
                    num_time_units = freq_cap.suppress_minutes
                elif freq_cap.suppress_minutes < 1440:  # Less than 24 hours
                    time_unit = "HOUR"
                    num_time_units = freq_cap.suppress_minutes // 60
                else:
                    time_unit = "DAY"
                    num_time_units = freq_cap.suppress_minutes // 1440

                frequency_caps.append(
                    {
                        "maxImpressions": 1,  # Suppress after 1 impression
                        "numTimeUnits": num_time_units,
                        "timeUnit": time_unit,
                    }
                )
                log(f"Added buyer frequency cap: 1 impression per {num_time_units} {time_unit.lower()}(s)")

            if frequency_caps:
                line_item["frequencyCaps"] = frequency_caps

            # Add competitive exclusion labels
            if impl_config.get("competitive_exclusion_labels"):
                line_item["effectiveAppliedLabels"] = [
                    {"labelId": label} for label in impl_config["competitive_exclusion_labels"]
                ]

            # Add discount if configured
            if impl_config.get("discount_type") and impl_config.get("discount_value"):
                line_item["discount"] = impl_config["discount_value"]
                line_item["discountType"] = impl_config["discount_type"]

            # Determine environment type - prefer buyer's media_type, fallback to product config
            environment_type = line_item_targeting.get("_media_type_environment")  # From targeting overlay
            if not environment_type:
                environment_type = impl_config.get("environment_type", "BROWSER")

            # Clean up internal field from targeting
            if "_media_type_environment" in line_item_targeting:
                del line_item_targeting["_media_type_environment"]

            # Add video-specific settings
            if environment_type == "VIDEO_PLAYER":
                line_item["environmentType"] = "VIDEO_PLAYER"
                if impl_config.get("companion_delivery_option"):
                    line_item["companionDeliveryOption"] = impl_config["companion_delivery_option"]
                if impl_config.get("video_max_duration"):
                    line_item["videoMaxDuration"] = impl_config["video_max_duration"]
                if impl_config.get("skip_offset"):
                    line_item["videoSkippableAdType"] = "ENABLED"
                    line_item["videoSkipOffset"] = impl_config["skip_offset"]
            else:
                line_item["environmentType"] = environment_type

            # Advanced settings
            if impl_config.get("allow_overbook"):
                line_item["allowOverbook"] = True
            if impl_config.get("skip_inventory_check"):
                line_item["skipInventoryCheck"] = True
            if impl_config.get("disable_viewability_avg_revenue_optimization"):
                line_item["disableViewabilityAvgRevenueOptimization"] = True

            if self.dry_run:
                log(f"Would call: line_item_service.createLineItems(['{package.name}'])")
                log(f"  Package: {package.name}")
                log(f"  Line Item Type: {impl_config.get('line_item_type', 'STANDARD')}")
                log(f"  Priority: {impl_config.get('priority', 8)}")
                log(f"  CPM: ${package.cpm}")
                log(f"  Impressions Goal: {package.impressions:,}")
                log(f"  Creative Placeholders: {len(creative_placeholders)} sizes")
                for cp in creative_placeholders[:3]:
                    log(
                        f"    - {cp['size']['width']}x{cp['size']['height']} ({'Native' if cp.get('creativeSizeType') == 'NATIVE' else 'Display'})"
                    )
                if len(creative_placeholders) > 3:
                    log(f"    - ... and {len(creative_placeholders) - 3} more")
                created_line_item_ids.append(f"dry_run_line_item_{len(created_line_item_ids)}")
            else:
                try:
                    line_item_service = self.client_manager.get_service("LineItemService")
                    created_line_items = line_item_service.createLineItems([line_item])
                    if created_line_items:
                        line_item_id = str(created_line_items[0]["id"])
                        created_line_item_ids.append(line_item_id)
                        log(f"✓ Created LineItem ID: {line_item_id} for {package.name}")
                except Exception as e:
                    error_msg = f"Failed to create LineItem for {package.name}: {str(e)}"
                    log(f"[red]Error: {error_msg}[/red]")
                    log(f"[red]Targeting structure: {line_item_targeting}[/red]")
                    raise

        return created_line_item_ids

    @staticmethod
    def _safe_get_nested(obj, *keys, default=None):
        """Safely get nested attribute/dict value from GAM API response.

        Handles both dict and object responses from GAM API.

        Args:
            obj: Dict or object to traverse
            *keys: Keys/attributes to traverse (e.g., 'costPerUnit', 'microAmount')
            default: Default value if key not found

        Returns:
            Value at nested path, or default if not found
        """
        current = obj
        for key in keys:
            if current is None:
                return default
            if isinstance(current, dict):
                current = current.get(key)
            else:
                current = getattr(current, key, None)
            if current is None:
                return default
        return current if current is not None else default

    def update_line_item_budget(
        self, line_item_id: str, new_budget: float, pricing_model: str, currency: str = "USD", max_retries: int = 5
    ) -> bool:
        """Update line item budget in GAM by modifying costPerUnit and primaryGoal.

        Args:
            line_item_id: GAM line item ID
            new_budget: New budget amount
            pricing_model: Pricing model (cpm, cpc, vcpm, flat_rate)
            currency: Currency code (default: USD)
            max_retries: Maximum number of retries for NO_FORECAST_YET errors (default: 5)

        Returns:
            True if update successful, False otherwise
        """
        if self.dry_run:
            logger.info(
                f"[DRY RUN] Would update line item {line_item_id} budget to {new_budget} {currency} "
                f"(pricing: {pricing_model})"
            )
            return True

        import time

        for attempt in range(max_retries):
            try:
                line_item_service = self.client_manager.get_service("LineItemService")

                # Get current line item
                statement_builder = ad_manager.StatementBuilder()
                statement_builder.Where("id = :lineItemId")
                statement_builder.WithBindVariable("lineItemId", int(line_item_id))
                statement = statement_builder.ToStatement()

                result = line_item_service.getLineItemsByStatement(statement)
                line_items = result.get("results", []) if isinstance(result, dict) else getattr(result, "results", [])

                if not line_items:
                    logger.error(f"Line item {line_item_id} not found in GAM")
                    return False

                line_item = line_items[0]

                # Calculate new goal units based on pricing model
                # Budget = (costPerUnit / 1000) * goal_units for CPM
                # Budget = costPerUnit * goal_units for CPC
                # Use helper function to handle both dict and object responses from GAM API
                current_cost_per_unit_micro = self._safe_get_nested(line_item, "costPerUnit", "microAmount", default=0)
                current_cost_per_unit = float(current_cost_per_unit_micro) / 1_000_000

                if current_cost_per_unit <= 0:
                    logger.error(f"Invalid costPerUnit for line item {line_item_id}: {current_cost_per_unit}")
                    return False

                # Calculate new goal units based on pricing model
                if pricing_model in ["cpm", "vcpm"]:
                    # CPM/VCPM: budget = (rate / 1000) * impressions → impressions = budget / (rate / 1000)
                    new_goal_units = int((new_budget * 1000) / current_cost_per_unit)
                elif pricing_model == "cpc":
                    # CPC: budget = rate * clicks → clicks = budget / rate
                    new_goal_units = int(new_budget / current_cost_per_unit)
                elif pricing_model == "flat_rate":
                    # FLAT_RATE: Keep existing goal units (100% for sponsorship)
                    new_goal_units = self._safe_get_nested(line_item, "primaryGoal", "units", default=100)
                else:
                    logger.error(f"Unsupported pricing model for budget update: {pricing_model}")
                    return False

                # Update line item (works for both dict and object)
                if isinstance(line_item, dict):
                    line_item["primaryGoal"]["units"] = new_goal_units
                else:
                    line_item.primaryGoal.units = new_goal_units

                # Update the line item in GAM
                updated_line_items = line_item_service.updateLineItems([line_item])

                if updated_line_items:
                    logger.info(
                        f"✓ Updated line item {line_item_id} budget: ${new_budget} {currency} "
                        f"(goal units: {new_goal_units}, pricing: {pricing_model})"
                    )
                    return True
                else:
                    logger.error(f"Failed to update line item {line_item_id} - GAM API returned no results")
                    return False

            except Exception as e:
                error_str = str(e)
                # Check if this is a NO_FORECAST_YET error
                if "NO_FORECAST_YET" in error_str and attempt < max_retries - 1:
                    # Wait with exponential backoff (capped at 30s)
                    # Sequence: 5s, 10s, 20s, 30s, 30s (total ~95s for 5 retries)
                    wait_time = min(5 * (2**attempt), 30)
                    logger.warning(
                        f"⏳ Line item {line_item_id} forecasting not ready yet - retrying in {wait_time}s "
                        f"(attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(wait_time)
                    continue  # Retry
                else:
                    # Non-retryable error or last attempt
                    logger.error(f"Error updating line item {line_item_id} budget: {e}")
                    return False

        # All retries exhausted
        logger.error(f"Failed to update line item {line_item_id} budget after {max_retries} attempts")
        return False

    def pause_line_item(self, line_item_id: str) -> bool:
        """Pause line item in GAM by setting status to PAUSED.

        Args:
            line_item_id: GAM line item ID

        Returns:
            True if pause successful, False otherwise
        """
        return self._update_line_item_status(line_item_id, "PAUSED")

    def resume_line_item(self, line_item_id: str) -> bool:
        """Resume line item in GAM by setting status to READY.

        Args:
            line_item_id: GAM line item ID

        Returns:
            True if resume successful, False otherwise
        """
        return self._update_line_item_status(line_item_id, "READY")

    def _update_line_item_status(self, line_item_id: str, new_status: str) -> bool:
        """Update line item status in GAM.

        Args:
            line_item_id: GAM line item ID
            new_status: New status (READY, PAUSED, etc.)

        Returns:
            True if update successful, False otherwise
        """
        if self.dry_run:
            logger.info(f"[DRY RUN] Would update line item {line_item_id} status to {new_status}")
            return True

        try:
            line_item_service = self.client_manager.get_service("LineItemService")

            # Get current line item
            statement_builder = ad_manager.StatementBuilder()
            statement_builder.Where("id = :lineItemId")
            statement_builder.WithBindVariable("lineItemId", int(line_item_id))
            statement = statement_builder.ToStatement()

            result = line_item_service.getLineItemsByStatement(statement)
            line_items = result.get("results", []) if isinstance(result, dict) else getattr(result, "results", [])

            if not line_items:
                logger.error(f"Line item {line_item_id} not found in GAM")
                return False

            line_item = line_items[0]

            # Update status
            line_item["status"] = new_status

            # Update the line item in GAM
            updated_line_items = line_item_service.updateLineItems([line_item])

            if updated_line_items:
                logger.info(f"✓ Updated line item {line_item_id} status to {new_status}")
                return True
            else:
                logger.error(f"Failed to update line item {line_item_id} status - GAM API returned no results")
                return False

        except Exception as e:
            logger.error(f"Error updating line item {line_item_id} status: {e}")
            return False

    def get_advertisers(
        self, search_query: str | None = None, limit: int = 500, fetch_all: bool = False
    ) -> list[dict[str, Any]]:
        """Get list of advertisers (companies) from GAM for advertiser selection.

        Args:
            search_query: Optional search string to filter by name (uses LIKE '%query%')
            limit: Maximum number of results per page (default: 500, max: 500)
            fetch_all: If True, fetches ALL advertisers with pagination (can be slow for large networks)

        Returns:
            List of advertisers with id, name, and type for dropdown selection

        Performance Notes:
            - For networks with 1000+ advertisers, use search_query to filter results
            - fetch_all=True can take 5-10 seconds for networks with thousands of advertisers
            - Default behavior (limit=500, no search) is fast but may not return all advertisers
        """
        logger.info(f"Loading GAM advertisers (search={search_query}, limit={limit}, fetch_all={fetch_all})")

        if self.dry_run:
            logger.info("Would call: company_service.getCompaniesByStatement(WHERE type='ADVERTISER')")
            # Return mock data for dry-run
            return [
                {"id": "123456789", "name": "Test Advertiser 1", "type": "ADVERTISER"},
                {"id": "987654321", "name": "Test Advertiser 2", "type": "ADVERTISER"},
            ]

        try:
            company_service = self.client_manager.get_service("CompanyService")
            statement_builder = ad_manager.StatementBuilder()

            # Sanitize and validate search query
            if search_query:
                # Strip whitespace and limit length to prevent abuse
                search_query = search_query.strip()[:100]
                if not search_query:
                    # If query is empty after stripping, treat as no search
                    search_query = None

            # Build WHERE clause
            if search_query:
                # Use LIKE filter for name search (case-insensitive wildcard matching)
                # Note: WithBindVariable() properly escapes the search string, preventing SQL injection
                statement_builder.Where("type = :type AND name LIKE :search")
                statement_builder.WithBindVariable("type", "ADVERTISER")
                statement_builder.WithBindVariable("search", f"%{search_query}%")
                logger.info(f"Filtering advertisers by name LIKE '%{search_query}%'")
            else:
                statement_builder.Where("type = :type")
                statement_builder.WithBindVariable("type", "ADVERTISER")

            # Enforce max limit per page
            limit = min(limit, 500)

            advertisers = []
            total_result_set_size = 0

            if fetch_all:
                # Fetch ALL advertisers with pagination (can be slow for large networks)
                logger.info("Fetching all advertisers with pagination...")
                while True:
                    result = company_service.getCompaniesByStatement(statement_builder.ToStatement())

                    if result and hasattr(result, "results"):
                        total_result_set_size = int(getattr(result, "totalResultSetSize", 0))
                        logger.info(
                            f"Fetched {len(result.results)} advertisers "
                            f"(offset: {statement_builder.offset}, total: {total_result_set_size})"
                        )

                        for company in result.results:
                            advertisers.append(
                                {
                                    "id": str(company.id),
                                    "name": company.name,
                                    "type": company.type,
                                }
                            )

                        statement_builder.offset += len(result.results)

                        # Check if we've fetched all results
                        if statement_builder.offset >= total_result_set_size:
                            break
                    else:
                        logger.info("No advertisers found")
                        break
            else:
                # Fetch first page only (fast, but may not return all advertisers)
                statement_builder.Limit(limit)
                result = company_service.getCompaniesByStatement(statement_builder.ToStatement())

                if result and hasattr(result, "results"):
                    total_result_set_size = int(getattr(result, "totalResultSetSize", 0))
                    logger.info(
                        f"Fetched {len(result.results)} of {total_result_set_size} total advertisers (limited to {limit})"
                    )

                    for company in result.results:
                        advertisers.append(
                            {
                                "id": str(company.id),
                                "name": company.name,
                                "type": company.type,
                            }
                        )

            logger.info(f"✓ Loaded {len(advertisers)} advertisers from GAM")
            return sorted(advertisers, key=lambda x: x["name"])

        except Exception as e:
            logger.error(f"Error loading advertisers: {str(e)}")
            return []
