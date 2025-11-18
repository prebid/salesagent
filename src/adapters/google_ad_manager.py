"""
Google Ad Manager (GAM) Adapter - Refactored Version

This is the refactored Google Ad Manager adapter that uses a modular architecture.
The main adapter class acts as an orchestrator, delegating specific operations
to specialized manager classes.
"""

# Export constants for backward compatibility
__all__ = [
    "GUARANTEED_LINE_ITEM_TYPES",
    "NON_GUARANTEED_LINE_ITEM_TYPES",
]

import logging
import uuid
from datetime import datetime
from typing import Any, cast

from flask import Flask

from src.adapters.base import AdServerAdapter

# Import modular components
from src.adapters.gam.client import GAMClientManager
from src.adapters.gam.managers import (
    GAMCreativesManager,
    GAMInventoryManager,
    GAMOrdersManager,
    GAMSyncManager,
    GAMTargetingManager,
    GAMWorkflowManager,
)

# Re-export constants for backward compatibility
from src.adapters.gam.managers.orders import (
    GUARANTEED_LINE_ITEM_TYPES,
    NON_GUARANTEED_LINE_ITEM_TYPES,
)
from src.adapters.gam.pricing_compatibility import PricingCompatibility
from src.core.audit_logger import AuditLogger
from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    AssetStatus,
    CheckMediaBuyStatusResponse,
    CreateMediaBuyError,
    CreateMediaBuyRequest,
    CreateMediaBuyResponse,
    CreateMediaBuySuccess,
    Error,
    MediaPackage,
    ReportingPeriod,
    UpdateMediaBuyError,
    UpdateMediaBuyResponse,
    UpdateMediaBuySuccess,
)

# Set up logger
logger = logging.getLogger(__name__)


class GoogleAdManager(AdServerAdapter):
    """Google Ad Manager adapter using modular architecture."""

    def __init__(
        self,
        config: dict[str, Any],
        principal,
        *,
        network_code: str,
        advertiser_id: str | None = None,
        trafficker_id: str | None = None,
        dry_run: bool = False,
        audit_logger: AuditLogger | None = None,
        tenant_id: str | None = None,
    ):
        """Initialize Google Ad Manager adapter with modular managers.

        Args:
            config: Configuration dictionary
            principal: Principal object for authentication
            network_code: GAM network code
            advertiser_id: GAM advertiser ID (optional, required only for order/campaign operations)
            trafficker_id: GAM trafficker ID (optional, required only for order/campaign operations)
            dry_run: Whether to run in dry-run mode
            audit_logger: Audit logging instance
            tenant_id: Tenant identifier
        """
        super().__init__(config, principal, dry_run, None, tenant_id)

        self.network_code = network_code
        self.advertiser_id = advertiser_id
        self.trafficker_id = trafficker_id
        self.refresh_token = config.get("refresh_token")
        self.key_file = config.get("service_account_key_file")
        self.service_account_json = config.get("service_account_json")
        self.principal = principal

        # Validate configuration
        if not self.network_code:
            raise ValueError("GAM config requires 'network_code'")

        # Validate advertiser_id is numeric if provided (GAM expects integer company IDs)
        if advertiser_id is not None and advertiser_id != "":
            # Check if it's numeric (as string or int)
            try:
                int(advertiser_id)
            except (ValueError, TypeError):
                raise ValueError(
                    f"GAM advertiser_id must be numeric (got: '{advertiser_id}'). "
                    f"Check principal platform_mappings configuration."
                )

        # advertiser_id is only required for order/campaign operations, not inventory sync

        # Skip auth validation in dry_run mode (for testing)
        if not self.dry_run:
            if not self.key_file and not self.service_account_json and not self.refresh_token:
                raise ValueError(
                    "GAM config requires either 'service_account_key_file', 'service_account_json', or 'refresh_token'"
                )

        # Initialize modular components
        if not self.dry_run:
            self.client_manager = GAMClientManager(self.config, self.network_code)
            # Legacy client property for backward compatibility
            self.client = self.client_manager.get_client()

            # Auto-detect trafficker_id if not provided
            if not self.trafficker_id:
                try:
                    user_service = self.client.GetService("UserService", version="v202411")
                    current_user = user_service.getCurrentUser()
                    self.trafficker_id = str(current_user["id"])
                    logger.info(
                        f"Auto-detected trafficker_id: {self.trafficker_id} ({current_user.get('name', 'Unknown')})"
                    )
                except Exception as e:
                    logger.warning(f"Could not auto-detect trafficker_id: {e}")

            # Initialize manager components
            self.targeting_manager = GAMTargetingManager(tenant_id or "", gam_client=self.client)

            # Initialize orders manager (advertiser_id/trafficker_id optional for query operations)
            self.orders_manager = GAMOrdersManager(self.client_manager, self.advertiser_id, self.trafficker_id, dry_run)

            # Only initialize creative manager if we have advertiser_id (required for creative operations)
            if self.advertiser_id and self.trafficker_id:
                self.creatives_manager = GAMCreativesManager(
                    self.client_manager, self.advertiser_id, dry_run, self.log, self
                )
            else:
                self.creatives_manager = None  # type: ignore[assignment]

            # Inventory manager doesn't need advertiser_id
            self.inventory_manager = GAMInventoryManager(self.client_manager, tenant_id or "", dry_run)

            # Sync manager only needs inventory manager for inventory sync
            self.sync_manager = GAMSyncManager(
                self.client_manager, self.inventory_manager, self.orders_manager, tenant_id or "", dry_run
            )
            self.workflow_manager = GAMWorkflowManager(tenant_id or "", principal, audit_logger, self.log)
        else:
            self.client_manager = None  # type: ignore[assignment]
            self.client = None
            self.log("[yellow]Running in dry-run mode - GAM client not initialized[/yellow]")

            # Initialize managers for dry-run mode (they can work without real client)
            self.targeting_manager = GAMTargetingManager(tenant_id or "")

            # Initialize orders manager in dry-run mode
            self.orders_manager = GAMOrdersManager(None, self.advertiser_id, self.trafficker_id, dry_run=True)  # type: ignore[arg-type]

            # Only initialize creative manager if we have advertiser_id (required for creative operations)
            if self.advertiser_id and self.trafficker_id:
                self.creatives_manager = GAMCreativesManager(
                    None, self.advertiser_id, dry_run=True, log_func=self.log, adapter=self  # type: ignore[arg-type]
                )
            else:
                self.creatives_manager = None  # type: ignore[assignment]

            # Initialize inventory manager in dry-run mode
            self.inventory_manager = GAMInventoryManager(None, tenant_id or "", dry_run=True)  # type: ignore[arg-type]

            # Initialize sync manager in dry-run mode
            self.sync_manager = GAMSyncManager(
                None, self.inventory_manager, self.orders_manager, tenant_id or "", dry_run=True  # type: ignore[arg-type]
            )

            # Initialize workflow manager (doesn't need client)
            self.workflow_manager = GAMWorkflowManager(tenant_id or "", principal, audit_logger, self.log)

        # Initialize legacy validator for backward compatibility
        from .gam.utils.validation import GAMValidator

        self.validator = GAMValidator()

    # Legacy methods for backward compatibility - delegated to managers
    def _init_client(self):
        """Initializes the Ad Manager client (legacy - now handled by client manager)."""
        if self.client_manager:
            return self.client_manager.get_client()
        return None

    def _get_oauth_credentials(self):
        """Get OAuth credentials (legacy - now handled by auth manager)."""
        if self.client_manager:
            return self.client_manager.auth_manager.get_credentials()
        return None

    # Legacy targeting methods - delegated to targeting manager
    def _validate_targeting(self, targeting_overlay):
        """Validate targeting and return unsupported features (delegated to targeting manager)."""
        return self.targeting_manager.validate_targeting(targeting_overlay)

    def _build_targeting(self, targeting_overlay):
        """Build GAM targeting criteria from AdCP targeting (delegated to targeting manager)."""
        return self.targeting_manager.build_targeting(targeting_overlay)

    # HITL (Human-in-the-Loop) support methods
    def _requires_manual_approval(self, operation: str) -> bool:
        """Check if an operation requires manual approval based on configuration.

        Args:
            operation: The operation name (e.g., 'create_media_buy', 'add_creative_assets')

        Returns:
            bool: True if manual approval is required for this operation
        """
        return self.manual_approval_required and operation in self.manual_approval_operations

    # Legacy admin/business logic methods for backward compatibility
    def _is_admin_principal(self) -> bool:
        """Check if the current principal has admin privileges."""
        if not hasattr(self.principal, "platform_mappings"):
            return False

        gam_mappings = self.principal.platform_mappings.get("google_ad_manager", {})
        return bool(gam_mappings.get("gam_admin", False) or gam_mappings.get("is_admin", False))

    def _validate_creative_for_gam(self, asset):
        """Validate creative asset for GAM requirements (delegated to creatives manager)."""
        if not self.creatives_manager:
            raise ValueError("GAM adapter not configured for creative operations")
        return self.creatives_manager._validate_creative_for_gam(asset)

    def _get_creative_type(self, asset):
        """Determine creative type from asset (delegated to creatives manager)."""
        if not self.creatives_manager:
            raise ValueError("GAM adapter not configured for creative operations")
        return self.creatives_manager._get_creative_type(asset)

    def _create_gam_creative(self, asset, creative_type, asset_placeholders):
        """Create a GAM creative (delegated to creatives manager)."""
        if not self.creatives_manager:
            raise ValueError("GAM adapter not configured for creative operations")
        return self.creatives_manager._create_gam_creative(asset, creative_type, asset_placeholders)

    def _check_order_has_guaranteed_items(self, order_id):
        """Check if order has guaranteed line items (delegated to orders manager)."""
        if not self.orders_manager:
            raise ValueError("GAM adapter not configured for order operations")
        return self.orders_manager.check_order_has_guaranteed_items(order_id)

    def get_supported_pricing_models(self) -> set[str]:
        """Return set of pricing models GAM adapter supports.

        Google Ad Manager supports:
        - CPM: All line item types
        - VCPM: STANDARD only (viewable CPM)
        - CPC: STANDARD, SPONSORSHIP, NETWORK, PRICE_PRIORITY
        - FLAT_RATE: SPONSORSHIP (translated to CPD internally)

        Returns:
            Set of pricing model strings supported by this adapter
        """
        return {"cpm", "vcpm", "cpc", "flat_rate"}

    # Legacy properties for backward compatibility
    @property
    def GEO_COUNTRY_MAP(self):
        return self.targeting_manager.geo_country_map

    @property
    def GEO_REGION_MAP(self):
        return self.targeting_manager.geo_region_map

    @property
    def GEO_METRO_MAP(self):
        return self.targeting_manager.geo_metro_map

    @property
    def DEVICE_TYPE_MAP(self):
        return self.targeting_manager.DEVICE_TYPE_MAP

    @property
    def SUPPORTED_MEDIA_TYPES(self):
        return self.targeting_manager.SUPPORTED_MEDIA_TYPES

    def create_media_buy(
        self,
        request: CreateMediaBuyRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
        package_pricing_info: dict[str, dict] | None = None,
    ) -> CreateMediaBuyResponse:
        """Create a new media buy (order) in GAM - main orchestration method.

        Args:
            request: Full create media buy request
            packages: Simplified package models
            start_time: Campaign start time
            end_time: Campaign end time
            package_pricing_info: Optional validated pricing info (AdCP PR #88)
                Maps package_id → {pricing_model, rate, currency, is_fixed, bid_price}

        Returns:
            CreateMediaBuyResponse with GAM order details
        """
        self.log("[bold]GoogleAdManager.create_media_buy[/bold] - Creating GAM order")

        # Validate pricing models - check GAM compatibility
        if package_pricing_info:
            for pkg_id, pricing in package_pricing_info.items():
                pricing_model = pricing["pricing_model"]

                # Check if pricing model is supported by GAM adapter at all
                try:
                    gam_cost_type = PricingCompatibility.get_gam_cost_type(pricing_model)
                except ValueError as e:
                    error_msg = (
                        f"Google Ad Manager adapter does not support '{pricing_model}' pricing. "
                        f"Supported pricing models: CPM, VCPM, CPC, FLAT_RATE. "
                        f"The requested pricing model ('{pricing_model}') is not available in GAM. "
                        f"Please choose a product with compatible pricing."
                    )
                    self.log(f"[red]Error: {error_msg}[/red]")
                    return CreateMediaBuyError(
                        errors=[Error(code="unsupported_pricing_model", message=error_msg, details=None)],
                    )

                self.log(
                    f"📊 Package {pkg_id} pricing: {pricing_model} → GAM {gam_cost_type} "
                    f"({pricing['currency']}, {'fixed' if pricing['is_fixed'] else 'auction'})"
                )

        # Validate that advertiser_id and trafficker_id are configured
        if not self.advertiser_id or not self.trafficker_id:
            error_msg = "GAM adapter is not fully configured for order creation. Missing required configuration: "
            missing = []
            if not self.advertiser_id:
                missing.append("advertiser_id (company_id)")
            if not self.trafficker_id:
                missing.append("trafficker_id")
            error_msg += ", ".join(missing)

            self.log(f"[red]Error: {error_msg}[/red]")
            return CreateMediaBuyError(
                errors=[Error(code="configuration_error", message=error_msg, details=None)],
            )

        # Get products to access implementation_config

        from src.core.database.database_session import get_db_session
        from src.core.database.models import GAMInventory, Product, ProductInventoryMapping

        products_map = {}
        with get_db_session() as db_session:
            for package in packages:
                from sqlalchemy import select

                # Extract product_id from package (not package_id!)
                # package.package_id is like "pkg_prod_610fbb8b_08dec6ae_1"
                # package.product_id is like "prod_610fbb8b"
                product_id = package.product_id
                logger.info(f"Looking up product for package {package.package_id}: product_id={product_id}")

                stmt = select(Product).filter_by(
                    tenant_id=self.tenant_id,
                    product_id=product_id,
                )
                product = db_session.scalars(stmt).first()
                if product:
                    logger.info(f"Found product: {product.product_id} (name={product.name})")
                    # Start with product's implementation_config
                    impl_config = product.implementation_config.copy() if product.implementation_config else {}
                    logger.info(f"Product implementation_config: {impl_config}")

                    # Load inventory mappings from ProductInventoryMapping table
                    inventory_stmt = select(ProductInventoryMapping).filter_by(
                        product_id=product.product_id, tenant_id=self.tenant_id  # Use product_id string, not integer id
                    )
                    inventory_mappings = db_session.scalars(inventory_stmt).all()
                    logger.info(f"Found {len(inventory_mappings)} inventory mappings for product {product.product_id}")

                    if inventory_mappings:
                        # Get GAM ad unit IDs from the mappings
                        ad_unit_ids = []
                        placement_ids = []
                        for mapping in inventory_mappings:
                            logger.info(
                                f"Processing mapping: type={mapping.inventory_type}, inventory_id={mapping.inventory_id}"
                            )
                            if mapping.inventory_type == "ad_unit":
                                # Load the actual GAM inventory record
                                gam_inv_stmt = select(GAMInventory).filter_by(
                                    inventory_id=mapping.inventory_id,  # Match by inventory_id string
                                    inventory_type="ad_unit",
                                    tenant_id=self.tenant_id,
                                )
                                gam_inv = db_session.scalars(gam_inv_stmt).first()
                                if gam_inv:
                                    logger.info(f"Found GAM ad unit: {gam_inv.inventory_id}")
                                    ad_unit_ids.append(str(gam_inv.inventory_id))
                                else:
                                    logger.warning(f"GAM ad unit not found for inventory_id={mapping.inventory_id}")
                            elif mapping.inventory_type == "placement":
                                # Load the actual GAM placement record
                                gam_inv_stmt = select(GAMInventory).filter_by(
                                    inventory_id=mapping.inventory_id,
                                    inventory_type="placement",
                                    tenant_id=self.tenant_id,
                                )
                                gam_inv = db_session.scalars(gam_inv_stmt).first()
                                if gam_inv:
                                    logger.info(f"Found GAM placement: {gam_inv.inventory_id}")
                                    placement_ids.append(str(gam_inv.inventory_id))
                                else:
                                    logger.warning(f"GAM placement not found for inventory_id={mapping.inventory_id}")

                        # Merge inventory mappings into implementation_config
                        if ad_unit_ids:
                            impl_config["targeted_ad_unit_ids"] = ad_unit_ids
                            logger.info(f"Set targeted_ad_unit_ids: {ad_unit_ids}")
                        if placement_ids:
                            impl_config["targeted_placement_ids"] = placement_ids
                            logger.info(f"Set targeted_placement_ids: {placement_ids}")

                    logger.info(f"Final impl_config for {package.package_id}: {impl_config}")
                    products_map[package.package_id] = {
                        "product_id": product.product_id,
                        "implementation_config": impl_config,
                    }
                else:
                    logger.error(f"Product NOT FOUND for package_id: {package.package_id}")

        # Validate products have required inventory targeting BEFORE creating order
        # This prevents the "hidden failure" where order is created but line items fail
        for package in packages:
            product_config = products_map.get(package.package_id)
            if not product_config:
                error_msg = (
                    f"Product configuration missing for package '{package.package_id}'. "
                    f"Product must exist in database with valid configuration before media buy creation."
                )
                self.log(f"[red]Error: {error_msg}[/red]")
                return CreateMediaBuyError(
                    errors=[Error(code="product_not_configured", message=error_msg, details=None)],
                )

            # Cast to dict to satisfy mypy (products_map values are dict[str, Any])
            product_impl_config = cast(dict[str, Any], product_config.get("implementation_config", {}))
            has_ad_units = product_impl_config.get("targeted_ad_unit_ids")
            has_placements = product_impl_config.get("targeted_placement_ids")

            if not has_ad_units and not has_placements:
                pkg_product_id = product_config.get("product_id", package.package_id)
                error_msg = (
                    f"Product '{pkg_product_id}' (package '{package.package_id}') is not configured with inventory targeting. "
                    f"GAM requires all products to have either ad units or placements configured. "
                    f"\n\n⚠️  SETUP REQUIRED: Please configure this product's inventory before accepting media buy requests."
                    f"\n\nTo fix:"
                    f"\n  1. Go to Admin UI → Products → '{pkg_product_id}'"
                    f"\n  2. Click 'Sync Inventory' to load ad units from GAM"
                    f"\n  3. Assign ad units or placements to this product"
                    f"\n  4. Save changes"
                    f"\n\nAlternatively, for testing you can use Mock adapter instead of GAM (set ad_server='mock' on tenant)."
                )
                self.log(f"[red]Error: {error_msg}[/red]")
                return CreateMediaBuyError(
                    errors=[Error(code="product_not_configured", message=error_msg, details=None)],
                )

        # Validate targeting
        unsupported_features = self._validate_targeting(request.targeting_overlay)
        if unsupported_features:
            error_msg = f"Unsupported targeting features: {', '.join(unsupported_features)}"
            self.log(f"[red]Error: {error_msg}[/red]")
            return CreateMediaBuyError(
                errors=[Error(code="unsupported_targeting", message=error_msg, details=None)],
            )

        # Build base targeting from targeting overlay
        base_targeting = self._build_targeting(request.targeting_overlay)

        # Check if manual approval is required for media buy creation
        # Skip approval workflow if this media buy was already manually approved
        # (when called from execute_approved_media_buy, we're in "post-approval execution" mode)
        already_approved = getattr(request, "_already_approved", False)
        if self._requires_manual_approval("create_media_buy") and not already_approved:
            self.log("[yellow]Manual approval mode - creating workflow step for human intervention[/yellow]")

            # Generate a media buy ID for tracking
            media_buy_id = f"gam_order_{uuid.uuid4().hex[:8]}"

            # Create manual order workflow step
            step_id = self.workflow_manager.create_manual_order_workflow_step(
                request, packages, start_time, end_time, media_buy_id
            )

            # Build package responses - Per AdCP spec, CreateMediaBuyResponse.Package only contains:
            # - buyer_ref (required)
            # - package_id (required)
            package_responses = []
            for idx, package in enumerate(packages):
                # Get matching request package for buyer_ref
                matching_req_package = None
                if request.packages and idx < len(request.packages):
                    matching_req_package = request.packages[idx]

                buyer_ref = "unknown"  # Default fallback
                if matching_req_package and hasattr(matching_req_package, "buyer_ref"):
                    buyer_ref = matching_req_package.buyer_ref or buyer_ref

                # Create minimal AdCP-compliant Package response
                package_responses.append(
                    {
                        "buyer_ref": buyer_ref,
                        "package_id": package.package_id,
                    }
                )

            if step_id:
                return CreateMediaBuySuccess(
                    buyer_ref=request.buyer_ref or "",
                    media_buy_id=media_buy_id,
                    creative_deadline=None,
                    workflow_step_id=step_id,
                    packages=package_responses,  # type: ignore[arg-type]
                )
            else:
                error_msg = "Failed to create manual order workflow step"
                return CreateMediaBuyError(
                    errors=[Error(code="workflow_creation_failed", message=error_msg, details=None)],
                )

        # Automatic mode - create order directly
        # Use naming template from adapter config, or fallback to default
        from sqlalchemy import select

        from src.adapters.gam.utils.constants import GAM_NAME_LIMITS
        from src.adapters.gam.utils.naming import truncate_name_with_suffix
        from src.core.database.database_session import get_db_session
        from src.core.database.models import AdapterConfig
        from src.core.utils.naming import apply_naming_template, build_order_name_context

        order_name_template = "{campaign_name|brand_name} - {date_range}"  # Default
        tenant_gemini_key = None
        with get_db_session() as db_session:
            from src.core.database.models import Tenant

            adapter_stmt = select(AdapterConfig).filter_by(tenant_id=self.tenant_id)
            adapter_config = db_session.scalars(adapter_stmt).first()
            if adapter_config and adapter_config.gam_order_name_template:
                order_name_template = adapter_config.gam_order_name_template

            # Get tenant's Gemini key for auto_name generation
            tenant_stmt = select(Tenant).filter_by(tenant_id=self.tenant_id)
            tenant = db_session.scalars(tenant_stmt).first()
            if tenant:
                tenant_gemini_key = tenant.gemini_api_key

        context = build_order_name_context(request, packages, start_time, end_time, tenant_gemini_key)
        base_order_name = apply_naming_template(order_name_template, context)

        # Add unique identifier to prevent duplicate order names
        # Use media_buy_id if available (from buyer_ref), otherwise timestamp
        unique_suffix = request.buyer_ref or f"mb_{int(datetime.now().timestamp())}"
        full_order_name = f"{base_order_name} [{unique_suffix}]"

        # Truncate to GAM's 255-character limit while preserving the unique suffix
        order_name = truncate_name_with_suffix(full_order_name, GAM_NAME_LIMITS["max_order_name_length"])

        # Calculate total budget from package budgets (AdCP v2.2.0)
        total_budget_amount = request.get_total_budget()

        order_id = self.orders_manager.create_order(
            order_name=order_name,
            total_budget=total_budget_amount,
            start_time=start_time,
            end_time=end_time,
        )

        self.log(f"✓ Created GAM Order ID: {order_id}")

        # Create line items for each package
        try:
            line_item_ids = self.orders_manager.create_line_items(
                order_id=order_id,
                packages=packages,
                start_time=start_time,
                end_time=end_time,
                targeting=base_targeting,
                products_map=products_map,
                log_func=self.log,
                tenant_id=self.tenant_id,
                order_name=order_name,
                targeting_overlay=request.targeting_overlay,
                package_pricing_info=package_pricing_info,
            )
            self.log(f"✓ Created {len(line_item_ids)} line items")

            # NOTE: platform_line_item_id persistence is handled by media_buy_create.py
            # after response object is returned. See CreateMediaBuySuccess._platform_line_item_ids mapping.

            # Approve the order now that it has line items
            # GAM requires line items to exist before an order can be APPROVED
            # Try once - if forecasting not ready, start background task
            self.log(f"[cyan]Attempting to approve GAM Order {order_id} (max_retries=1)[/cyan]")
            try:
                approval_success = self.orders_manager.approve_order(order_id, max_retries=1)
                if approval_success:
                    self.log(f"✓ Approved GAM Order {order_id}")
                else:
                    # Approval failed (likely NO_FORECAST_YET) - start background polling
                    self.log(
                        f"[yellow]Order {order_id} forecasting not ready - starting background approval task[/yellow]"
                    )

                    # Get webhook URL from push notification config
                    webhook_url = None
                    if request.push_notification_config:
                        webhook_url = request.push_notification_config.get("url")

                    # Get principal_id from adapter's principal object
                    principal_id = self.principal.principal_id if hasattr(self.principal, "principal_id") else "unknown"

                    # Start background approval polling task
                    from src.services.order_approval_service import start_order_approval_background

                    try:
                        approval_id = start_order_approval_background(
                            order_id=order_id,
                            media_buy_id=order_id,  # In automatic mode, media_buy_id = order_id
                            tenant_id=self.tenant_id or "",
                            principal_id=principal_id,
                            webhook_url=webhook_url,
                            max_attempts=12,  # 2 minutes with 10 second intervals
                            poll_interval_seconds=10,
                        )
                        self.log(f"✓ Started background approval polling (job: {approval_id})")
                    except ValueError as e:
                        self.log(f"[red]Failed to start background approval: {e}[/red]")
            except Exception as approval_error:
                # Non-fatal error - order and line items were created successfully
                self.log(f"[yellow]Warning: Could not approve order {order_id}: {approval_error}[/yellow]")

        except Exception as e:
            error_msg = f"Order created but failed to create line items: {str(e)}"
            self.log(f"[red]Error: {error_msg}[/red]")

            # CRITICAL: Return media_buy_id=None to indicate failure
            # Even though order was created, line items failed, so media buy is not functional
            # Per AdCP spec: errors present → media_buy_id must be None
            return CreateMediaBuyError(
                errors=[Error(code="line_item_creation_failed", message=error_msg, details=None)],
            )

        # Check if activation approval is needed (guaranteed line items require human approval)
        has_guaranteed, item_types = self._check_order_has_guaranteed_items(order_id)
        if has_guaranteed:
            self.log("[yellow]Order contains guaranteed line items - creating activation workflow step[/yellow]")

            step_id = self.workflow_manager.create_activation_workflow_step(order_id, packages)

            # Build package responses - Per AdCP spec, CreateMediaBuyResponse.Package only contains:
            # - buyer_ref (required)
            # - package_id (required)
            package_responses = []
            for idx, (package, _line_item_id) in enumerate(zip(packages, line_item_ids, strict=False)):
                # Get matching request package for buyer_ref
                matching_req_package = None
                if request.packages and idx < len(request.packages):
                    matching_req_package = request.packages[idx]

                buyer_ref = "unknown"  # Default fallback
                if matching_req_package and hasattr(matching_req_package, "buyer_ref"):
                    buyer_ref = matching_req_package.buyer_ref or buyer_ref

                # Create minimal AdCP-compliant Package response
                package_responses.append(
                    {
                        "buyer_ref": buyer_ref,
                        "package_id": package.package_id,
                    }
                )

            # Create response and attach platform_line_item_id mapping for database persistence
            # This mapping is used by media_buy_create.py to update MediaPackage records
            response = CreateMediaBuySuccess(
                buyer_ref=request.buyer_ref or "",
                media_buy_id=order_id,
                creative_deadline=None,
                workflow_step_id=step_id,
                packages=package_responses,  # type: ignore[arg-type]
            )

            # Store platform_line_item_id mapping as a non-standard attribute
            # This survives Pydantic validation since it's set after construction
            # Build mapping from parallel arrays: packages (with package_id) and line_item_ids
            platform_line_item_ids = {}
            for package, line_item_id in zip(packages, line_item_ids, strict=False):
                platform_line_item_ids[package.package_id] = line_item_id

            self.log(f"[DEBUG] Guaranteed path: Created platform_line_item_ids mapping: {platform_line_item_ids}")

            # Attach to response object (bypass Pydantic validation)
            object.__setattr__(response, "_platform_line_item_ids", platform_line_item_ids)
            self.log("[DEBUG] Attached _platform_line_item_ids to response object")
            self.log(f"[DEBUG] Verify attribute exists: {hasattr(response, '_platform_line_item_ids')}")

            return response

        # Build package responses - Per AdCP spec, CreateMediaBuyResponse.Package only contains:
        # - buyer_ref (required)
        # - package_id (required)
        package_responses = []
        for idx, (package, _line_item_id) in enumerate(zip(packages, line_item_ids, strict=False)):
            # Get matching request package for buyer_ref
            matching_req_package = None
            if request.packages and idx < len(request.packages):
                matching_req_package = request.packages[idx]

            buyer_ref = "unknown"  # Default fallback
            if matching_req_package and hasattr(matching_req_package, "buyer_ref"):
                buyer_ref = matching_req_package.buyer_ref or buyer_ref

            # Create minimal AdCP-compliant Package response
            package_responses.append(
                {
                    "buyer_ref": buyer_ref,
                    "package_id": package.package_id,
                }
            )

        # Create response and store platform_line_item_id mapping for database persistence
        # This mapping is used by media_buy_create.py to update MediaPackage records
        response = CreateMediaBuySuccess(
            buyer_ref=request.buyer_ref or "",
            media_buy_id=order_id,
            creative_deadline=None,
            packages=package_responses,  # type: ignore[arg-type]
        )

        # Store platform_line_item_id mapping as a non-standard attribute
        # This survives Pydantic validation since it's set after construction
        # Build mapping from parallel arrays: packages (with package_id) and line_item_ids
        platform_line_item_ids = {}
        for package, line_item_id in zip(packages, line_item_ids, strict=False):
            platform_line_item_ids[package.package_id] = line_item_id

        self.log(f"[DEBUG] Created platform_line_item_ids mapping: {platform_line_item_ids}")

        # Attach to response object (bypass Pydantic validation)
        object.__setattr__(response, "_platform_line_item_ids", platform_line_item_ids)
        self.log("[DEBUG] Attached _platform_line_item_ids to response object")
        self.log(f"[DEBUG] Verify attribute exists: {hasattr(response, '_platform_line_item_ids')}")

        return response

    def archive_order(self, order_id: str) -> bool:
        """Archive a GAM order for cleanup purposes (delegated to orders manager)."""
        if not self.advertiser_id or not self.trafficker_id:
            self.log(
                "[red]Error: GAM adapter not configured for order operations (missing advertiser_id or trafficker_id)[/red]"
            )
            return False
        return self.orders_manager.archive_order(order_id)

    def get_advertisers(
        self, search_query: str | None = None, limit: int = 500, fetch_all: bool = False
    ) -> list[dict[str, Any]]:
        """Get list of advertisers from GAM (delegated to orders manager).

        Args:
            search_query: Optional search string to filter by name (uses LIKE '%query%')
            limit: Maximum number of results per page (default: 500, max: 500)
            fetch_all: If True, fetches ALL advertisers with pagination (can be slow for large networks)

        Returns:
            List of advertisers with id, name, and type
        """
        return self.orders_manager.get_advertisers(search_query=search_query, limit=limit, fetch_all=fetch_all)

    def add_creative_assets(
        self, media_buy_id: str, assets: list[dict[str, Any]], today: datetime
    ) -> list[AssetStatus]:
        """Create and associate creatives with line items (delegated to creatives manager)."""

        # Validate that creatives manager is initialized
        if not self.creatives_manager:
            error_msg = "GAM adapter is not fully configured for creative operations. Missing required configuration: "
            missing = []
            if not self.advertiser_id:
                missing.append("advertiser_id (company_id)")
            if not self.trafficker_id:
                missing.append("trafficker_id")
            error_msg += ", ".join(missing)

            self.log(f"[red]Error: {error_msg}[/red]")
            return [
                AssetStatus(
                    asset_id=asset.get("asset_id", f"failed_{i}"),
                    status="failed",
                    message=error_msg,
                    creative_id=None,
                )
                for i, asset in enumerate(assets)
            ]

        # Check if manual approval is required for creative assets
        if self._requires_manual_approval("add_creative_assets"):
            self.log("[yellow]Manual approval mode - creating workflow step for creative asset approval[/yellow]")

            # Create approval workflow step
            step_id = self.workflow_manager.create_approval_workflow_step(media_buy_id, "creative_assets_approval")

            if step_id:
                # Return asset statuses indicating they are awaiting approval
                asset_statuses: list[AssetStatus] = []
                for asset in assets:
                    asset_statuses.append(
                        AssetStatus(
                            asset_id=asset.get("asset_id", f"pending_{len(asset_statuses)}"),
                            status="submitted",
                            message=f"Creative asset submitted for approval. Workflow step: {step_id}",
                            creative_id=None,
                            workflow_step_id=step_id,
                        )
                    )
                return asset_statuses
            else:
                # Return failed statuses if workflow creation failed
                asset_statuses = []
                for asset in assets:
                    asset_statuses.append(
                        AssetStatus(
                            asset_id=asset.get("asset_id", f"failed_{len(asset_statuses)}"),
                            status="failed",
                            message="Failed to create approval workflow step",
                            creative_id=None,
                        )
                    )
                return asset_statuses

        # Automatic mode - process creatives directly
        return self.creatives_manager.add_creative_assets(media_buy_id, assets, today)

    def associate_creatives(self, line_item_ids: list[str], platform_creative_ids: list[str]) -> list[dict[str, Any]]:
        """Associate already-uploaded creatives with line items.

        Used when buyer provides creative_ids in create_media_buy, indicating
        creatives were already synced and should be associated immediately.

        Args:
            line_item_ids: GAM line item IDs
            platform_creative_ids: GAM creative IDs (already uploaded)

        Returns:
            List of association results with status
        """
        if not self.creatives_manager:
            self.log("[red]Error: Creatives manager not initialized[/red]")
            return [
                {
                    "line_item_id": lid,
                    "creative_id": cid,
                    "status": "failed",
                    "error": "Creatives manager not initialized",
                }
                for lid in line_item_ids
                for cid in platform_creative_ids
            ]

        results = []

        if not self.dry_run and self.client_manager:
            lica_service = self.client_manager.get_service("LineItemCreativeAssociationService")

        for line_item_id in line_item_ids:
            for creative_id in platform_creative_ids:
                if self.dry_run:
                    self.log(
                        f"[cyan][DRY RUN] Would associate creative {creative_id} with line item {line_item_id}[/cyan]"
                    )
                    results.append(
                        {"line_item_id": line_item_id, "creative_id": creative_id, "status": "success (dry-run)"}
                    )
                else:
                    association = {
                        "creativeId": int(creative_id),
                        "lineItemId": int(line_item_id),
                    }

                    try:
                        lica_service.createLineItemCreativeAssociations([association])
                        self.log(f"[green]✓ Associated creative {creative_id} with line item {line_item_id}[/green]")
                        results.append({"line_item_id": line_item_id, "creative_id": creative_id, "status": "success"})
                    except Exception as e:
                        error_msg = str(e)
                        self.log(
                            f"[red]✗ Failed to associate creative {creative_id} with line item {line_item_id}: {error_msg}[/red]"
                        )
                        results.append(
                            {
                                "line_item_id": line_item_id,
                                "creative_id": creative_id,
                                "status": "failed",
                                "error": error_msg,
                            }
                        )

        return results

    def check_media_buy_status(self, media_buy_id: str, today: datetime) -> CheckMediaBuyStatusResponse:
        """Check the status of a media buy in GAM."""
        # This would be implemented with appropriate manager delegation
        # For now, returning a basic implementation
        status = self.orders_manager.get_order_status(media_buy_id)

        return CheckMediaBuyStatusResponse(
            buyer_ref="", media_buy_id=media_buy_id, status=status.lower()  # Would need to be retrieved from database
        )

    def get_media_buy_delivery(
        self, media_buy_id: str, date_range: ReportingPeriod, today: datetime
    ) -> AdapterGetMediaBuyDeliveryResponse:
        """Get delivery metrics for a media buy."""
        # This would be implemented with appropriate manager delegation
        # For now, returning a basic implementation
        from src.core.schemas import DeliveryTotals

        return AdapterGetMediaBuyDeliveryResponse(
            media_buy_id=media_buy_id,
            reporting_period=date_range,
            by_package=[],
            totals=DeliveryTotals(
                impressions=0,
                spend=0,
                clicks=0,
                ctr=0.0,
                video_completions=0,
                completion_rate=0.0,
            ),
            currency="USD",
        )

    def update_media_buy(
        self,
        media_buy_id: str,
        buyer_ref: str,
        action: str,
        package_id: str | None,
        budget: int | None,
        today: datetime,
    ) -> UpdateMediaBuyResponse:
        """Update a media buy in GAM."""
        # Admin-only actions
        admin_only_actions = ["approve_order"]

        # Check if action requires admin privileges
        if action in admin_only_actions and not self._is_admin_principal():
            return UpdateMediaBuyError(
                errors=[
                    Error(code="insufficient_privileges", message="Only admin users can approve orders", details=None)
                ],
            )

        # Check if manual approval is required for media buy updates
        if self._requires_manual_approval("update_media_buy"):
            self.log("[yellow]Manual approval mode - creating workflow step for media buy update approval[/yellow]")

            # Create approval workflow step for the update action
            step_id = self.workflow_manager.create_approval_workflow_step(media_buy_id, f"update_media_buy_{action}")

            if step_id:
                # Manual approval success - no errors
                return UpdateMediaBuySuccess(
                    media_buy_id=media_buy_id,
                    buyer_ref=buyer_ref,
                    affected_packages=[],  # List of package_ids affected by update
                    implementation_date=today,
                )
            else:
                return UpdateMediaBuyError(
                    errors=[
                        Error(
                            code="workflow_creation_failed",
                            message="Failed to create approval workflow step",
                            details=None,
                        )
                    ],
                )

        # Check for activate_order action with guaranteed items
        if action == "activate_order":
            # Check if order has guaranteed line items
            has_guaranteed, item_types = self._check_order_has_guaranteed_items(media_buy_id)
            if has_guaranteed:
                self.log("[yellow]Order contains guaranteed line items - creating activation workflow step[/yellow]")

                # Create activation workflow step
                step_id = self.workflow_manager.create_activation_workflow_step(media_buy_id, [])

                if step_id:
                    # Activation workflow created - success (no errors)
                    return UpdateMediaBuySuccess(
                        media_buy_id=media_buy_id,
                        buyer_ref=buyer_ref,
                        affected_packages=[],  # type: ignore[arg-type]
                        implementation_date=today,
                        workflow_step_id=step_id,
                    )
                else:
                    return UpdateMediaBuyError(
                        errors=[
                            Error(
                                code="activation_workflow_failed",
                                message=f"Cannot auto-activate order with guaranteed line items: {', '.join(item_types)}",
                                details=None,
                            )
                        ],
                    )

        # Handle package budget updates
        if action == "update_package_budget" and package_id and budget is not None:
            from sqlalchemy import select
            from sqlalchemy.orm import attributes

            from src.core.database.database_session import get_db_session
            from src.core.database.models import MediaPackage

            # Validate budget is positive (security: prevent negative/zero budgets)
            if budget <= 0:
                self.log(f"[red]Invalid budget value: {budget} (must be positive)[/red]")
                return UpdateMediaBuyError(
                    errors=[
                        Error(
                            code="invalid_budget",
                            message=f"Budget must be positive, got {budget}",
                            details={"budget": budget},
                        )
                    ],
                )

            self.log(f"[GAM] Updating package {package_id} budget to {budget} (with delivery validation)")

            with get_db_session() as session:
                # Security: Join with MediaBuy for tenant isolation
                from src.core.database.models import MediaBuy as MediaBuyModel

                stmt = (
                    select(MediaPackage)
                    .join(MediaBuyModel, MediaPackage.media_buy_id == MediaBuyModel.media_buy_id)
                    .where(
                        MediaPackage.package_id == package_id,
                        MediaPackage.media_buy_id == media_buy_id,
                        MediaBuyModel.tenant_id == self.tenant_id,
                    )
                )
                media_package = session.scalars(stmt).first()

                if not media_package:
                    self.log(f"[red]Package {package_id} not found for media buy {media_buy_id}[/red]")
                    return UpdateMediaBuyError(
                        errors=[
                            Error(
                                code="package_not_found",
                                message=f"Package {package_id} not found for media buy {media_buy_id}",
                                details=None,
                            )
                        ],
                    )

                # Validate budget isn't less than delivery to date
                delivery_metrics = media_package.package_config.get("delivery_metrics", {})
                current_spend = float(delivery_metrics.get("spend", 0))

                if budget < current_spend:
                    self.log(
                        f"[red]Cannot set budget ${budget} below current spend ${current_spend} "
                        f"for package {package_id}[/red]"
                    )
                    return UpdateMediaBuyError(
                        errors=[
                            Error(
                                code="budget_below_delivery",
                                message=f"Cannot set budget ${budget} below current spend ${current_spend}",
                                details={
                                    "requested_budget": budget,
                                    "current_spend": current_spend,
                                    "package_id": package_id,
                                },
                            )
                        ],
                    )

                # Get platform line item ID from package config
                platform_line_item_id = media_package.package_config.get("platform_line_item_id")
                if not platform_line_item_id:
                    self.log(f"[red]Package {package_id} has no platform_line_item_id - cannot sync to GAM[/red]")
                    return UpdateMediaBuyError(
                        errors=[
                            Error(
                                code="missing_platform_id",
                                message=f"Package {package_id} has no GAM line item ID",
                                details={"package_id": package_id},
                            )
                        ],
                    )

                # Get pricing model from package config for budget calculation
                pricing_info = media_package.package_config.get("pricing", {})
                pricing_model = pricing_info.get("model", "cpm").lower()
                currency = pricing_info.get("currency", "USD")

                # Sync budget change to GAM line item
                self.log(f"[GAM] Syncing budget change to GAM line item {platform_line_item_id}")
                success = self.orders_manager.update_line_item_budget(
                    line_item_id=platform_line_item_id,
                    new_budget=float(budget),
                    pricing_model=pricing_model,
                    currency=currency,
                )

                if not success:
                    self.log(f"[red]Failed to update GAM line item {platform_line_item_id} budget[/red]")
                    return UpdateMediaBuyError(
                        errors=[
                            Error(
                                code="gam_update_failed",
                                message="Failed to update budget in Google Ad Manager",
                                details={
                                    "package_id": package_id,
                                    "line_item_id": platform_line_item_id,
                                },
                            )
                        ],
                    )

                # Update budget in package_config JSON after successful GAM sync
                media_package.package_config["budget"] = float(budget)
                # Flag the JSON field as modified so SQLAlchemy persists it
                attributes.flag_modified(media_package, "package_config")
                session.commit()
                self.log(f"✓ Updated package {package_id} budget to ${budget} in both GAM and database")

            return UpdateMediaBuySuccess(
                media_buy_id=media_buy_id,
                buyer_ref=buyer_ref,
                affected_packages=[],  # Required by AdCP spec
                implementation_date=today,
            )

        # Handle pause/resume actions
        if action in ["pause_package", "resume_package", "pause_media_buy", "resume_media_buy"]:
            from sqlalchemy import select

            from src.core.database.database_session import get_db_session
            from src.core.database.models import MediaPackage

            # Determine if we're pausing or resuming
            is_pause = action.startswith("pause_")
            new_status = "PAUSED" if is_pause else "READY"
            action_verb = "Pausing" if is_pause else "Resuming"

            # Package-level actions
            if action in ["pause_package", "resume_package"]:
                if not package_id:
                    return UpdateMediaBuyError(
                        errors=[
                            Error(
                                code="missing_package_id",
                                message=f"package_id required for {action}",
                                details={"action": action},
                            )
                        ],
                    )

                with get_db_session() as session:
                    # Security: Join with MediaBuy for tenant isolation
                    from src.core.database.models import MediaBuy as MediaBuyModel

                    stmt = (
                        select(MediaPackage)
                        .join(MediaBuyModel, MediaPackage.media_buy_id == MediaBuyModel.media_buy_id)
                        .where(
                            MediaPackage.package_id == package_id,
                            MediaPackage.media_buy_id == media_buy_id,
                            MediaBuyModel.tenant_id == self.tenant_id,
                        )
                    )
                    media_package = session.scalars(stmt).first()

                    if not media_package:
                        return UpdateMediaBuyError(
                            errors=[
                                Error(
                                    code="package_not_found",
                                    message=f"Package {package_id} not found",
                                    details={"package_id": package_id},
                                )
                            ],
                        )

                    # Get platform line item ID
                    platform_line_item_id = media_package.package_config.get("platform_line_item_id")
                    if not platform_line_item_id:
                        return UpdateMediaBuyError(
                            errors=[
                                Error(
                                    code="missing_platform_id",
                                    message=f"Package {package_id} has no GAM line item ID",
                                    details={"package_id": package_id},
                                )
                            ],
                        )

                    # Update status in GAM
                    self.log(f"[GAM] {action_verb} line item {platform_line_item_id}")
                    if is_pause:
                        success = self.orders_manager.pause_line_item(platform_line_item_id)
                    else:
                        success = self.orders_manager.resume_line_item(platform_line_item_id)

                    if not success:
                        return UpdateMediaBuyError(
                            errors=[
                                Error(
                                    code="gam_update_failed",
                                    message=f"Failed to {action_verb.lower()} line item in GAM",
                                    details={"package_id": package_id, "line_item_id": platform_line_item_id},
                                )
                            ],
                        )

                    self.log(f"✓ {action_verb} package {package_id} in GAM")

            # Media buy-level actions (pause/resume all packages)
            elif action in ["pause_media_buy", "resume_media_buy"]:
                with get_db_session() as session:
                    # Security: Join with MediaBuy for tenant isolation
                    from src.core.database.models import MediaBuy as MediaBuyModel

                    stmt = (
                        select(MediaPackage)
                        .join(MediaBuyModel, MediaPackage.media_buy_id == MediaBuyModel.media_buy_id)
                        .where(MediaPackage.media_buy_id == media_buy_id, MediaBuyModel.tenant_id == self.tenant_id)
                    )
                    packages = session.scalars(stmt).all()

                    if not packages:
                        return UpdateMediaBuyError(
                            errors=[
                                Error(
                                    code="no_packages_found",
                                    message=f"No packages found for media buy {media_buy_id}",
                                    details={"media_buy_id": media_buy_id},
                                )
                            ],
                        )

                    # Pause/resume each package's line item
                    failed_packages = []
                    for pkg in packages:
                        platform_line_item_id = pkg.package_config.get("platform_line_item_id")
                        if not platform_line_item_id:
                            failed_packages.append({"package_id": pkg.package_id, "reason": "No GAM line item ID"})
                            continue

                        self.log(f"[GAM] {action_verb} line item {platform_line_item_id} (package {pkg.package_id})")
                        if is_pause:
                            success = self.orders_manager.pause_line_item(platform_line_item_id)
                        else:
                            success = self.orders_manager.resume_line_item(platform_line_item_id)

                        if not success:
                            failed_packages.append(
                                {"package_id": pkg.package_id, "line_item_id": platform_line_item_id}
                            )

                    if failed_packages:
                        return UpdateMediaBuyError(
                            errors=[
                                Error(
                                    code="partial_failure",
                                    message=f"Failed to {action_verb.lower()} some packages in GAM",
                                    details={"failed_packages": failed_packages},
                                )
                            ],
                        )

                    self.log(f"✓ {action_verb} all {len(packages)} packages in media buy {media_buy_id}")

            return UpdateMediaBuySuccess(
                media_buy_id=media_buy_id,
                buyer_ref=buyer_ref,
                affected_packages=[],  # Required by AdCP spec
                implementation_date=today,
            )

        # Explicit failure for unsupported actions (no silent success)
        self.log(f"[red]Unsupported action '{action}' for GAM adapter[/red]")
        return UpdateMediaBuyError(
            errors=[
                Error(
                    code="unsupported_action",
                    message=f"Action '{action}' is not supported by the Google Ad Manager adapter",
                    details={
                        "action": action,
                        "supported_actions": ["approve_order", "activate_order", "update_package_budget"],
                    },
                )
            ],
        )

    def update_media_buy_performance_index(self, media_buy_id: str, package_performance: list) -> bool:
        """Update the performance index for packages in a media buy."""
        # This would be implemented with appropriate manager delegation
        self.log(f"Update performance index for media buy {media_buy_id} with {len(package_performance)} packages")
        return True

    def get_config_ui_endpoint(self) -> str | None:
        """Return the endpoint for GAM-specific configuration UI."""
        return "/adapters/gam/config"

    def register_ui_routes(self, app: Flask) -> None:
        """Register GAM-specific configuration routes."""
        from flask import jsonify, render_template, request

        @app.route("/adapters/gam/config/<tenant_id>/<product_id>", methods=["GET", "POST"])
        def gam_config_ui(tenant_id: str, product_id: str):
            """GAM adapter configuration UI."""
            if request.method == "POST":
                # Handle configuration updates
                return jsonify({"success": True})

            return render_template(
                "gam_config.html", tenant_id=tenant_id, product_id=product_id, title="Google Ad Manager Configuration"
            )

    def validate_product_config(self, config: dict[str, Any]) -> tuple[bool, str | None]:
        """Validate GAM-specific product configuration."""
        required_fields = ["network_code", "advertiser_id"]

        for field in required_fields:
            if not config.get(field):
                return False, f"Missing required field: {field}"

        return True, None

    def _create_order_statement(self, order_id: int):
        """Helper method to create a GAM statement for order filtering."""
        return self.orders_manager.create_order_statement(order_id)

    # Inventory management methods - delegated to inventory manager
    def discover_ad_units(self, parent_id=None, max_depth=10):
        """Discover ad units in the GAM network (delegated to inventory manager)."""
        return self.inventory_manager.discover_ad_units(parent_id, max_depth)

    def discover_placements(self):
        """Discover all placements in the GAM network (delegated to inventory manager)."""
        return self.inventory_manager.discover_placements()

    def discover_custom_targeting(self):
        """Discover all custom targeting keys and values (delegated to inventory manager)."""
        return self.inventory_manager.discover_custom_targeting()

    def discover_audience_segments(self):
        """Discover audience segments (delegated to inventory manager)."""
        return self.inventory_manager.discover_audience_segments()

    def sync_all_inventory(self):
        """Perform full inventory sync (delegated to inventory manager)."""
        return self.inventory_manager.sync_all_inventory()

    def build_ad_unit_tree(self):
        """Build hierarchical ad unit tree (delegated to inventory manager)."""
        return self.inventory_manager.build_ad_unit_tree()

    def get_targetable_ad_units(self, include_inactive=False, min_sizes=None):
        """Get targetable ad units (delegated to inventory manager)."""
        return self.inventory_manager.get_targetable_ad_units(include_inactive, min_sizes)

    def suggest_ad_units_for_product(self, creative_sizes, keywords=None):
        """Suggest ad units for product (delegated to inventory manager)."""
        return self.inventory_manager.suggest_ad_units_for_product(creative_sizes, keywords)

    def validate_inventory_access(self, ad_unit_ids):
        """Validate inventory access (delegated to inventory manager)."""
        return self.inventory_manager.validate_inventory_access(ad_unit_ids)

    # Sync management methods - delegated to sync manager
    def sync_inventory(self, db_session, force=False, custom_targeting_limit=1000):
        """Synchronize inventory data from GAM (delegated to sync manager)."""
        return self.sync_manager.sync_inventory(db_session, force, custom_targeting_limit)

    def sync_orders(self, db_session, force=False):
        """Synchronize orders data from GAM (delegated to sync manager)."""
        return self.sync_manager.sync_orders(db_session, force)

    def sync_full(self, db_session, force=False, custom_targeting_limit=1000):
        """Perform full synchronization (delegated to sync manager)."""
        return self.sync_manager.sync_full(db_session, force, custom_targeting_limit)

    def sync_selective(self, db_session, sync_types, custom_targeting_limit=1000, audience_segment_limit=None):
        """Perform selective synchronization (delegated to sync manager)."""
        return self.sync_manager.sync_selective(db_session, sync_types, custom_targeting_limit, audience_segment_limit)

    def get_sync_status(self, db_session, sync_id):
        """Get sync status (delegated to sync manager)."""
        return self.sync_manager.get_sync_status(db_session, sync_id)

    def get_sync_history(self, db_session, limit=10, offset=0, status_filter=None):
        """Get sync history (delegated to sync manager)."""
        return self.sync_manager.get_sync_history(db_session, limit, offset, status_filter)

    def needs_sync(self, db_session, sync_type, max_age_hours=24):
        """Check if sync is needed (delegated to sync manager)."""
        return self.sync_manager.needs_sync(db_session, sync_type, max_age_hours)
