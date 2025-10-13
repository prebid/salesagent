"""
Raw AdCP tool functions without FastMCP decorators.

This module provides direct access to the core AdCP functions without
FastMCP tool decorators, specifically for use by the A2A server.
The functions here are the same implementation as in main.py but without
the @mcp.tool decorators.
"""

import logging
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context

logger = logging.getLogger(__name__)

# Database models

# Other imports
from src.core.config_loader import (
    get_current_tenant,
)

# Schema models (explicit imports to avoid collisions)
from src.core.schemas import (
    CreateMediaBuyResponse,
    GetMediaBuyDeliveryRequest,
    GetMediaBuyDeliveryResponse,
    GetProductsRequest,
    GetProductsResponse,
    GetSignalsRequest,
    GetSignalsResponse,
    ListAuthorizedPropertiesRequest,
    ListAuthorizedPropertiesResponse,
    ListCreativeFormatsRequest,
    ListCreativeFormatsResponse,
    ListCreativesResponse,
    SyncCreativesResponse,
)


def get_principal_from_context(context: Context | None) -> str | None:
    """Extract principal ID from the FastMCP context or ToolContext.

    Supports both:
    - FastMCP Context: Extracts from meta.headers['x-adcp-auth']
    - ToolContext: Uses direct principal_id attribute
    """
    if not context:
        return None

    try:
        # Check if this is a ToolContext with direct principal_id attribute
        if hasattr(context, "principal_id"):
            return context.principal_id

        # Otherwise, extract from FastMCP context metadata
        headers = context.meta.get("headers", {}) if hasattr(context, "meta") else {}
        if not headers:
            return None

        # Get the x-adcp-auth header (case-insensitive lookup)
        # HTTP headers are case-insensitive, but dict.get() is case-sensitive
        auth_token = None
        for key, value in headers.items():
            if key.lower() == "x-adcp-auth":
                auth_token = value
                break

        if not auth_token:
            return None

        # Look up principal by token
        from src.core.auth_utils import get_principal_from_token

        return get_principal_from_token(auth_token)

    except Exception as e:
        logger.warning(f"Error extracting principal from context: {e}")
        return None


async def get_products_raw(
    brief: str,
    promoted_offering: str,
    adcp_version: str = "1.0.0",
    min_exposures: int | None = None,
    filters: dict | None = None,
    strategy_id: str | None = None,
    context: Context = None,
) -> GetProductsResponse:
    """Get available products matching the brief.

    Raw function without @mcp.tool decorator for A2A server use.

    Args:
        brief: Brief description of the advertising campaign or requirements
        promoted_offering: What is being promoted/advertised (required per AdCP spec)
        adcp_version: AdCP schema version for this request (default: 1.0.0)
        min_exposures: Minimum impressions needed for measurement validity (optional)
        filters: Structured filters for product discovery (optional)
        strategy_id: Optional strategy ID for linking operations (optional)
        context: FastMCP context (automatically provided)

    Returns:
        GetProductsResponse containing matching products
    """
    # Use lazy import to avoid circular dependencies
    from src.core.main import _get_products_impl
    from src.core.schemas import ProductFilters

    # Convert filters dict to ProductFilters if provided
    filters_obj = ProductFilters(**filters) if filters else None

    # Create request object
    req = GetProductsRequest(
        brief=brief or "",
        promoted_offering=promoted_offering,
        adcp_version=adcp_version,
        min_exposures=min_exposures,
        filters=filters_obj,
        strategy_id=strategy_id,
    )

    # Call shared implementation
    return await _get_products_impl(req, context)


async def get_signals_raw(req: GetSignalsRequest, context: Context = None) -> GetSignalsResponse:
    """Optional endpoint for discovering available signals (audiences, contextual, etc.)

    Raw function without @mcp.tool decorator for A2A server use.

    Args:
        req: Request containing query parameters for signal discovery
        context: FastMCP context (automatically provided)

    Returns:
        GetSignalsResponse containing matching signals
    """
    # Use ToolContext if available
    if hasattr(context, "tenant_id") and hasattr(context, "principal_id"):
        tenant_id = context.tenant_id
        principal_id = context.principal_id
    else:
        # Legacy path - extract from FastMCP Context
        principal_id = get_principal_from_context(context)
        tenant = get_current_tenant()
        if not tenant:
            raise ToolError("No tenant configuration found", "NO_TENANT")
        tenant_id = tenant["tenant_id"]

    # Get available signals from configuration
    signals = []

    # Add some basic signals as example
    from src.core.schemas import Signal, SignalDeployment, SignalPricing

    basic_signals = [
        {
            "signal_agent_segment_id": "age_18_24",
            "name": "Age 18-24",
            "description": "Audience aged 18-24 years",
            "signal_type": "marketplace",
            "data_provider": "Internal Data",
            "coverage_percentage": 75.0,
            "deployments": [
                SignalDeployment(
                    platform="gam",
                    is_live=True,
                    scope="platform-wide",
                    decisioning_platform_segment_id="age_18_24",
                )
            ],
            "pricing": SignalPricing(cpm=1.2, currency="USD"),
        },
        {
            "signal_agent_segment_id": "sports_interest",
            "name": "Sports Interest",
            "description": "Users interested in sports content",
            "signal_type": "marketplace",
            "data_provider": "Internal Data",
            "coverage_percentage": 60.0,
            "deployments": [
                SignalDeployment(
                    platform="gam",
                    is_live=True,
                    scope="platform-wide",
                    decisioning_platform_segment_id="sports_interest",
                )
            ],
            "pricing": SignalPricing(cpm=1.1, currency="USD"),
        },
    ]

    # Filter by spec/filters if specified (AdCP v2.4)
    # For now, return all signals - proper AI-based filtering would go here
    # using req.signal_spec and req.deliver_to to intelligently match signals

    # Convert to Signal objects
    for signal_data in basic_signals:
        signal = Signal(**signal_data)
        signals.append(signal)

    return GetSignalsResponse(signals=signals)


def create_media_buy_raw(
    po_number: str,
    promoted_offering: str = None,
    buyer_ref: str = None,
    packages: list = None,
    start_time: str = None,
    end_time: str = None,
    product_ids: list[str] = None,
    total_budget: float = None,
    start_date: str = None,
    end_date: str = None,
    targeting_overlay: dict = None,
    pacing: str = "even",
    daily_budget: float = None,
    creatives: list = None,
    reporting_webhook: dict = None,
    required_axe_signals: list = None,
    enable_creative_macro: bool = False,
    strategy_id: str = None,
    push_notification_config: dict = None,
    budget: dict = None,
    context: Context = None,
) -> CreateMediaBuyResponse:
    """Create a new media buy with specified parameters.

    Raw function without @mcp.tool decorator for A2A server use.
    Delegates to the shared implementation in main.py.

    Args:
        po_number: Purchase order number
        promoted_offering: Description of advertiser and what is being promoted (optional in raw API)
        buyer_ref: Buyer reference identifier
        packages: List of media packages (optional)
        start_time: Start time (legacy parameter)
        end_time: End time (legacy parameter)
        product_ids: List of product IDs to include
        total_budget: Total budget for the media buy
        start_date: Flight start date (YYYY-MM-DD)
        end_date: Flight end date (YYYY-MM-DD)
        targeting_overlay: Additional targeting parameters
        pacing: Pacing strategy
        daily_budget: Daily budget limit
        creatives: Creative assets
        reporting_webhook: Webhook configuration for automated reporting delivery
        required_axe_signals: Required signals
        enable_creative_macro: Enable creative macro
        strategy_id: Strategy ID
        push_notification_config: Push notification config for status updates
        budget: Budget dict
        context: FastMCP context (automatically provided)

    Returns:
        CreateMediaBuyResponse with media buy details
    """
    # Import here to avoid circular imports
    from src.core.main import _create_media_buy_impl

    # Call the shared implementation
    return _create_media_buy_impl(
        promoted_offering=promoted_offering,
        po_number=po_number,
        buyer_ref=buyer_ref,
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


def sync_creatives_raw(
    creatives: list[dict],
    patch: bool = False,
    assignments: dict = None,
    delete_missing: bool = False,
    dry_run: bool = False,
    validation_mode: str = "strict",
    push_notification_config: dict = None,
    context: Context = None,
) -> SyncCreativesResponse:
    """Sync creative assets to the centralized creative library (AdCP v2.4 spec compliant endpoint).

    Delegates to the shared implementation in main.py.

    Args:
        creatives: List of creative asset objects
        patch: When true, only update provided fields (partial update). When false, full upsert.
        assignments: Bulk assignment map of creative_id to package_ids (spec-compliant)
        delete_missing: Delete creatives not in sync payload (use with caution)
        dry_run: Preview changes without applying them
        validation_mode: Validation strictness (strict or lenient)
        push_notification_config: Push notification config for status updates
        context: FastMCP context (automatically provided)

    Returns:
        SyncCreativesResponse with synced creatives and assignments
    """
    # Import here to avoid circular imports
    from src.core.main import _sync_creatives_impl

    return _sync_creatives_impl(
        creatives=creatives,
        patch=patch,
        assignments=assignments,
        delete_missing=delete_missing,
        dry_run=dry_run,
        validation_mode=validation_mode,
        push_notification_config=push_notification_config,
        context=context,
    )


def list_creatives_raw(
    media_buy_id: str = None,
    buyer_ref: str = None,
    status: str = None,
    format: str = None,
    tags: list[str] = None,
    created_after: str = None,
    created_before: str = None,
    search: str = None,
    page: int = 1,
    limit: int = 50,
    sort_by: str = "created_date",
    sort_order: str = "desc",
    context: Context = None,
) -> ListCreativesResponse:
    """List creative assets with filtering and pagination (AdCP spec endpoint).

    Delegates to the shared implementation in main.py.

    Args:
        media_buy_id: Filter by media buy ID (optional)
        buyer_ref: Filter by buyer reference (optional)
        status: Filter by status (optional)
        format: Filter by creative format (optional)
        tags: Filter by creative group tags (optional)
        created_after: Filter creatives created after this date (ISO format) (optional)
        created_before: Filter creatives created before this date (ISO format) (optional)
        search: Search in creative name or description (optional)
        page: Page number for pagination (default: 1)
        limit: Number of results per page (default: 50, max: 1000)
        sort_by: Sort field (default: created_date)
        sort_order: Sort order (default: desc)
        context: FastMCP context (automatically provided)

    Returns:
        ListCreativesResponse with filtered creative assets and pagination info
    """
    # Import here to avoid circular imports
    from src.core.main import _list_creatives_impl

    return _list_creatives_impl(
        media_buy_id=media_buy_id,
        buyer_ref=buyer_ref,
        status=status,
        format=format,
        tags=tags,
        created_after=created_after,
        created_before=created_before,
        search=search,
        page=page,
        limit=limit,
        sort_by=sort_by,
        sort_order=sort_order,
        context=context,
    )


def list_creative_formats_raw(
    req: ListCreativeFormatsRequest | None = None, context: Context = None
) -> ListCreativeFormatsResponse:
    """List all available creative formats (raw function for A2A server use).

    Delegates to shared implementation in main.py.

    Args:
        req: Optional request with filter parameters
        context: FastMCP context

    Returns:
        ListCreativeFormatsResponse with all available formats
    """
    from src.core.main import _list_creative_formats_impl

    return _list_creative_formats_impl(req, context)


def list_authorized_properties_raw(
    req: ListAuthorizedPropertiesRequest = None, context: Context = None
) -> ListAuthorizedPropertiesResponse:
    """List all properties this agent is authorized to represent (raw function for A2A server use).

    Delegates to shared implementation in main.py.
    """
    from src.core.main import _list_authorized_properties_impl

    return _list_authorized_properties_impl(req, context)


def update_media_buy_raw(
    media_buy_id: str,
    buyer_ref: str = None,
    active: bool = None,
    flight_start_date: str = None,
    flight_end_date: str = None,
    budget: float = None,
    currency: str = None,
    targeting_overlay: dict = None,
    start_time: str = None,
    end_time: str = None,
    pacing: str = None,
    daily_budget: float = None,
    packages: list = None,
    creatives: list = None,
    push_notification_config: dict = None,
    context: Context = None,
):
    """Update an existing media buy (raw function for A2A server use).

    Delegates to the shared implementation in main.py.

    Args:
        media_buy_id: The ID of the media buy to update
        buyer_ref: Update buyer reference
        active: True to activate, False to pause
        flight_start_date: Change start date
        flight_end_date: Change end date
        budget: Update total budget
        currency: Update currency
        targeting_overlay: Update targeting
        start_time: Update start datetime
        end_time: Update end datetime
        pacing: Pacing strategy
        daily_budget: Daily budget cap
        packages: Package updates
        creatives: Creative updates
        push_notification_config: Push notification config for status updates
        context: Context for authentication

    Returns:
        UpdateMediaBuyResponse
    """
    # Import here to avoid circular imports
    from src.core.main import _update_media_buy_impl

    return _update_media_buy_impl(
        media_buy_id=media_buy_id,
        buyer_ref=buyer_ref,
        active=active,
        flight_start_date=flight_start_date,
        flight_end_date=flight_end_date,
        budget=budget,
        currency=currency,
        targeting_overlay=targeting_overlay,
        start_time=start_time,
        end_time=end_time,
        pacing=pacing,
        daily_budget=daily_budget,
        packages=packages,
        creatives=creatives,
        push_notification_config=push_notification_config,
        context=context,
    )


def get_media_buy_delivery_raw(
    media_buy_ids: list[str] = None,
    buyer_refs: list[str] = None,
    status_filter: str = None,
    start_date: str = None,
    end_date: str = None,
    context: Context = None,
) -> GetMediaBuyDeliveryResponse:
    """Get delivery metrics for media buys (raw function for A2A server use).

    Args:
        media_buy_ids: Array of publisher media buy IDs to get delivery data for (optional)
        buyer_refs: Array of buyer reference IDs to get delivery data for (optional)
        status_filter: Filter by status - single status or array (optional)
        start_date: Start date for reporting period in YYYY-MM-DD format (optional)
        end_date: End date for reporting period in YYYY-MM-DD format (optional)
        context: Context for authentication

    Returns:
        GetMediaBuyDeliveryResponse with delivery metrics
    """
    # Import here to avoid circular imports
    from src.core.main import _get_media_buy_delivery_impl

    # Create request object
    req = GetMediaBuyDeliveryRequest(
        media_buy_ids=media_buy_ids,
        buyer_refs=buyer_refs,
        status_filter=status_filter,
        start_date=start_date,
        end_date=end_date,
    )

    # Call the implementation
    return _get_media_buy_delivery_impl(req, context)


def update_performance_index_raw(media_buy_id: str, performance_data: list[dict[str, Any]], context: Context = None):
    """Update performance data for a media buy (raw function for A2A server use).

    Delegates to the shared implementation in main.py.

    Args:
        media_buy_id: The ID of the media buy to update performance for
        performance_data: List of performance data objects
        context: Context for authentication

    Returns:
        UpdatePerformanceIndexResponse
    """
    # Import here to avoid circular imports
    from src.core.main import _update_performance_index_impl

    return _update_performance_index_impl(media_buy_id, performance_data, context)
