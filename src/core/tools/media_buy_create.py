"""Create Media Buy tool implementation.

Handles media buy creation including:
- Product selection and validation
- Package configuration with pricing
- Creative assignment
- Ad server order provisioning
- Budget validation
"""

import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from pydantic import ValidationError
from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()

# Tool-specific imports
from src.core.audit_logger import get_audit_logger
from src.core.auth import (
    get_principal_object,
)
from src.core.config_loader import get_current_tenant
from src.core.context_manager import get_context_manager
from src.core.database.models import MediaBuy
from src.core.database.models import Product as ModelProduct
from src.core.helpers import get_principal_id_from_context, log_tool_activity
from src.core.schema_adapters import CreateMediaBuyResponse
from src.core.schemas import (
    CreateMediaBuyRequest,
    CreativeStatus,
    Error,
    MediaPackage,
    Package,
    TaskStatus,
)
from src.core.testing_hooks import apply_testing_hooks, get_testing_context

# Import get_product_catalog from main (after refactor)
from src.core.tools.products import get_product_catalog
from src.core.validation_helpers import format_validation_error
from src.services import activity_feed

# --- Helper Functions ---


def _validate_pricing_model_selection(
    package: Package,
    product: Any,  # ProductModel from database
    campaign_currency: str | None,
) -> dict[str, Any]:
    """Validate pricing model selection for a package against product's pricing options.

    Args:
        package: Package with optional pricing_model and bid_price
        product: Product database model with pricing_options relationship
        campaign_currency: Optional campaign-level currency

    Returns:
        Dict with validated pricing information:
        {
            "pricing_model": str,
            "rate": float | None,
            "currency": str,
            "is_fixed": bool,
            "bid_price": float | None,
        }

    Raises:
        ToolError: If pricing_model validation fails
    """
    from decimal import Decimal

    # All products must have pricing_options
    if not product.pricing_options or len(product.pricing_options) == 0:
        raise ToolError(
            "PRICING_ERROR",
            f"Product {product.product_id} has no pricing_options configured. This is a data integrity error.",
        )

    # If package doesn't specify pricing_model, use first pricing option from product
    if not package.pricing_model:
        first_option = product.pricing_options[0]
        return {
            "pricing_model": first_option.pricing_model,
            "rate": float(first_option.rate) if first_option.rate else None,
            "currency": first_option.currency or campaign_currency or "USD",
            "is_fixed": first_option.is_fixed,
            "bid_price": None,
        }

    # Find matching pricing option
    selected_option = None
    for option in product.pricing_options:
        if option.pricing_model == package.pricing_model.value:
            # If campaign currency specified, must match
            if campaign_currency and option.currency != campaign_currency:
                continue
            selected_option = option
            break

    if not selected_option:
        available_options = [f"{opt.pricing_model} ({opt.currency})" for opt in product.pricing_options]
        error_msg = f"Product {product.product_id} does not offer pricing model '{package.pricing_model}'"
        if campaign_currency:
            error_msg += f" in currency {campaign_currency}"
        error_msg += f". Available options: {', '.join(available_options)}"
        raise ToolError("PRICING_ERROR", error_msg)

    # Validate auction pricing
    if not selected_option.is_fixed:
        if not package.bid_price:
            raise ToolError(
                "PRICING_ERROR",
                f"Package requires bid_price for auction-based {package.pricing_model} pricing. "
                f"Floor price: {selected_option.price_guidance.get('floor') if selected_option.price_guidance else 'N/A'}",
            )

        floor_price = (
            Decimal(str(selected_option.price_guidance.get("floor", 0)))
            if selected_option.price_guidance
            else Decimal("0")
        )
        bid_decimal = Decimal(str(package.bid_price))

        if bid_decimal < floor_price:
            raise ToolError(
                "PRICING_ERROR",
                f"Bid price {package.bid_price} is below floor price {floor_price} for {package.pricing_model} pricing",
            )

    # Validate fixed pricing has rate
    if selected_option.is_fixed and not selected_option.rate:
        raise ToolError(
            "PRICING_ERROR",
            f"Product {product.product_id} pricing option has is_fixed=true but no rate specified",
        )

    # Validate minimum spend per package
    if selected_option.min_spend_per_package:
        package_budget = None
        if isinstance(package.budget, dict):
            package_budget = Decimal(str(package.budget.get("total", 0)))
        elif isinstance(package.budget, int | float):
            package_budget = Decimal(str(package.budget))

        if package_budget and package_budget < Decimal(str(selected_option.min_spend_per_package)):
            raise ToolError(
                "PRICING_ERROR",
                f"Package budget {package_budget} {selected_option.currency} is below minimum spend "
                f"{selected_option.min_spend_per_package} {selected_option.currency} for {package.pricing_model}",
            )

    # Return validated pricing information
    return {
        "pricing_model": selected_option.pricing_model,
        "rate": float(selected_option.rate) if selected_option.rate else None,
        "currency": selected_option.currency,
        "is_fixed": selected_option.is_fixed,
        "bid_price": float(package.bid_price) if package.bid_price else None,
    }


async def _validate_and_convert_format_ids(
    format_ids: list[Any], tenant_id: str, package_idx: int
) -> list[dict[str, str]]:
    """Validate and convert format_ids to FormatId objects with strict enforcement.

    Per AdCP spec, format_ids must be FormatId objects with {agent_url, id}.
    This function enforces:
    1. Only FormatId objects are accepted (no plain strings)
    2. agent_url must be a registered creative agent (default or tenant-specific)
    3. format_id must exist on the specified agent
    4. Format must pass validation (dimensions, asset requirements, etc.)

    Args:
        format_ids: List of format ID objects from request
        tenant_id: Tenant ID for looking up registered agents
        package_idx: Package index for error messages (0-based)

    Returns:
        List of validated FormatId dicts with {agent_url, id}

    Raises:
        ToolError: If any format_id is invalid, unregistered, or doesn't exist
    """
    from src.core.creative_agent_registry import CreativeAgentRegistry

    if not format_ids:
        return []

    registry = CreativeAgentRegistry()
    validated_format_ids = []

    # Get registered agents for this tenant
    registered_agents = registry._get_tenant_agents(tenant_id)
    # Normalize agent URLs for consistent comparison (strips /mcp, /a2a, /.well-known/*, trailing slashes)
    # This ensures all URL variations match: "https://example.com/mcp/" -> "https://example.com"
    from src.core.validation import normalize_agent_url

    registered_agent_urls = {normalize_agent_url(agent.agent_url) for agent in registered_agents}

    for idx, fmt_id in enumerate(format_ids):
        # STRICT ENFORCEMENT: Reject plain strings
        if isinstance(fmt_id, str):
            raise ToolError(
                "FORMAT_VALIDATION_ERROR",
                f"Package {package_idx + 1}, format_ids[{idx}]: Plain string format IDs are not supported. "
                f"Per AdCP spec, format_ids must be FormatId objects with {{agent_url, id}}. "
                f'Example: {{"agent_url": "https://creative.adcontextprotocol.org", "id": "{fmt_id}"}}. '
                f"Use list_creative_formats to discover available formats.",
            )

        # Extract agent_url and id from dict/object
        if isinstance(fmt_id, dict):
            agent_url = fmt_id.get("agent_url")
            format_id = fmt_id.get("id")
        elif hasattr(fmt_id, "agent_url") and hasattr(fmt_id, "id"):
            agent_url = fmt_id.agent_url
            format_id = fmt_id.id
        else:
            raise ToolError(
                "FORMAT_VALIDATION_ERROR",
                f"Package {package_idx + 1}, format_ids[{idx}]: Invalid format_id structure. "
                f"Expected FormatId object with {{agent_url, id}}, got: {type(fmt_id).__name__}",
            )

        if not agent_url or not format_id:
            raise ToolError(
                "FORMAT_VALIDATION_ERROR",
                f"Package {package_idx + 1}, format_ids[{idx}]: FormatId object missing required fields. "
                f"Both agent_url and id are required. Got: agent_url={agent_url!r}, id={format_id!r}",
            )

        # VALIDATION: Check agent is registered
        # Normalize incoming agent_url for comparison (strips /mcp, /a2a, /.well-known/*, trailing slashes)
        normalized_agent_url = normalize_agent_url(agent_url)
        if normalized_agent_url not in registered_agent_urls:
            raise ToolError(
                "FORMAT_VALIDATION_ERROR",
                f"Package {package_idx + 1}, format_ids[{idx}]: Creative agent not registered: {agent_url}. "
                f"Registered agents: {', '.join(sorted(registered_agent_urls))}. "
                f"Contact your administrator to register this creative agent.",
            )

        # VALIDATION: Verify format exists on agent
        try:
            format_obj = await registry.get_format(agent_url, format_id)
            if not format_obj:
                raise ToolError(
                    "FORMAT_VALIDATION_ERROR",
                    f"Package {package_idx + 1}, format_ids[{idx}]: Format not found on agent. "
                    f"agent_url={agent_url}, format_id={format_id!r}. "
                    f"Use list_creative_formats to discover available formats.",
                )
        except Exception as e:
            if isinstance(e, ToolError):
                raise
            logger.exception(f"Error fetching format {format_id} from {agent_url}: {e}")
            raise ToolError(
                "FORMAT_VALIDATION_ERROR",
                f"Package {package_idx + 1}, format_ids[{idx}]: Failed to verify format on agent. "
                f"agent_url={agent_url}, format_id={format_id!r}. Error: {e}",
            )

        # Format validated - add to results
        validated_format_ids.append({"agent_url": agent_url, "id": format_id})

    return validated_format_ids


from src.core.helpers.adapter_helpers import get_adapter
from src.services.setup_checklist_service import SetupIncompleteError, validate_setup_complete
from src.services.slack_notifier import get_slack_notifier


async def _create_media_buy_impl(
    buyer_ref: str,
    brand_manifest: Any,  # BrandManifest | str - REQUIRED per AdCP v2.2.0 spec
    packages: list[Any],  # REQUIRED per AdCP spec
    start_time: Any,  # datetime | Literal["asap"] | str - REQUIRED per AdCP spec
    end_time: Any,  # datetime | str - REQUIRED per AdCP spec
    budget: Any,  # Budget | float | dict - REQUIRED per AdCP spec
    po_number: str | None = None,
    product_ids: list[str] | None = None,  # Legacy format conversion
    start_date: Any | None = None,  # Legacy format conversion
    end_date: Any | None = None,  # Legacy format conversion
    total_budget: float | None = None,  # Legacy format conversion
    targeting_overlay: dict[str, Any] | None = None,
    pacing: str = "even",
    daily_budget: float | None = None,
    creatives: list[Any] | None = None,
    reporting_webhook: dict[str, Any] | None = None,
    required_axe_signals: list[str] | None = None,
    enable_creative_macro: bool = False,
    strategy_id: str | None = None,
    push_notification_config: dict[str, Any] | None = None,
    context: Context | None = None,
) -> CreateMediaBuyResponse:
    """Create a media buy with the specified parameters.

    Args:
        buyer_ref: Buyer reference for tracking (REQUIRED per AdCP spec)
        brand_manifest: Brand information manifest - inline object or URL string (REQUIRED per AdCP v2.2.0 spec)
        packages: Array of packages with products and budgets (REQUIRED)
        start_time: Campaign start time ISO 8601 or 'asap' (REQUIRED)
        end_time: Campaign end time ISO 8601 (REQUIRED)
        budget: Overall campaign budget (REQUIRED)
        po_number: Purchase order number (optional)
        product_ids: Legacy: Product IDs (converted to packages)
        start_date: Legacy: Start date (converted to start_time)
        end_date: Legacy: End date (converted to end_time)
        total_budget: Legacy: Total budget (converted to Budget object)
        targeting_overlay: Targeting overlay configuration
        pacing: Pacing strategy (even, asap, daily_budget)
        daily_budget: Daily budget limit
        creatives: Creative assets for the campaign
        reporting_webhook: Webhook configuration for automated reporting delivery
        required_axe_signals: Required targeting signals
        enable_creative_macro: Enable AXE to provide creative_macro signal
        strategy_id: Optional strategy ID for linking operations
        push_notification_config: Push notification config for status updates (MCP/A2A)
        context: FastMCP context (automatically provided)

    Returns:
        CreateMediaBuyResponse with media buy details
    """
    request_start_time = time.time()

    # DEBUG: Log incoming push_notification_config
    logger.info(f"🐛 create_media_buy called with push_notification_config={push_notification_config}")
    logger.info(f"🐛 push_notification_config type: {type(push_notification_config)}")
    if push_notification_config:
        logger.info(f"🐛 push_notification_config contents: {push_notification_config}")

    # Create request object from individual parameters (MCP-compliant)
    # Validate early with helpful error messages
    try:
        req = CreateMediaBuyRequest(
            buyer_ref=buyer_ref,
            brand_manifest=brand_manifest,
            campaign_name=None,  # Optional display name
            po_number=po_number,
            packages=packages,
            start_time=start_time,
            end_time=end_time,
            budget=budget,
            currency=None,  # Derived from product pricing_options
            product_ids=product_ids,
            start_date=start_date,
            end_date=end_date,
            total_budget=total_budget,
            targeting_overlay=targeting_overlay,
            pacing=pacing,
            daily_budget=daily_budget,
            creatives=creatives,
            reporting_webhook=reporting_webhook,
            required_axe_signals=required_axe_signals,
            enable_creative_macro=enable_creative_macro,
            strategy_id=strategy_id,
            webhook_url=None,  # Internal field, not in AdCP spec
            webhook_auth_token=None,  # Internal field, not in AdCP spec
            push_notification_config=push_notification_config,
        )
    except ValidationError as e:
        # Format validation errors with helpful context using shared helper
        raise ToolError(format_validation_error(e, context="request")) from e

    # Extract testing context first
    testing_ctx = get_testing_context(context)

    # Authentication and tenant setup
    principal_id = get_principal_id_from_context(context)
    tenant = get_current_tenant()

    # Validate setup completion (only in production, skip for testing)
    if not testing_ctx.dry_run and not testing_ctx.test_session_id:
        try:
            validate_setup_complete(tenant["tenant_id"])
        except SetupIncompleteError as e:
            # Return helpful error with missing tasks
            task_list = "\n".join(f"  - {task['name']}: {task['description']}" for task in e.missing_tasks)
            error_msg = (
                f"Setup incomplete. Please complete the following required tasks:\n\n{task_list}\n\n"
                f"Visit the setup checklist at /tenant/{tenant['tenant_id']}/setup-checklist for details."
            )
            raise ToolError(error_msg)

    # Validate principal exists BEFORE creating context (foreign key constraint)
    principal = get_principal_object(principal_id)
    if not principal:
        error_msg = f"Principal {principal_id} not found"
        # Cannot create context or workflow step without valid principal
        return CreateMediaBuyResponse(
            buyer_ref=buyer_ref or "unknown",
            errors=[Error(code="authentication_error", message=error_msg, details=None)],
        )

    # Context management and workflow step creation - create workflow step FIRST
    ctx_manager = get_context_manager()
    ctx_id = context.headers.get("x-context-id") if hasattr(context, "headers") else None
    persistent_ctx = None
    step = None

    # Create workflow step immediately for tracking all operations
    if not persistent_ctx:
        # Check if we have an existing context ID
        if ctx_id:
            persistent_ctx = ctx_manager.get_context(ctx_id)

        # Create new context if needed (principal already validated above)
        if not persistent_ctx:
            persistent_ctx = ctx_manager.create_context(tenant_id=tenant["tenant_id"], principal_id=principal_id)

    # Create workflow step for tracking this operation
    step = ctx_manager.create_workflow_step(
        context_id=persistent_ctx.context_id,
        step_type="media_buy_creation",
        owner="system",
        status="in_progress",
        tool_name="create_media_buy",
        request_data=req.model_dump(mode="json"),
    )

    # Register push notification config if provided (MCP/A2A protocol support)
    if push_notification_config:
        from src.core.database.database_session import get_db_session
        from src.core.database.models import PushNotificationConfig as DBPushNotificationConfig

        logger.info(f"[MCP/A2A] Registering push notification config from request: {push_notification_config}")

        # Extract config details
        url = push_notification_config.get("url")
        authentication = push_notification_config.get("authentication", {})

        if url:
            # Extract authentication details (A2A format: schemes + credentials)
            schemes = authentication.get("schemes", []) if authentication else []
            auth_type = schemes[0] if schemes else None
            credentials = authentication.get("credentials") if authentication else None

            # Generate config ID
            config_id = push_notification_config.get("id") or f"pnc_{uuid.uuid4().hex[:16]}"

            # Save to database
            with get_db_session() as db:
                # Check if config already exists
                from sqlalchemy import select

                stmt = select(DBPushNotificationConfig).filter_by(
                    id=config_id, tenant_id=tenant["tenant_id"], principal_id=principal_id
                )
                existing_config = db.scalars(stmt).first()

                if existing_config:
                    # Update existing
                    existing_config.url = url
                    existing_config.authentication_type = auth_type
                    existing_config.authentication_token = credentials
                    existing_config.updated_at = datetime.now(UTC)
                    existing_config.is_active = True
                else:
                    # Create new
                    new_config = DBPushNotificationConfig(
                        id=config_id,
                        tenant_id=tenant["tenant_id"],
                        principal_id=principal_id,
                        url=url,
                        authentication_type=auth_type,
                        authentication_token=credentials,
                        is_active=True,
                    )
                    db.add(new_config)

                db.commit()
                logger.info(
                    f"[MCP/A2A] Push notification config {'updated' if existing_config else 'created'}: {config_id}"
                )

    try:
        # Validate input parameters
        # 1. Budget validation
        total_budget = req.get_total_budget()
        if total_budget <= 0:
            error_msg = f"Invalid budget: {total_budget}. Budget must be positive."
            raise ValueError(error_msg)

        # 2. DateTime validation
        now = datetime.now(UTC)

        # Validate start_time
        if req.start_time is None:
            error_msg = "start_time is required"
            raise ValueError(error_msg)

        # Handle 'asap' start_time (AdCP v1.7.0)
        if req.start_time == "asap":
            start_time = now
        else:
            # Ensure start_time is timezone-aware for comparison
            # At this point, req.start_time is guaranteed to be datetime (not str)
            assert isinstance(req.start_time, datetime), "start_time must be datetime when not 'asap'"
            start_time = req.start_time  # type: datetime
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=UTC)

            if start_time < now:
                error_msg = f"Invalid start time: {req.start_time}. Start time cannot be in the past."
                raise ValueError(error_msg)

        # Validate end_time
        if req.end_time is None:
            error_msg = "end_time is required"
            raise ValueError(error_msg)

        # Ensure end_time is timezone-aware for comparison
        end_time = req.end_time
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=UTC)

        if end_time <= start_time:
            error_msg = f"Invalid time range: end time ({req.end_time}) must be after start time ({req.start_time})."
            raise ValueError(error_msg)

        # 3. Package/Product validation
        product_ids = req.get_product_ids()
        logger.info(f"DEBUG: Extracted product_ids: {product_ids}")
        logger.info(
            f"DEBUG: Request packages: {[{'package_id': p.package_id, 'product_id': p.product_id, 'buyer_ref': p.buyer_ref} for p in (req.packages or [])]}"
        )
        if not product_ids:
            error_msg = "At least one product is required."
            raise ValueError(error_msg)

        if req.packages:
            for package in req.packages:
                # Check product_id field per AdCP spec
                if not package.product_id:
                    error_msg = f"Package {package.buyer_ref} must specify product_id."
                    raise ValueError(error_msg)

            # Check for duplicate product_ids across packages
            product_id_counts: dict[str, int] = {}
            for package in req.packages:
                if package.product_id:
                    product_id_counts[package.product_id] = product_id_counts.get(package.product_id, 0) + 1

            duplicate_products = [pid for pid, count in product_id_counts.items() if count > 1]
            if duplicate_products:
                error_msg = f"Duplicate product_id(s) found in packages: {', '.join(duplicate_products)}. Each product can only be used once per media buy."
                raise ValueError(error_msg)

        # 4. Currency-specific budget validation
        from decimal import Decimal

        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import CurrencyLimit
        from src.core.database.models import Product as ProductModel

        # Get products first to determine currency from pricing options
        with get_db_session() as session:
            # Get products from database
            from sqlalchemy.orm import selectinload

            stmt = (
                select(ProductModel)
                .where(ProductModel.tenant_id == tenant["tenant_id"], ProductModel.product_id.in_(product_ids))
                .options(selectinload(ProductModel.pricing_options))
            )
            products = session.scalars(stmt).all()

            # Build product lookup map
            product_map = {p.product_id: p for p in products}

            # Get currency from product pricing options (per AdCP spec)
            request_currency = None

            # First, try to get currency from first package's pricing option
            if req.packages and len(req.packages) > 0:
                first_package = req.packages[0]
                package_product_ids = [first_package.product_id] if first_package.product_id else []

                if package_product_ids and package_product_ids[0] in product_map:
                    product = product_map[package_product_ids[0]]
                    pricing_options = product.pricing_options or []

                    # Find the pricing option matching the package's pricing_model
                    if first_package.pricing_model and pricing_options:
                        matching_option = next(
                            (po for po in pricing_options if po.pricing_model == first_package.pricing_model), None
                        )
                        if matching_option:
                            request_currency = matching_option.currency

                    # If no pricing_model specified, use first pricing option's currency
                    if not request_currency and pricing_options:
                        request_currency = pricing_options[0].currency

            # Fallback to deprecated/legacy sources
            if not request_currency and req.currency:
                # Deprecated field, but still supported for backward compatibility
                request_currency = req.currency
            elif not request_currency and req.budget and hasattr(req.budget, "currency"):
                # Legacy: Extract currency from Budget object
                request_currency = req.budget.currency
            elif (
                not request_currency
                and req.packages
                and req.packages[0].budget
                and hasattr(req.packages[0].budget, "currency")
            ):
                # Legacy: Extract currency from package budget object
                request_currency = req.packages[0].budget.currency

            # Final fallback
            if not request_currency:
                request_currency = "USD"

            # Get currency limits for this tenant and currency
            stmt = select(CurrencyLimit).where(
                CurrencyLimit.tenant_id == tenant["tenant_id"], CurrencyLimit.currency_code == request_currency
            )
            currency_limit = session.scalars(stmt).first()

            # Check if tenant supports this currency
            if not currency_limit:
                error_msg = (
                    f"Currency {request_currency} is not supported by this publisher. "
                    f"Contact the publisher to add support for this currency."
                )
                raise ValueError(error_msg)

            # NEW: Validate pricing_model selections (AdCP PR #88)
            # Store validated pricing info for later use in adapter
            package_pricing_info = {}
            if req.packages:
                for package in req.packages:
                    # Get product ID for this package (AdCP spec: single product per package)
                    package_product_ids = [package.product_id] if package.product_id else []

                    # Validate pricing for the product
                    if package_product_ids:
                        product_id = package_product_ids[0]
                        if product_id in product_map:
                            try:
                                pricing_info = _validate_pricing_model_selection(
                                    package=package,
                                    product=product_map[product_id],
                                    campaign_currency=request_currency,
                                )
                                # Store for adapter use
                                if package.package_id:
                                    package_pricing_info[package.package_id] = pricing_info
                            except ToolError as e:
                                # Re-raise pricing validation errors
                                raise ValueError(str(e))

            # Validate minimum product spend (legacy + new pricing_options)
            if currency_limit.min_package_budget:
                # Build map of product_id -> minimum spend
                product_min_spends = {}
                for product in products:
                    # Use product pricing_options min_spend if set, otherwise use currency limit minimum
                    min_spend = currency_limit.min_package_budget
                    if product.pricing_options and len(product.pricing_options) > 0:
                        # Find pricing option matching the request currency (not just first option)
                        matching_option = next(
                            (po for po in product.pricing_options if po.currency == request_currency), None
                        )
                        if matching_option and matching_option.min_spend_per_package is not None:
                            min_spend = matching_option.min_spend_per_package
                    if min_spend is not None:
                        product_min_spends[product.product_id] = Decimal(str(min_spend))

                # Validate budget against minimum spend requirements
                if product_min_spends:
                    # Check if we're in legacy mode (packages without budgets)
                    is_legacy_mode = req.packages and all(not pkg.budget for pkg in req.packages)

                    # For packages with budgets, validate each package's budget
                    if req.packages and not is_legacy_mode:
                        for package in req.packages:
                            # Skip packages without budgets (shouldn't happen in v2.4 format)
                            if not package.budget:
                                continue

                            # Extract budget amount and currency from package (each package can have different currency)
                            from src.core.schemas import extract_budget_amount

                            package_budget_amount, package_currency = extract_budget_amount(
                                package.budget, request_currency
                            )
                            package_budget = Decimal(str(package_budget_amount))

                            # Validate that package currency is supported (check currency limit exists)
                            stmt_pkg_currency_check = select(CurrencyLimit).where(
                                CurrencyLimit.tenant_id == tenant["tenant_id"],
                                CurrencyLimit.currency_code == package_currency,
                            )
                            pkg_currency_limit_check = session.scalars(stmt_pkg_currency_check).first()

                            if not pkg_currency_limit_check:
                                error_msg = (
                                    f"Currency {package_currency} is not supported by this publisher. "
                                    f"Contact the publisher to add support for this currency."
                                )
                                raise ValueError(error_msg)

                            # Get the product for this package
                            package_product_ids = [package.product_id] if package.product_id else []

                            if not package_product_ids:
                                continue

                            # Look up minimum spend for this package's currency
                            for product_id in package_product_ids:
                                if product_id not in product_map:
                                    continue

                                product = product_map[product_id]

                                # Find minimum spend for this package's currency
                                min_spend = None

                                # First check if product has pricing option for this currency
                                if product.pricing_options:
                                    matching_option = next(
                                        (po for po in product.pricing_options if po.currency == package_currency), None
                                    )
                                    if matching_option and matching_option.min_spend_per_package is not None:
                                        min_spend = Decimal(str(matching_option.min_spend_per_package))

                                # If no product override, check currency limit
                                if min_spend is None:
                                    # Get currency limit for package's currency
                                    stmt_pkg_currency = select(CurrencyLimit).where(
                                        CurrencyLimit.tenant_id == tenant["tenant_id"],
                                        CurrencyLimit.currency_code == package_currency,
                                    )
                                    pkg_currency_limit = session.scalars(stmt_pkg_currency).first()

                                    if pkg_currency_limit and pkg_currency_limit.min_package_budget:
                                        min_spend = Decimal(str(pkg_currency_limit.min_package_budget))

                                # Validate if minimum spend is set
                                if min_spend and package_budget < min_spend:
                                    error_msg = (
                                        f"Package budget ({package_budget} {package_currency}) does not meet minimum spend requirement "
                                        f"({min_spend} {package_currency}) for products in this package"
                                    )
                                    raise ValueError(error_msg)
                    else:
                        # Legacy mode: single total_budget for all products
                        applicable_min_spends = list(product_min_spends.values())
                        if applicable_min_spends:
                            required_min_spend = max(applicable_min_spends)
                            budget_decimal = Decimal(str(total_budget))

                            if budget_decimal < required_min_spend:
                                error_msg = (
                                    f"Total budget ({total_budget} {request_currency}) does not meet minimum spend requirement "
                                    f"({required_min_spend} {request_currency}) for the selected products"
                                )
                                raise ValueError(error_msg)

            # Validate maximum daily spend per package (if set)
            # This is per-package to prevent buyers from splitting large budgets across many packages
            if currency_limit.max_daily_package_spend:
                flight_days = (end_time - start_time).days
                if flight_days <= 0:
                    flight_days = 1

                # Check if we're in legacy mode (packages without budgets)
                is_legacy_mode = req.packages and all(not pkg.budget for pkg in req.packages)

                # For packages with budgets, validate each package's daily budget
                if req.packages and not is_legacy_mode:
                    for package in req.packages:
                        if not package.budget:
                            continue
                        # Extract budget amount (v1.8.0 compatible)
                        from src.core.schemas import extract_budget_amount

                        package_budget_amount, _ = extract_budget_amount(package.budget, request_currency)
                        package_budget = Decimal(str(package_budget_amount))
                        package_daily_budget = package_budget / Decimal(str(flight_days))

                        if package_daily_budget > currency_limit.max_daily_package_spend:
                            error_msg = (
                                f"Package daily budget ({package_daily_budget} {request_currency}) exceeds "
                                f"maximum daily spend per package ({currency_limit.max_daily_package_spend} {request_currency}). "
                                f"This protects against accidental large budgets and prevents GAM line item proliferation."
                            )
                            raise ValueError(error_msg)
                else:
                    # Legacy mode: validate total budget
                    daily_budget = Decimal(str(total_budget)) / Decimal(str(flight_days))

                    if daily_budget > currency_limit.max_daily_package_spend:
                        error_msg = (
                            f"Daily budget ({daily_budget} {request_currency}) exceeds maximum daily spend "
                            f"({currency_limit.max_daily_package_spend} {request_currency}). "
                            f"This protects against accidental large budgets."
                        )
                        raise ValueError(error_msg)

        # Validate targeting doesn't use managed-only dimensions
        if req.targeting_overlay:
            from src.services.targeting_capabilities import validate_overlay_targeting

            violations = validate_overlay_targeting(req.targeting_overlay.model_dump(exclude_none=True))
            if violations:
                error_msg = f"Targeting validation failed: {'; '.join(violations)}"
                raise ValueError(error_msg)

    except (ValueError, PermissionError) as e:
        # Update workflow step as failed
        ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=str(e))

        # Return error response (protocol layer will add status="failed")
        return CreateMediaBuyResponse(
            buyer_ref=buyer_ref or "unknown",
            errors=[Error(code="validation_error", message=str(e), details=None)],
        )

    # Principal already validated earlier (before context creation) to avoid foreign key errors

    try:
        # Get the appropriate adapter with testing context
        adapter = get_adapter(principal, dry_run=testing_ctx.dry_run, testing_context=testing_ctx)

        # Check if manual approval is required
        manual_approval_required = (
            adapter.manual_approval_required if hasattr(adapter, "manual_approval_required") else False
        )
        manual_approval_operations = (
            adapter.manual_approval_operations if hasattr(adapter, "manual_approval_operations") else []
        )

        # DEBUG: Log manual approval settings
        logger.info(
            f"[DEBUG] Manual approval check - required: {manual_approval_required}, "
            f"operations: {manual_approval_operations}, "
            f"adapter type: {adapter.__class__.__name__}"
        )

        # Check if auto-creation is disabled in tenant config
        auto_create_enabled = tenant.get("auto_create_media_buys", True)
        product_auto_create = True  # Will be set correctly when we get products later

        if manual_approval_required and "create_media_buy" in manual_approval_operations:
            # Update existing workflow step to require approval
            ctx_manager.update_workflow_step(
                step.step_id,
                status="requires_approval",
                add_comment={"user": "system", "comment": "Manual approval required for media buy creation"},
            )

            # Workflow step already created above - no need for separate task
            # Generate permanent media buy ID (not "pending_xxx")
            # This ID will be used whether pending or approved - only status changes
            media_buy_id = f"mb_{uuid.uuid4().hex[:12]}"

            response_msg = (
                f"Manual approval required. Workflow Step ID: {step.step_id}. Context ID: {persistent_ctx.context_id}"
            )
            ctx_manager.add_message(persistent_ctx.context_id, "assistant", response_msg)

            # Send Slack notification for manual approval requirement
            try:
                # Get principal name for notification
                principal_name = principal.name if principal else principal_id

                # Build notifier config from tenant fields
                notifier_config = {
                    "features": {
                        "slack_webhook_url": tenant.get("slack_webhook_url"),
                        "slack_audit_webhook_url": tenant.get("slack_audit_webhook_url"),
                    }
                }
                slack_notifier = get_slack_notifier(notifier_config)

                # Create notification details
                notification_details = {
                    "total_budget": total_budget,
                    "po_number": req.po_number,
                    "start_time": start_time.isoformat(),  # Resolved from 'asap' if needed
                    "end_time": end_time.isoformat(),
                    "product_ids": req.get_product_ids(),
                    "workflow_step_id": step.step_id,
                    "context_id": persistent_ctx.context_id,
                }

                slack_notifier.notify_media_buy_event(
                    event_type="approval_required",
                    media_buy_id=media_buy_id,
                    principal_name=principal_name,
                    details=notification_details,
                    tenant_name=tenant.get("name", "Unknown"),
                    tenant_id=tenant.get("tenant_id"),
                    success=True,
                )
                console.print("[green]📧 Sent manual approval notification to Slack[/green]")
            except Exception as e:
                console.print(f"[yellow]⚠️ Failed to send manual approval Slack notification: {e}[/yellow]")

            # Generate permanent package IDs (not dependent on media buy ID)
            # These IDs will be used whether the media buy is pending or approved
            pending_packages = []
            raw_request_dict = req.model_dump(mode="json")  # Serialize datetimes to JSON-compatible format

            for idx, pkg in enumerate(req.packages, 1):
                # Generate permanent package ID using product_id and index
                # Format: pkg_{product_id}_{timestamp_part}_{idx}
                import secrets

                package_id = f"pkg_{pkg.product_id}_{secrets.token_hex(4)}_{idx}"

                # Use product_id or buyer_ref for package name since Package schema doesn't have 'name'
                pkg_name = f"Package {idx}"
                if pkg.product_id:
                    pkg_name = f"{pkg.product_id} - Package {idx}"
                elif pkg.buyer_ref:
                    pkg_name = f"{pkg.buyer_ref} - Package {idx}"

                # Serialize the full package to include all fields (budget, targeting, etc.)
                # Use model_dump_internal to get complete package data
                if hasattr(pkg, "model_dump_internal"):
                    pkg_dict = pkg.model_dump_internal()
                elif hasattr(pkg, "model_dump"):
                    pkg_dict = pkg.model_dump(exclude_none=True, mode="python")
                else:
                    pkg_dict = {}

                # Build response with complete package data (matching auto-approval path)
                pending_packages.append(
                    {
                        **pkg_dict,  # Include all package fields (budget, targeting_overlay, creative_ids, etc.)
                        "package_id": package_id,
                        "name": pkg_name,
                        "buyer_ref": pkg.buyer_ref,  # Include buyer_ref from request package
                        "status": TaskStatus.INPUT_REQUIRED,  # Consistent with TaskStatus enum (requires approval)
                    }
                )

                # Update the package in raw_request with the generated package_id so UI can find it
                raw_request_dict["packages"][idx - 1]["package_id"] = package_id

            # Create media buy record in the database with permanent ID
            # Status is "pending_approval" but the ID is final
            with get_db_session() as session:
                pending_buy = MediaBuy(
                    media_buy_id=media_buy_id,
                    buyer_ref=req.buyer_ref,
                    principal_id=principal.principal_id,
                    tenant_id=tenant["tenant_id"],
                    status="pending_approval",
                    order_name=f"{req.buyer_ref} - {start_time.strftime('%Y-%m-%d')}",
                    advertiser_name=principal.name,
                    budget=total_budget,
                    currency=request_currency or "USD",  # Use request_currency from validation above
                    start_date=start_time.date(),
                    end_date=end_time.date(),
                    start_time=start_time,
                    end_time=end_time,
                    raw_request=raw_request_dict,  # Now includes package_id in each package
                    created_at=datetime.now(UTC),
                )
                session.add(pending_buy)
                session.commit()
                console.print(f"[green]✅ Created media buy {media_buy_id} with status=pending_approval[/green]")

            # Create MediaPackage records for structured querying
            # This enables the UI to display packages and creative assignments to work properly
            with get_db_session() as session:
                from src.core.database.models import MediaPackage as DBMediaPackage

                for pkg_data in pending_packages:
                    package_config = {
                        "package_id": pkg_data["package_id"],
                        "name": pkg_data.get("name"),
                        "status": pkg_data.get("status"),
                    }
                    # Add full package data from raw_request
                    for idx, req_pkg in enumerate(req.packages):
                        if idx == pending_packages.index(pkg_data):
                            package_config.update(
                                {
                                    "product_id": req_pkg.product_id,
                                    "budget": req_pkg.budget.model_dump() if req_pkg.budget else None,
                                    "targeting_overlay": (
                                        req_pkg.targeting_overlay.model_dump() if req_pkg.targeting_overlay else None
                                    ),
                                    "creative_ids": req_pkg.creative_ids,
                                    "format_ids_to_provide": req_pkg.format_ids_to_provide,
                                }
                            )
                            break

                    db_package = DBMediaPackage(
                        media_buy_id=media_buy_id,
                        package_id=pkg_data["package_id"],
                        package_config=package_config,
                    )
                    session.add(db_package)

                session.commit()
                console.print(f"[green]✅ Created {len(pending_packages)} MediaPackage records[/green]")

            # Link the workflow step to the media buy so the approval button shows in UI
            with get_db_session() as session:
                from src.core.database.models import ObjectWorkflowMapping

                mapping = ObjectWorkflowMapping(
                    object_type="media_buy", object_id=media_buy_id, step_id=step.step_id, action="create"
                )
                session.add(mapping)
                session.commit()
                console.print(f"[green]✅ Linked workflow step {step.step_id} to media buy[/green]")

            # Return success response with packages awaiting approval
            # The workflow_step_id in packages indicates approval is required
            return CreateMediaBuyResponse(
                buyer_ref=req.buyer_ref,
                media_buy_id=media_buy_id,
                creative_deadline=None,
                packages=pending_packages,
                workflow_step_id=step.step_id,  # Client can track approval via this ID
            )

        # Get products for the media buy to check product-level auto-creation settings
        catalog = get_product_catalog()
        product_ids = req.get_product_ids()
        products_in_buy = [p for p in catalog if p.product_id in product_ids]

        # Validate and auto-generate GAM implementation_config for each product if needed
        if adapter.__class__.__name__ == "GoogleAdManager":
            from src.services.gam_product_config_service import GAMProductConfigService

            gam_validator = GAMProductConfigService()
            config_errors = []

            for product in products_in_buy:
                # Auto-generate default config if missing
                if not product.implementation_config:
                    logger.info(
                        f"Product '{product.name}' ({product.product_id}) is missing GAM configuration. "
                        f"Auto-generating defaults based on product type."
                    )
                    # Generate defaults based on product delivery type and formats
                    delivery_type = product.delivery_type if hasattr(product, "delivery_type") else "non_guaranteed"
                    formats = product.formats if hasattr(product, "formats") else None
                    product.implementation_config = gam_validator.generate_default_config(
                        delivery_type=delivery_type, formats=formats
                    )

                    # Persist the auto-generated config to database
                    with get_db_session() as db_session:
                        stmt = select(ModelProduct).filter_by(product_id=product.product_id)
                        db_product = db_session.scalars(stmt).first()
                        if db_product:
                            db_product.implementation_config = product.implementation_config
                            db_session.commit()
                            logger.info(f"Saved auto-generated GAM config for product {product.product_id}")

                # Validate the config (whether existing or auto-generated)
                is_valid, error_msg = gam_validator.validate_config(product.implementation_config)
                if not is_valid:
                    config_errors.append(
                        f"Product '{product.name}' ({product.product_id}) has invalid GAM configuration: {error_msg}"
                    )

            if config_errors:
                error_detail = "GAM configuration validation failed:\n" + "\n".join(
                    f"  • {err}" for err in config_errors
                )
                ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_detail)
                return CreateMediaBuyResponse(
                    buyer_ref=req.buyer_ref,
                    errors=[{"code": "invalid_configuration", "message": err} for err in config_errors],
                )

        product_auto_create = all(
            p.implementation_config.get("auto_create_enabled", True) if p.implementation_config else True
            for p in products_in_buy
        )

        # Check if either tenant or product disables auto-creation
        if not auto_create_enabled or not product_auto_create:
            reason = "Tenant configuration" if not auto_create_enabled else "Product configuration"

            # Update existing workflow step to require approval
            ctx_manager.update_workflow_step(
                step.step_id, status="requires_approval", step_type="approval", owner="publisher"
            )

            # Workflow step already created above - no need for separate task
            # Generate permanent media buy ID (not "pending_xxx")
            media_buy_id = f"mb_{uuid.uuid4().hex[:12]}"

            response_msg = f"Media buy requires approval due to {reason.lower()}. Workflow Step ID: {step.step_id}. Context ID: {persistent_ctx.context_id}"
            ctx_manager.add_message(persistent_ctx.context_id, "assistant", response_msg)

            # Generate permanent package IDs and prepare response packages
            response_packages = []
            for idx, pkg in enumerate(req.packages, 1):
                # Generate permanent package ID
                import secrets

                package_id = f"pkg_{pkg.product_id}_{secrets.token_hex(4)}_{idx}"

                # Serialize the full package to include all fields (budget, targeting, etc.)
                # Use model_dump_internal to get complete package data
                if hasattr(pkg, "model_dump_internal"):
                    pkg_dict = pkg.model_dump_internal()
                elif hasattr(pkg, "model_dump"):
                    pkg_dict = pkg.model_dump(exclude_none=True, mode="python")
                else:
                    pkg_dict = {}

                # Build response with complete package data (matching auto-approval path)
                response_packages.append(
                    {
                        **pkg_dict,  # Include all package fields (budget, targeting_overlay, creative_ids, etc.)
                        "package_id": package_id,
                        "name": f"{pkg.product_id} - Package {idx}",
                        "buyer_ref": pkg.buyer_ref,  # Include buyer_ref from request
                        "status": TaskStatus.INPUT_REQUIRED,  # Consistent with TaskStatus enum (requires approval)
                    }
                )

            # Send Slack notification for configuration-based approval requirement
            try:
                # Get principal name for notification
                principal_name = principal.name if principal else principal_id

                # Build notifier config from tenant fields
                notifier_config = {
                    "features": {
                        "slack_webhook_url": tenant.get("slack_webhook_url"),
                        "slack_audit_webhook_url": tenant.get("slack_audit_webhook_url"),
                    }
                }
                slack_notifier = get_slack_notifier(notifier_config)

                # Create notification details including configuration reason
                notification_details = {
                    "total_budget": total_budget,
                    "po_number": req.po_number,
                    "start_time": start_time.isoformat(),  # Resolved from 'asap' if needed
                    "end_time": end_time.isoformat(),
                    "product_ids": req.get_product_ids(),
                    "approval_reason": reason,
                    "workflow_step_id": step.step_id,
                    "context_id": persistent_ctx.context_id,
                    "auto_create_enabled": auto_create_enabled,
                    "product_auto_create": product_auto_create,
                }

                slack_notifier.notify_media_buy_event(
                    event_type="config_approval_required",
                    media_buy_id=media_buy_id,
                    principal_name=principal_name,
                    details=notification_details,
                    tenant_name=tenant.get("name", "Unknown"),
                    tenant_id=tenant.get("tenant_id"),
                    success=True,
                )
                console.print(f"[green]📧 Sent {reason.lower()} approval notification to Slack[/green]")
            except Exception as e:
                console.print(f"[yellow]⚠️ Failed to send configuration approval Slack notification: {e}[/yellow]")

            return CreateMediaBuyResponse(
                buyer_ref=req.buyer_ref,
                media_buy_id=media_buy_id,
                packages=response_packages,  # Include packages with buyer_ref
                workflow_step_id=step.step_id,
            )

        # Continue with synchronized media buy creation

        # Note: products_in_buy was already calculated above for product_auto_create check
        # No need to recalculate

        # Note: Key-value pairs are NOT aggregated here anymore.
        # Each product maintains its own custom_targeting_keys in implementation_config
        # which will be applied separately to its corresponding line item in GAM.
        # The adapter (google_ad_manager.py) handles this per-product targeting at line 491-494

        # Convert products to MediaPackages
        # If req.packages provided, use format_ids from request; otherwise use product.formats
        packages = []
        for idx, product in enumerate(products_in_buy, 1):
            # Determine format_ids to use
            format_ids_to_use = []

            # Check if this product has a corresponding package in the request with format_ids
            matching_package = None
            if req.packages:
                # Find the package for this product
                for pkg in req.packages:
                    if pkg.product_id == product.product_id:
                        matching_package = pkg
                        break

                # If found and has format_ids, validate and use those
                if matching_package and hasattr(matching_package, "format_ids") and matching_package.format_ids:
                    # Validate that requested formats are supported by product
                    # Format is composite key: (agent_url, format_id) per AdCP spec
                    # Note: AdCP JSON uses "id" field, but Pydantic object uses "format_id" attribute
                    # Build set of (agent_url, format_id) tuples for comparison
                    product_format_keys = set()
                    if product.formats:
                        for fmt in product.formats:
                            agent_url = None
                            format_id = None

                            if isinstance(fmt, dict):
                                # Database JSONB: uses "id" per AdCP spec
                                agent_url = fmt.get("agent_url")
                                format_id = fmt.get("id") or fmt.get(
                                    "format_id"
                                )  # "id" is AdCP spec, "format_id" is legacy
                            elif hasattr(fmt, "agent_url") and (hasattr(fmt, "format_id") or hasattr(fmt, "id")):
                                # Pydantic object: uses "format_id" attribute (serializes to "id" in JSON)
                                agent_url = fmt.agent_url
                                format_id = getattr(fmt, "format_id", None) or getattr(fmt, "id", None)
                            elif isinstance(fmt, str):
                                # Legacy: plain string format ID (no agent_url)
                                format_id = fmt

                            if format_id:
                                # Normalize agent_url by removing trailing slash for consistent comparison
                                normalized_url = agent_url.rstrip("/") if agent_url else None
                                product_format_keys.add((normalized_url, format_id))

                    # Build set of requested format keys for comparison
                    requested_format_keys = set()
                    for fmt in matching_package.format_ids:
                        agent_url = None
                        format_id = None

                        if isinstance(fmt, dict):
                            # JSON from request: uses "id" per AdCP spec
                            agent_url = fmt.get("agent_url")
                            format_id = fmt.get("id") or fmt.get(
                                "format_id"
                            )  # "id" is AdCP spec, "format_id" is legacy
                        elif hasattr(fmt, "agent_url") and (hasattr(fmt, "format_id") or hasattr(fmt, "id")):
                            # Pydantic object: uses "format_id" attribute
                            agent_url = fmt.agent_url
                            format_id = getattr(fmt, "format_id", None) or getattr(fmt, "id", None)
                        elif isinstance(fmt, str):
                            # Legacy: plain string
                            format_id = fmt

                        if format_id:
                            # Normalize agent_url by removing trailing slash for consistent comparison
                            normalized_url = agent_url.rstrip("/") if agent_url else None
                            requested_format_keys.add((normalized_url, format_id))

                    def format_display(url: str | None, fid: str) -> str:
                        """Format a (url, id) pair for display, handling trailing slashes."""
                        if not url:
                            return fid
                        # Remove trailing slash from URL to avoid double slashes
                        clean_url = url.rstrip("/")
                        return f"{clean_url}/{fid}"

                    unsupported_formats = [
                        format_display(url, fid)
                        for url, fid in requested_format_keys
                        if (url, fid) not in product_format_keys
                    ]

                    if unsupported_formats:
                        supported_formats_str = ", ".join(
                            [format_display(url, fid) for url, fid in product_format_keys]
                        )
                        error_msg = (
                            f"Product '{product.name}' ({product.product_id}) does not support requested format(s): "
                            f"{', '.join(unsupported_formats)}. Supported formats: {supported_formats_str}"
                        )
                        raise ValueError(error_msg)

                    # Preserve original format objects for format_ids_to_use
                    format_ids_to_use = list(matching_package.format_ids)

            # Fallback to product's formats if no request format_ids
            if not format_ids_to_use:
                format_ids_to_use = list(product.formats) if product.formats else []

            # Get CPM from pricing_options
            cpm = 10.0  # Default
            if product.pricing_options and len(product.pricing_options) > 0:
                first_option = product.pricing_options[0]
                if first_option.rate:
                    cpm = float(first_option.rate)

            # Generate permanent package ID (not product_id)
            import secrets

            package_id = f"pkg_{product.product_id}_{secrets.token_hex(4)}_{idx}"

            # Get buyer_ref and budget from matching request package if available
            buyer_ref = None
            budget = None
            if matching_package:
                if hasattr(matching_package, "buyer_ref"):
                    buyer_ref = matching_package.buyer_ref
                if hasattr(matching_package, "budget"):
                    budget = matching_package.budget

            packages.append(
                MediaPackage(
                    package_id=package_id,
                    name=product.name,
                    delivery_type=product.delivery_type,
                    cpm=cpm,
                    impressions=int(total_budget / cpm * 1000),
                    format_ids=format_ids_to_use,
                    targeting_overlay=(
                        matching_package.targeting_overlay
                        if matching_package and hasattr(matching_package, "targeting_overlay")
                        else None
                    ),
                    buyer_ref=buyer_ref,
                    product_id=product.product_id,  # Include product_id
                    budget=budget,  # Include budget from request
                )
            )

        # Create the media buy using the adapter (SYNCHRONOUS operation)
        # Defensive null check: ensure start_time and end_time are set
        if not req.start_time or not req.end_time:
            error_msg = "start_time and end_time are required but were not properly set"
            ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
            return CreateMediaBuyResponse(
                buyer_ref=req.buyer_ref,
                errors=[Error(code="invalid_datetime", message=error_msg, details=None)],
            )

        # Call adapter with detailed error logging
        # Note: start_time variable already resolved from 'asap' to actual datetime if needed
        # Pass package_pricing_info for pricing model support (AdCP PR #88)
        try:
            response = adapter.create_media_buy(req, packages, start_time, end_time, package_pricing_info)
            logger.info(
                f"[DEBUG] create_media_buy: Adapter returned response with {len(response.packages) if response.packages else 0} packages"
            )
            if response.packages:
                for i, pkg in enumerate(response.packages):
                    logger.info(f"[DEBUG] create_media_buy: Response package {i} = {pkg}")
        except Exception as adapter_error:
            import traceback

            error_traceback = traceback.format_exc()
            logger.error(f"Adapter create_media_buy failed with traceback:\n{error_traceback}")
            raise

        # Note: In-memory media_buys dict removed after refactor
        # Media buys are persisted in database, not in-memory state

        # Determine initial status based on flight dates
        now = datetime.now(UTC)
        if now < start_time:
            media_buy_status = "ready"  # Scheduled to go live at flight start date
        elif now > end_time:
            media_buy_status = "completed"
        else:
            media_buy_status = "active"

        # Store the media buy in database (context_id is NULL for synchronous operations)
        tenant = get_current_tenant()
        with get_db_session() as session:
            new_media_buy = MediaBuy(
                media_buy_id=response.media_buy_id,
                tenant_id=tenant["tenant_id"],
                principal_id=principal_id,
                buyer_ref=req.buyer_ref,  # AdCP v2.4 buyer reference
                order_name=req.po_number or f"Order-{response.media_buy_id}",
                advertiser_name=principal.name,
                campaign_objective=getattr(req, "campaign_objective", ""),  # Optional field
                kpi_goal=getattr(req, "kpi_goal", ""),  # Optional field
                budget=total_budget,  # Extract total budget
                currency=request_currency,  # AdCP v2.4 currency field (resolved above)
                start_date=start_time.date(),  # Legacy field for compatibility
                end_date=end_time.date(),  # Legacy field for compatibility
                start_time=start_time,  # AdCP v2.4 datetime scheduling (resolved from 'asap' if needed)
                end_time=end_time,  # AdCP v2.4 datetime scheduling
                status=media_buy_status,
                raw_request=req.model_dump(mode="json"),
            )
            session.add(new_media_buy)
            session.commit()

        # Populate media_packages table for structured querying
        # This enables creative_assignments to work properly
        if req.packages or (response.packages and len(response.packages) > 0):
            with get_db_session() as session:
                from src.core.database.models import MediaPackage as DBMediaPackage

                # Use response packages if available (has package_ids), otherwise generate from request
                packages_to_save = response.packages if response.packages else []
                logger.info(f"[DEBUG] Saving {len(packages_to_save)} packages to media_packages table")

                for i, resp_package in enumerate(packages_to_save):
                    # Extract package_id from response - MUST be present, no fallback allowed
                    package_id = resp_package.get("package_id")
                    logger.info(f"[DEBUG] Package {i}: resp_package.get('package_id') = {package_id}")

                    if not package_id:
                        error_msg = (
                            f"Adapter did not return package_id for package {i}. This is a critical bug in the adapter."
                        )
                        logger.error(error_msg)
                        raise ValueError(error_msg)

                    logger.info(f"[DEBUG] Package {i}: Using package_id = {package_id}")

                    # Store full package config as JSON
                    package_config = {
                        "package_id": package_id,
                        "name": resp_package.get("name"),  # Include package name from adapter response
                        "product_id": resp_package.get("product_id"),
                        "budget": resp_package.get("budget"),
                        "targeting_overlay": resp_package.get("targeting_overlay"),
                        "creative_ids": resp_package.get("creative_ids"),
                        "creative_assignments": resp_package.get("creative_assignments"),
                        "format_ids_to_provide": resp_package.get("format_ids_to_provide"),
                        "status": resp_package.get("status"),
                    }

                    db_package = DBMediaPackage(
                        media_buy_id=response.media_buy_id,
                        package_id=package_id,
                        package_config=package_config,
                    )
                    session.add(db_package)

                session.commit()
                logger.info(
                    f"Saved {len(packages_to_save)} packages to media_packages table for media_buy {response.media_buy_id}"
                )

        # Handle creative_ids in packages if provided (immediate association)
        if req.packages:
            with get_db_session() as session:
                from src.core.database.models import Creative as DBCreative
                from src.core.database.models import CreativeAssignment as DBAssignment

                # Batch load all creatives upfront to avoid N+1 queries
                all_creative_ids = []
                for package in req.packages:
                    if package.creative_ids:
                        all_creative_ids.extend(package.creative_ids)

                creatives_map: dict[str, Any] = {}
                if all_creative_ids:
                    creative_stmt = select(DBCreative).where(
                        DBCreative.tenant_id == tenant["tenant_id"],
                        DBCreative.creative_id.in_(all_creative_ids),
                    )
                    creatives_list = session.scalars(creative_stmt).all()
                    creatives_map = {str(c.creative_id): c for c in creatives_list}

                    # Validate all creative IDs exist (match update_media_buy behavior)
                    found_creative_ids = set(creatives_map.keys())
                    requested_creative_ids = set(all_creative_ids)
                    missing_ids = requested_creative_ids - found_creative_ids

                    if missing_ids:
                        error_msg = f"Creative IDs not found: {', '.join(sorted(missing_ids))}"
                        logger.error(error_msg)
                        ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
                        raise ToolError("CREATIVES_NOT_FOUND", error_msg)

                for i, package in enumerate(req.packages):
                    if package.creative_ids:
                        # Use package_id from response (matches what's in media_packages table)
                        # NO FALLBACK - if adapter doesn't return package_id, fail loudly
                        package_id = None
                        if response.packages and i < len(response.packages):
                            package_id = response.packages[i].get("package_id")
                            logger.info(f"[DEBUG] Package {i}: response.packages[i] = {response.packages[i]}")
                            logger.info(f"[DEBUG] Package {i}: extracted package_id = {package_id}")

                        if not package_id:
                            error_msg = f"Cannot assign creatives: Adapter did not return package_id for package {i}"
                            logger.error(error_msg)
                            raise ValueError(error_msg)

                        # Get platform_line_item_id from response if available
                        platform_line_item_id = None
                        if response.packages and i < len(response.packages):
                            platform_line_item_id = response.packages[i].get("platform_line_item_id")

                        # Collect platform creative IDs for association
                        platform_creative_ids = []

                        for creative_id in package.creative_ids:
                            # Get creative from batch-loaded map
                            creative = creatives_map.get(creative_id)

                            # This should never happen now due to validation above
                            if not creative:
                                logger.error(f"Creative {creative_id} not in map despite validation - this is a bug")
                                continue

                            # Create database assignment (always create, even if not yet uploaded to GAM)
                            # Get platform_creative_id from creative.data JSON
                            platform_creative_id = creative.data.get("platform_creative_id") if creative.data else None
                            if platform_creative_id:
                                # Add to association list for immediate GAM association
                                platform_creative_ids.append(platform_creative_id)
                            else:
                                logger.warning(
                                    f"Creative {creative_id} has not been uploaded to ad server yet (no platform_creative_id). "
                                    f"Database assignment will be created, but GAM association will be skipped until creative is uploaded."
                                )

                            # Create database assignment
                            assignment_id = f"assign_{uuid.uuid4().hex[:12]}"
                            assignment = DBAssignment(
                                assignment_id=assignment_id,
                                tenant_id=tenant["tenant_id"],
                                media_buy_id=response.media_buy_id,
                                package_id=package_id,
                                creative_id=creative_id,
                            )
                            session.add(assignment)

                        session.commit()

                        # Associate creatives with line items in ad server immediately
                        if platform_line_item_id and platform_creative_ids:
                            try:
                                console.print(
                                    f"[cyan]Associating {len(platform_creative_ids)} pre-synced creatives with line item {platform_line_item_id}[/cyan]"
                                )
                                association_results = adapter.associate_creatives(
                                    [platform_line_item_id], platform_creative_ids
                                )

                                # Log results
                                for result in association_results:
                                    if result.get("status") == "success":
                                        console.print(
                                            f"  ✓ Associated creative {result['creative_id']} with line item {result['line_item_id']}"
                                        )
                                    else:
                                        console.print(
                                            f"  ✗ Failed to associate creative {result['creative_id']}: {result.get('error', 'Unknown error')}"
                                        )
                            except Exception as e:
                                logger.error(
                                    f"Failed to associate creatives with line item {platform_line_item_id}: {e}"
                                )
                        elif platform_creative_ids:
                            logger.warning(
                                f"Package {package_id} has {len(platform_creative_ids)} creatives but no platform_line_item_id from adapter. "
                                f"Creatives will need to be associated via sync_creatives."
                            )

        # Handle creatives if provided
        if req.creatives:
            # Convert Creative objects to format expected by adapter
            assets = []
            for creative in req.creatives:
                try:
                    asset = _convert_creative_to_adapter_asset(creative, req.product_ids)
                    assets.append(asset)
                except Exception as e:
                    console.print(f"[red]Error converting creative {creative.creative_id}: {e}[/red]")
                    # Add a failed status for this creative
                    creative_statuses[creative.creative_id] = CreativeStatus(
                        creative_id=creative.creative_id, status="rejected", detail=f"Conversion error: {str(e)}"
                    )
                    continue
            statuses = adapter.add_creative_assets(response.media_buy_id, assets, datetime.now())
            for status in statuses:
                creative_statuses[status.creative_id] = CreativeStatus(
                    creative_id=status.creative_id,
                    status="approved" if status.status == "approved" else "pending_review",
                    detail="Creative submitted to ad server",
                )

        # Build packages list for response (AdCP v2.4 format)
        # Use packages from adapter response (has package_ids) merged with request package fields
        response_packages = []

        # Get adapter response packages (have package_ids)
        adapter_packages = response.packages if response.packages else []

        for i, package in enumerate(req.packages):
            # Start with adapter response package (has package_id)
            if i < len(adapter_packages):
                # Get package_id and other fields from adapter response
                response_package_dict = (
                    adapter_packages[i] if isinstance(adapter_packages[i], dict) else adapter_packages[i].model_dump()
                )
            else:
                # Fallback if adapter didn't return enough packages
                logger.warning(f"Adapter returned fewer packages than request. Using request package {i}")
                response_package_dict = {}

            # CRITICAL: Save package_id from adapter response BEFORE merge
            adapter_package_id = response_package_dict.get("package_id")
            logger.info(f"[DEBUG] Package {i}: adapter_package_id from response = {adapter_package_id}")

            # Serialize the request package to get fields like buyer_ref, format_ids
            if hasattr(package, "model_dump_internal"):
                request_package_dict = package.model_dump_internal()
            elif hasattr(package, "model_dump"):
                request_package_dict = package.model_dump(exclude_none=True, mode="python")
            else:
                request_package_dict = package if isinstance(package, dict) else {}

            # Merge: Start with adapter response (has package_id), overlay request fields
            package_dict = {**response_package_dict, **request_package_dict}

            # CRITICAL: Restore package_id from adapter (merge may have overwritten it with None from request)
            if adapter_package_id:
                package_dict["package_id"] = adapter_package_id
                logger.info(f"[DEBUG] Package {i}: Forced package_id = {adapter_package_id}")
            else:
                # NO FALLBACK - adapter MUST return package_id
                error_msg = f"Adapter did not return package_id for package {i}. Cannot build response."
                logger.error(error_msg)
                raise ValueError(error_msg)

            # Validate and convert format_ids (request field) to format_ids_to_provide (response field)
            if "format_ids" in package_dict and package_dict["format_ids"]:
                validated_format_ids = await _validate_and_convert_format_ids(
                    package_dict["format_ids"], tenant["tenant_id"], i
                )
                package_dict["format_ids_to_provide"] = validated_format_ids
                # Remove format_ids from response
                del package_dict["format_ids"]

            # Determine package status
            package_status = TaskStatus.WORKING
            if package.creative_ids and len(package.creative_ids) > 0:
                package_status = TaskStatus.COMPLETED
            elif hasattr(package, "format_ids_to_provide") and package.format_ids_to_provide:
                package_status = TaskStatus.WORKING

            # Add status
            package_dict["status"] = package_status
            response_packages.append(package_dict)

        # Ensure buyer_ref is set (defensive check)
        buyer_ref_value = req.buyer_ref if req.buyer_ref else buyer_ref
        if not buyer_ref_value:
            logger.error(f"🚨 buyer_ref is missing! req.buyer_ref={req.buyer_ref}, buyer_ref={buyer_ref}")
            buyer_ref_value = f"missing-{response.media_buy_id}"

        # Create AdCP response (protocol fields like status are added by ProtocolEnvelope wrapper)
        adcp_response = CreateMediaBuyResponse(
            buyer_ref=buyer_ref_value,
            media_buy_id=response.media_buy_id,
            packages=response_packages,
            creative_deadline=response.creative_deadline,
        )

        # Log activity
        # Activity logging imported at module level

        log_tool_activity(context, "create_media_buy", request_start_time)

        # Also log specific media buy activity
        try:
            principal_name = "Unknown"
            with get_db_session() as session:
                stmt = select(ModelPrincipal).filter_by(principal_id=principal_id, tenant_id=tenant["tenant_id"])
                principal_db = session.scalars(stmt).first()
                if principal_db:
                    principal_name = principal_db.name

            # Calculate duration using new datetime fields (resolved from 'asap' if needed)
            duration_days = (end_time - start_time).days + 1

            activity_feed.log_media_buy(
                tenant_id=tenant["tenant_id"],
                principal_name=principal_name,
                media_buy_id=response.media_buy_id,
                budget=total_budget,  # Extract total budget
                duration_days=duration_days,
                action="created",
            )
        except Exception as e:
            # Activity feed logging is non-critical, but we should log the failure
            logger.warning(f"Failed to log media buy creation to activity feed: {e}")

        # Apply testing hooks to response with campaign information (resolved from 'asap' if needed)
        campaign_info = {"start_date": start_time, "end_date": end_time, "total_budget": total_budget}

        response_data = (
            adcp_response.model_dump_internal()
            if hasattr(adcp_response, "model_dump_internal")
            else adcp_response.model_dump()
        )

        response_data = apply_testing_hooks(response_data, testing_ctx, "create_media_buy", campaign_info)

        # Reconstruct response from modified data
        # Filter out testing hook fields that aren't part of CreateMediaBuyResponse schema
        # Domain fields only (status/adcp_version are protocol fields, added by ProtocolEnvelope)
        valid_fields = {
            "buyer_ref",
            "media_buy_id",
            "creative_deadline",
            "packages",
            "errors",
            "workflow_step_id",
        }
        filtered_data = {k: v for k, v in response_data.items() if k in valid_fields}

        # Ensure required fields are present (validator compliance)
        if "status" not in filtered_data:
            filtered_data["status"] = "completed"
        if "buyer_ref" not in filtered_data:
            filtered_data["buyer_ref"] = buyer_ref_value

        # Use explicit fields for validator (instead of **kwargs)
        modified_response = CreateMediaBuyResponse(
            buyer_ref=filtered_data["buyer_ref"],
            media_buy_id=filtered_data.get("media_buy_id"),
            creative_deadline=filtered_data.get("creative_deadline"),
            packages=filtered_data.get("packages"),
            errors=filtered_data.get("errors"),
        )

        # Mark workflow step as completed on success
        ctx_manager.update_workflow_step(step.step_id, status="completed")

        # Send Slack notification for successful media buy creation
        try:
            # Get principal name for notification (reuse from activity logging above)
            principal_name = "Unknown"
            with get_db_session() as session:
                stmt = select(ModelPrincipal).filter_by(principal_id=principal_id, tenant_id=tenant["tenant_id"])
                principal_db = session.scalars(stmt).first()
                if principal_db:
                    principal_name = principal_db.name

            # Build notifier config from tenant fields
            notifier_config = {
                "features": {
                    "slack_webhook_url": tenant.get("slack_webhook_url"),
                    "slack_audit_webhook_url": tenant.get("slack_audit_webhook_url"),
                }
            }
            slack_notifier = get_slack_notifier(notifier_config)

            # Create success notification details
            success_details = {
                "total_budget": total_budget,
                "po_number": req.po_number,
                "start_time": start_time.isoformat(),  # Resolved from 'asap' if needed
                "end_time": end_time.isoformat(),
                "product_ids": req.get_product_ids(),
                "duration_days": (end_time - start_time).days + 1,
                "packages_count": len(response_packages) if response_packages else 0,
                "creatives_count": len(req.creatives) if req.creatives else 0,
                "workflow_step_id": step.step_id,
            }

            slack_notifier.notify_media_buy_event(
                event_type="created",
                media_buy_id=response.media_buy_id,
                principal_name=principal_name,
                details=success_details,
                tenant_name=tenant.get("name", "Unknown"),
                tenant_id=tenant.get("tenant_id"),
                success=True,
            )

            console.print(f"[green]🎉 Sent success notification to Slack for media buy {response.media_buy_id}[/green]")
        except Exception as e:
            console.print(f"[yellow]⚠️ Failed to send success Slack notification: {e}[/yellow]")

        # Log to audit logs for business activity feed
        audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
        audit_logger.log_operation(
            operation="create_media_buy",
            principal_name=principal_name,
            principal_id=principal_id or "anonymous",
            adapter_id="mcp_server",
            success=True,
            details={
                "media_buy_id": response.media_buy_id,
                "total_budget": total_budget,
                "po_number": req.po_number,
                "duration_days": (end_time - start_time).days + 1,  # Resolved from 'asap' if needed
                "product_count": len(req.get_product_ids()),
                "packages_count": len(response_packages) if response_packages else 0,
            },
        )

        return modified_response

    except Exception as e:
        # Update workflow step as failed on any error during execution
        if step:
            ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=str(e))

        # Send Slack notification for failed media buy creation
        try:
            # Get principal name for notification
            principal_name = "Unknown"
            if principal:
                principal_name = principal.name

            # Build notifier config from tenant fields
            notifier_config = {
                "features": {
                    "slack_webhook_url": tenant.get("slack_webhook_url"),
                    "slack_audit_webhook_url": tenant.get("slack_audit_webhook_url"),
                }
            }
            slack_notifier = get_slack_notifier(notifier_config)

            # Create failure notification details
            failure_details = {
                "total_budget": total_budget if "total_budget" in locals() else 0,
                "po_number": req.po_number,
                "start_time": (
                    start_time.isoformat() if "start_time" in locals() else None
                ),  # Resolved from 'asap' if needed
                "end_time": end_time.isoformat() if "end_time" in locals() else None,
                "product_ids": req.get_product_ids(),
                "error_message": str(e),
                "workflow_step_id": step.step_id if step else "unknown",
            }

            slack_notifier.notify_media_buy_event(
                event_type="failed",
                media_buy_id=None,
                principal_name=principal_name,
                details=failure_details,
                tenant_name=tenant.get("name", "Unknown"),
                tenant_id=tenant.get("tenant_id"),
                success=False,
                error_message=str(e),
            )

            console.print(f"[red]❌ Sent failure notification to Slack: {str(e)}[/red]")
        except Exception as notify_error:
            console.print(f"[yellow]⚠️ Failed to send failure Slack notification: {notify_error}[/yellow]")

        # Log to audit logs for failed operation
        try:
            audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
            audit_logger.log_operation(
                operation="create_media_buy",
                principal_name=principal.name if principal else "unknown",
                principal_id=principal_id or "anonymous",
                adapter_id="mcp_server",
                success=False,
                error_message=str(e),
                details={
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "po_number": req.po_number if req else None,
                    "total_budget": total_budget if "total_budget" in locals() else 0,
                },
            )
        except Exception as audit_error:
            # Audit logging failure is non-critical, but we should log it
            logger.warning(f"Failed to log failed media buy creation to audit: {audit_error}")

        raise ToolError("MEDIA_BUY_CREATION_ERROR", f"Failed to create media buy: {str(e)}")


async def create_media_buy(
    buyer_ref: str,
    brand_manifest: Any,  # BrandManifest | str - REQUIRED per AdCP v2.2.0 spec
    packages: list[Any],  # REQUIRED per AdCP spec
    start_time: Any,  # datetime | Literal["asap"] | str - REQUIRED per AdCP spec
    end_time: Any,  # datetime | str - REQUIRED per AdCP spec
    budget: Any,  # Budget | float | dict - REQUIRED per AdCP spec
    po_number: str | None = None,
    product_ids: list[str] | None = None,  # Legacy format conversion
    start_date: Any | None = None,  # Legacy format conversion
    end_date: Any | None = None,  # Legacy format conversion
    total_budget: float | None = None,  # Legacy format conversion
    targeting_overlay: dict[str, Any] | None = None,
    pacing: str = "even",
    daily_budget: float | None = None,
    creatives: list[Any] | None = None,
    reporting_webhook: dict[str, Any] | None = None,
    required_axe_signals: list[str] | None = None,
    enable_creative_macro: bool = False,
    strategy_id: str | None = None,
    push_notification_config: dict[str, Any] | None = None,
    webhook_url: str | None = None,
    context: Context | None = None,
) -> CreateMediaBuyResponse:
    """Create a media buy with the specified parameters.

    MCP tool wrapper that delegates to the shared implementation.

    Args:
        buyer_ref: Buyer reference for tracking (REQUIRED per AdCP spec)
        brand_manifest: Brand information manifest - inline object or URL string (REQUIRED per AdCP v2.2.0 spec)
        packages: Array of packages with products and budgets (REQUIRED)
        start_time: Campaign start time ISO 8601 or 'asap' (REQUIRED)
        end_time: Campaign end time ISO 8601 (REQUIRED)
        budget: Overall campaign budget (REQUIRED)
        po_number: Purchase order number (optional)
        product_ids: Legacy: Product IDs (converted to packages)
        start_date: Legacy: Start date (converted to start_time)
        end_date: Legacy: End date (converted to end_time)
        total_budget: Legacy: Total budget (converted to Budget object)
        targeting_overlay: Targeting overlay configuration
        pacing: Pacing strategy (even, asap, daily_budget)
        daily_budget: Daily_budget limit
        creatives: Creative assets for the campaign
        reporting_webhook: Webhook configuration for automated reporting delivery
        required_axe_signals: Required targeting signals
        enable_creative_macro: Enable AXE to provide creative_macro signal
        strategy_id: Optional strategy ID for linking operations
        push_notification_config: Push notification config dict with url, authentication (AdCP spec)
        context: FastMCP context (automatically provided)

    Returns:
        CreateMediaBuyResponse with media buy details
    """
    return await _create_media_buy_impl(
        buyer_ref=buyer_ref,
        brand_manifest=brand_manifest,
        po_number=po_number,
        packages=packages,
        start_time=start_time,
        end_time=end_time,
        budget=budget,
        product_ids=product_ids,
        start_date=start_date,
        end_date=end_date,
        total_budget=total_budget,
        targeting_overlay=targeting_overlay,
        pacing=pacing,
        daily_budget=daily_budget,
        creatives=creatives,
        reporting_webhook=reporting_webhook,
        required_axe_signals=required_axe_signals,
        enable_creative_macro=enable_creative_macro,
        strategy_id=strategy_id,
        push_notification_config=push_notification_config,
        context=context,
    )


async def create_media_buy_raw(
    buyer_ref: str,
    brand_manifest: Any,  # BrandManifest | str - REQUIRED per AdCP v2.2.0 spec
    packages: list[Any],  # REQUIRED per AdCP spec
    start_time: Any,  # datetime | Literal["asap"] | str - REQUIRED per AdCP spec
    end_time: Any,  # datetime | str - REQUIRED per AdCP spec
    budget: Any,  # Budget | float | dict - REQUIRED per AdCP spec
    po_number: str | None = None,
    product_ids: list[str] | None = None,  # Legacy format conversion
    total_budget: float | None = None,  # Legacy format conversion
    start_date: Any | None = None,  # Legacy format conversion
    end_date: Any | None = None,  # Legacy format conversion
    targeting_overlay: dict[str, Any] | None = None,
    pacing: str = "even",
    daily_budget: float | None = None,
    creatives: list[Any] | None = None,
    reporting_webhook: dict[str, Any] | None = None,
    required_axe_signals: list[str] | None = None,
    enable_creative_macro: bool = False,
    strategy_id: str | None = None,
    push_notification_config: dict[str, Any] | None = None,
    context: Context | None = None,
):
    """Create a new media buy with specified parameters (raw function for A2A server use).

    Delegates to the shared implementation.

    Args:
        buyer_ref: Buyer reference identifier (REQUIRED per AdCP spec)
        brand_manifest: Brand information manifest - inline object or URL string (REQUIRED per AdCP v2.2.0 spec)
        packages: List of media packages (REQUIRED)
        start_time: Campaign start time ISO 8601 or 'asap' (REQUIRED)
        end_time: Campaign end time ISO 8601 (REQUIRED)
        budget: Overall campaign budget (REQUIRED)
        po_number: Purchase order number (optional)
        product_ids: Legacy: Product IDs (converted to packages)
        total_budget: Legacy: Total budget (converted to Budget object)
        start_date: Legacy: Start date (converted to start_time)
        end_date: Legacy: End date (converted to end_time)
        targeting_overlay: Additional targeting parameters
        pacing: Pacing strategy
        daily_budget: Daily budget limit
        creatives: Creative assets
        reporting_webhook: Webhook configuration for automated reporting delivery
        required_axe_signals: Required signals
        enable_creative_macro: Enable creative macro
        strategy_id: Strategy ID
        push_notification_config: Push notification config for status updates
        context: FastMCP context (automatically provided)

    Returns:
        CreateMediaBuyResponse with media buy details
    """
    return await _create_media_buy_impl(
        buyer_ref=buyer_ref,
        brand_manifest=brand_manifest,
        po_number=po_number,
        packages=packages,
        start_time=start_time,
        end_time=end_time,
        budget=budget,
        product_ids=product_ids,
        start_date=start_date,
        end_date=end_date,
        total_budget=total_budget,
        targeting_overlay=targeting_overlay,
        pacing=pacing,
        daily_budget=daily_budget,
        creatives=creatives,
        reporting_webhook=reporting_webhook,
        required_axe_signals=required_axe_signals,
        enable_creative_macro=enable_creative_macro,
        strategy_id=strategy_id,
        push_notification_config=push_notification_config,
        context=context,
    )


# Unified update tools
