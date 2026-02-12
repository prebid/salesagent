"""AdCP tool implementation.

This module contains tool implementations following the MCP/A2A shared
implementation pattern from CLAUDE.md.
"""

import logging
import time
import uuid
from typing import Literal

from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult

from src.core.tool_context import ToolContext

logger = logging.getLogger(__name__)

from adcp.types import PlatformDeployment, Pricing, Signal, SignalCatalogType

from src.core.auth import get_principal_from_context, get_principal_object
from src.core.config_loader import get_current_tenant
from src.core.schemas import ActivateSignalResponse, GetSignalsRequest, GetSignalsResponse
from src.core.testing_hooks import get_testing_context


def _get_principal_id_from_context(context: Context | ToolContext | None) -> str | None:
    """Extract principal ID from the FastMCP Context or ToolContext."""
    if not context:
        return None
    # ToolContext has principal_id directly
    if isinstance(context, ToolContext):
        return context.principal_id
    # FastMCP Context needs extraction
    principal_id, _ = get_principal_from_context(context, require_valid_token=False)
    return principal_id


async def _get_signals_impl(req: GetSignalsRequest, context: Context | ToolContext | None = None) -> GetSignalsResponse:
    """Shared implementation for get_signals (used by both MCP and A2A).

    Args:
        req: Request containing query parameters for signal discovery
        context: FastMCP context (automatically provided)

    Returns:
        GetSignalsResponse with matching signals
    """
    _get_principal_id_from_context(context)

    # Get tenant information
    tenant = get_current_tenant()
    if not tenant:
        raise ToolError("No tenant context available")

    # Mock implementation - in production, this would query from a signal provider
    # or the ad server's available audience segments
    signals = []

    # Sample signals for demonstration using AdCP-compliant structure
    sample_signals = [
        Signal(
            signal_agent_segment_id="auto_intenders_q1_2025",
            name="Auto Intenders Q1 2025",
            description="Users actively researching new vehicles in Q1 2025",
            signal_type=SignalCatalogType.marketplace,
            data_provider="Acme Data Solutions",
            coverage_percentage=85.0,
            deployments=[
                PlatformDeployment(  # type: ignore[list-item]
                    platform="google_ad_manager",
                    is_live=True,
                    type="platform",
                )
            ],
            pricing=Pricing(cpm=3.0, currency="USD"),
        ),
        Signal(
            signal_agent_segment_id="luxury_travel_enthusiasts",
            name="Luxury Travel Enthusiasts",
            description="High-income individuals interested in premium travel experiences",
            signal_type=SignalCatalogType.marketplace,
            data_provider="Premium Audience Co",
            coverage_percentage=75.0,
            deployments=[
                PlatformDeployment(  # type: ignore[list-item]
                    platform="google_ad_manager",
                    is_live=True,
                    type="platform",
                )
            ],
            pricing=Pricing(cpm=5.0, currency="USD"),
        ),
        Signal(
            signal_agent_segment_id="sports_content",
            name="Sports Content Pages",
            description="Target ads on sports-related content",
            signal_type=SignalCatalogType.owned,
            data_provider="Publisher Sports Network",
            coverage_percentage=95.0,
            deployments=[
                PlatformDeployment(  # type: ignore[list-item]
                    platform="google_ad_manager",
                    is_live=True,
                    type="platform",
                )
            ],
            pricing=Pricing(cpm=1.5, currency="USD"),
        ),
        Signal(
            signal_agent_segment_id="finance_content",
            name="Finance & Business Content",
            description="Target ads on finance and business content",
            signal_type=SignalCatalogType.owned,
            data_provider="Financial News Corp",
            coverage_percentage=88.0,
            deployments=[
                PlatformDeployment(  # type: ignore[list-item]
                    platform="google_ad_manager",
                    is_live=True,
                    type="platform",
                )
            ],
            pricing=Pricing(cpm=2.0, currency="USD"),
        ),
        Signal(
            signal_agent_segment_id="urban_millennials",
            name="Urban Millennials",
            description="Millennials living in major metropolitan areas",
            signal_type=SignalCatalogType.marketplace,
            data_provider="Demographics Plus",
            coverage_percentage=78.0,
            deployments=[
                PlatformDeployment(  # type: ignore[list-item]
                    platform="google_ad_manager",
                    is_live=True,
                    type="platform",
                )
            ],
            pricing=Pricing(cpm=1.8, currency="USD"),
        ),
        Signal(
            signal_agent_segment_id="pet_owners",
            name="Pet Owners",
            description="Households with dogs or cats",
            signal_type=SignalCatalogType.marketplace,
            data_provider="Lifestyle Data Inc",
            coverage_percentage=92.0,
            deployments=[
                PlatformDeployment(  # type: ignore[list-item]
                    platform="google_ad_manager",
                    is_live=True,
                    type="platform",
                )
            ],
            pricing=Pricing(cpm=1.2, currency="USD"),
        ),
    ]

    # Filter based on request parameters using new AdCP-compliant fields
    for signal in sample_signals:
        # Apply signal_spec filter (natural language description matching)
        if req.signal_spec:
            spec_lower = req.signal_spec.lower()
            if (
                spec_lower not in signal.name.lower()
                and spec_lower not in signal.description.lower()
                and spec_lower not in signal.signal_type.value.lower()
            ):
                continue

        # Apply filters if provided
        if req.filters:
            # Filter by catalog_types (equivalent to old 'type' field)
            if req.filters.catalog_types and signal.signal_type not in req.filters.catalog_types:
                continue

            # Filter by data_providers
            if req.filters.data_providers and signal.data_provider not in req.filters.data_providers:
                continue

            # Filter by max_cpm (using signal's pricing.cpm)
            if req.filters.max_cpm is not None and signal.pricing and signal.pricing.cpm > req.filters.max_cpm:
                continue

            # Filter by min_coverage_percentage
            if (
                req.filters.min_coverage_percentage is not None
                and signal.coverage_percentage < req.filters.min_coverage_percentage
            ):
                continue

        signals.append(signal)

    # Apply max_results limit (AdCP-compliant field name)
    if req.max_results:
        signals = signals[: req.max_results]

    # Per AdCP PR #113 and official schema, protocol fields (message, context_id)
    # are added by the protocol layer, not the domain response.
    # Convert library Signal types to our local Signal type for type compatibility
    from src.core.schemas import Signal as LocalSignal
    from src.core.schemas import SignalDeployment, SignalPricing

    local_signals = []
    for s in signals:
        # Convert library signal_type enum to string literal
        signal_type_val = s.signal_type.value if hasattr(s.signal_type, "value") else str(s.signal_type)
        # Map to valid Literal type
        signal_type_literal: Literal["marketplace", "custom", "owned"] = (
            "marketplace" if signal_type_val == "marketplace" else "custom" if signal_type_val == "custom" else "owned"
        )

        # Convert library deployments to local SignalDeployment
        # Library Deployment is a union of Deployment1 (platform-based) and Deployment2 (agent-based)
        local_deployments = []
        for d in s.deployments or []:
            # Access attributes safely - both Deployment1 and Deployment2 have is_live
            # platform is only on Deployment1, use getattr with default
            deployment_platform = getattr(d, "platform", "unknown")
            deployment_is_live = getattr(d, "is_live", False)
            deployment_type = getattr(d, "type", "platform")
            local_deployments.append(
                SignalDeployment(
                    platform=deployment_platform,
                    account=None,
                    is_live=deployment_is_live,
                    scope="platform-wide" if deployment_type == "platform" else "account-specific",
                    decisioning_platform_segment_id=None,
                    estimated_activation_duration_minutes=None,
                )
            )

        local_signals.append(
            LocalSignal(
                signal_agent_segment_id=s.signal_agent_segment_id,
                name=s.name,
                description=s.description or "",
                signal_type=signal_type_literal,
                data_provider=s.data_provider or "",
                coverage_percentage=s.coverage_percentage or 0.0,
                deployments=local_deployments,
                pricing=SignalPricing(
                    cpm=s.pricing.cpm if s.pricing else 0.0,
                    currency=s.pricing.currency if s.pricing else "USD",
                ),
                # Optional internal fields - explicitly None to satisfy mypy
                tenant_id=None,
                created_at=None,
                updated_at=None,
                metadata=None,
            )
        )
    return GetSignalsResponse(signals=local_signals, errors=None, context=req.context)


async def get_signals(req: GetSignalsRequest, context: Context | ToolContext | None = None):
    """Optional endpoint for discovering available signals (audiences, contextual, etc.)

    MCP tool wrapper that delegates to the shared implementation.

    Args:
        req: Request containing query parameters for signal discovery
        context: FastMCP context (automatically provided)

    Returns:
        ToolResult with GetSignalsResponse data
    """
    response = await _get_signals_impl(req, context)
    return ToolResult(content=str(response), structured_content=response)


async def _activate_signal_impl(
    signal_id: str,
    campaign_id: str = None,
    media_buy_id: str = None,
    context: dict | None = None,  # payload-level context
    ctx: Context | ToolContext | None = None,
) -> ActivateSignalResponse:
    """Shared implementation for activate_signal (used by both MCP and A2A).

    Args:
        signal_id: Signal ID to activate
        campaign_id: Optional campaign ID to activate signal for
        media_buy_id: Optional media buy ID to activate signal for
        context: Application level context per adcp spec
        ctx: FastMCP context (automatically provided)

    Returns:
        ActivateSignalResponse with activation status
    """
    start_time = time.time()

    # Authentication required for signal activation
    principal_id = _get_principal_id_from_context(ctx)

    # Get tenant information
    tenant = get_current_tenant()
    if not tenant:
        raise ToolError("No tenant context available")

    # Get the Principal object with ad server mappings
    if not principal_id:
        raise ToolError("Authentication required for signal activation")
    principal = get_principal_object(principal_id)

    # Apply testing hooks
    if not ctx:
        raise ToolError("Context required for signal activation")
    testing_ctx = get_testing_context(ctx)
    campaign_info = {"endpoint": "activate_signal", "signal_id": signal_id}
    # Note: apply_testing_hooks modifies response data dict, not called here as no response yet

    try:
        # In a real implementation, this would:
        # 1. Validate the signal exists and is available
        # 2. Check if the principal has permission to activate the signal
        # 3. Communicate with the signal provider's API to activate the signal
        # 4. Update the campaign or media buy configuration to include the signal

        # Mock implementation for demonstration
        activation_success = True
        requires_approval = signal_id.startswith("premium_")  # Mock rule: premium signals need approval

        from src.core.schemas import Error

        if requires_approval:
            # Create a human task for approval - return error response
            errors = [
                Error(
                    code="APPROVAL_REQUIRED",
                    message=f"Signal {signal_id} requires manual approval before activation",
                )
            ]
            return ActivateSignalResponse(
                signal_id=signal_id,
                activation_details=None,
                errors=errors,
                context=context,
            )
        elif activation_success:
            # Success - return activation details
            decisioning_platform_segment_id = f"seg_{signal_id}_{uuid.uuid4().hex[:8]}"
            return ActivateSignalResponse(
                signal_id=signal_id,
                activation_details={
                    "decisioning_platform_segment_id": decisioning_platform_segment_id,
                    "estimated_activation_duration_minutes": 15.0,
                    "status": "processing",
                },
                errors=None,
                context=context,
            )
        else:
            # Failure
            errors = [Error(code="ACTIVATION_FAILED", message="Signal provider unavailable")]
            return ActivateSignalResponse(
                signal_id=signal_id,
                activation_details=None,
                errors=errors,
                context=context,
            )

    except Exception as e:
        logger.error(f"Error activating signal {signal_id}: {e}")
        from src.core.schemas import Error

        return ActivateSignalResponse(
            signal_id=signal_id,
            activation_details=None,
            errors=[Error(code="ACTIVATION_ERROR", message=str(e))],
            context=context,
        )


async def activate_signal(
    signal_id: str,
    campaign_id: str = None,
    media_buy_id: str = None,
    context: dict | None = None,  # payload-level context
    ctx: Context | ToolContext | None = None,
):
    """Activate a signal for use in campaigns.

    MCP tool wrapper that delegates to the shared implementation.

    Args:
        signal_id: Signal ID to activate
        campaign_id: Optional campaign ID to activate signal for
        media_buy_id: Optional media buy ID to activate signal for
        context: Application level context per adcp spec
        ctx: FastMCP context (automatically provided)

    Returns:
        ToolResult with ActivateSignalResponse data
    """
    response = await _activate_signal_impl(signal_id, campaign_id, media_buy_id, context, ctx)
    return ToolResult(content=str(response), structured_content=response)


async def get_signals_raw(req: GetSignalsRequest, ctx: Context | ToolContext | None = None) -> GetSignalsResponse:
    """Optional endpoint for discovering available signals (raw function for A2A server use).

    Delegates to the shared implementation.

    Args:
        req: Request containing query parameters for signal discovery
        context: FastMCP context (automatically provided)

    Returns:
        GetSignalsResponse containing matching signals
    """
    return await _get_signals_impl(req, ctx)


async def activate_signal_raw(
    signal_id: str,
    campaign_id: str = None,
    media_buy_id: str = None,
    context: dict | None = None,  # payload-level context
    ctx: Context | ToolContext | None = None,
) -> ActivateSignalResponse:
    """Activate a signal for use in campaigns (raw function for A2A server use).

    Delegates to the shared implementation.

    Args:
        signal_id: Signal ID to activate
        campaign_id: Optional campaign ID to activate signal for
        media_buy_id: Optional media buy ID to activate signal for
        context: Application level context per adcp spec
        ctx: FastMCP context (automatically provided)

    Returns:
        ActivateSignalResponse with activation status
    """
    return await _activate_signal_impl(signal_id, campaign_id, media_buy_id, context, ctx)
