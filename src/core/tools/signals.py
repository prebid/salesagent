"""AdCP tool implementation.

This module contains tool implementations following the MCP/A2A shared
implementation pattern from CLAUDE.md.
"""

import logging
import time
import uuid

from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult

from src.core.exceptions import AdCPAuthenticationError, AdCPValidationError
from src.core.tool_context import ToolContext

logger = logging.getLogger(__name__)

from adcp.types.generated_poc.core.account_ref import AccountReference
from adcp.types.generated_poc.core.context import ContextObject
from adcp.types.generated_poc.core.destination import Destination
from adcp.types.generated_poc.core.ext import ExtensionObject
from adcp.types.generated_poc.core.pagination_request import PaginationRequest
from adcp.types.generated_poc.core.signal_id import SignalId, SignalId18
from adcp.types.generated_poc.core.vendor_pricing_option import VendorPricingOption
from adcp.types.generated_poc.signals.get_signals_request import Country
from adcp.types.generated_poc.signals.get_signals_response import Range

from src.core.auth import get_principal_object
from src.core.database.models import TenantSignal
from src.core.database.repositories.tenant_signal import TenantSignalRepository
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    ActivateSignalResponse,
    GetSignalsRequest,
    GetSignalsResponse,
    Signal,
    SignalDeployment,
    SignalFilters,
)
from src.core.testing_hooks import AdCPTestContext

_SAMPLE_SIGNAL_IDS: frozenset[str] = frozenset(
    {
        "auto_intenders_q1_2025",
        "luxury_travel_enthusiasts",
        "sports_content",
        "finance_content",
        "urban_millennials",
        "pet_owners",
    }
)


def _canonical_signal_id(signal_id: str) -> str:
    return signal_id.replace(".", "_")


def _agent_signal_id(segment_id: str) -> SignalId:
    """Build a SignalId for an agent-native signal."""
    return SignalId(SignalId18(id=segment_id, source="agent", agent_url="https://salesagent.adcontextprotocol.org"))


def _cpm_pricing_option(cpm: float, currency: str = "USD") -> list[VendorPricingOption]:
    """Build a single-element pricing_options list for a CPM signal."""
    return [
        VendorPricingOption.model_validate(
            {"pricing_option_id": f"cpm_{currency.lower()}", "model": "cpm", "cpm": cpm, "currency": currency}
        )
    ]


def _tenant_signal_to_adcp(
    tenant_signal: TenantSignal,
    *,
    ad_server: str | None,
    agent_url: str | None,
) -> Signal:
    """Translate an operator-authored TenantSignal row to AdCP Signal."""
    range_obj: Range | None = None
    if tenant_signal.range_min is not None or tenant_signal.range_max is not None:
        range_obj = Range(min=tenant_signal.range_min, max=tenant_signal.range_max)

    wire_id = _canonical_signal_id(tenant_signal.signal_id)
    signal_kwargs: dict = {
        "signal_id": {
            "source": "agent",
            "agent_url": agent_url or "https://salesagent.adcontextprotocol.org/signals",
            "id": wire_id,
        },
        "signal_agent_segment_id": wire_id,
        "name": tenant_signal.name,
        "description": tenant_signal.description or "",
        "signal_type": "owned",
        "data_provider": tenant_signal.data_provider or "publisher",
        "coverage_percentage": 100.0,
        "deployments": [
            SignalDeployment(
                platform=ad_server or "mock",
                is_live=True,
                type="platform",
            )
        ],
        "pricing_options": _cpm_pricing_option(0.0),
    }
    if tenant_signal.value_type:
        signal_kwargs["value_type"] = tenant_signal.value_type
    if tenant_signal.categories:
        signal_kwargs["categories"] = list(tenant_signal.categories)
    if range_obj is not None:
        signal_kwargs["range"] = range_obj
    if tenant_signal.tags:
        signal_kwargs["tags"] = list(tenant_signal.tags)
    return Signal.model_validate(signal_kwargs)


def _load_tenant_signals(
    tenant_id: str,
    *,
    ad_server: str | None,
    agent_url: str | None,
) -> list[Signal]:
    rows = TenantSignalRepository.list_for_tenant(tenant_id)
    return [_tenant_signal_to_adcp(row, ad_server=ad_server, agent_url=agent_url) for row in rows]


async def _get_signals_impl(req: GetSignalsRequest, identity: ResolvedIdentity | None = None) -> GetSignalsResponse:
    """Shared implementation for get_signals (used by both MCP and A2A).

    Args:
        req: Request containing query parameters for signal discovery
        identity: Resolved identity from transport boundary

    Returns:
        GetSignalsResponse with matching signals
    """
    # Principal ID available via identity.principal_id if needed
    _ = identity.principal_id if identity else None

    # Tenant is resolved at the transport boundary (resolve_identity_from_context)
    assert identity is not None, "identity is required for signals"
    tenant = identity.tenant
    if not tenant:
        raise AdCPAuthenticationError("No tenant context available")
    tenant_id = identity.tenant_id
    if tenant_id is None:
        raise AdCPAuthenticationError("No tenant context available")

    # Sample signals for demonstration using local types (extend AdCP library types)
    sample_signals = [
        Signal(
            signal_id=_agent_signal_id("auto_intenders_q1_2025"),
            signal_agent_segment_id="auto_intenders_q1_2025",
            name="Auto Intenders Q1 2025",
            description="Users actively researching new vehicles in Q1 2025",
            signal_type="marketplace",
            data_provider="Acme Data Solutions",
            coverage_percentage=85.0,
            deployments=[SignalDeployment(platform="google_ad_manager", is_live=True, type="platform")],
            pricing_options=_cpm_pricing_option(3.0),
        ),
        Signal(
            signal_id=_agent_signal_id("luxury_travel_enthusiasts"),
            signal_agent_segment_id="luxury_travel_enthusiasts",
            name="Luxury Travel Enthusiasts",
            description="High-income individuals interested in premium travel experiences",
            signal_type="marketplace",
            data_provider="Premium Audience Co",
            coverage_percentage=75.0,
            deployments=[SignalDeployment(platform="google_ad_manager", is_live=True, type="platform")],
            pricing_options=_cpm_pricing_option(5.0),
        ),
        Signal(
            signal_id=_agent_signal_id("sports_content"),
            signal_agent_segment_id="sports_content",
            name="Sports Content Pages",
            description="Target ads on sports-related content",
            signal_type="owned",
            data_provider="Publisher Sports Network",
            coverage_percentage=95.0,
            deployments=[SignalDeployment(platform="google_ad_manager", is_live=True, type="platform")],
            pricing_options=_cpm_pricing_option(1.5),
        ),
        Signal(
            signal_id=_agent_signal_id("finance_content"),
            signal_agent_segment_id="finance_content",
            name="Finance & Business Content",
            description="Target ads on finance and business content",
            signal_type="owned",
            data_provider="Financial News Corp",
            coverage_percentage=88.0,
            deployments=[SignalDeployment(platform="google_ad_manager", is_live=True, type="platform")],
            pricing_options=_cpm_pricing_option(2.0),
        ),
        Signal(
            signal_id=_agent_signal_id("urban_millennials"),
            signal_agent_segment_id="urban_millennials",
            name="Urban Millennials",
            description="Millennials living in major metropolitan areas",
            signal_type="marketplace",
            data_provider="Demographics Plus",
            coverage_percentage=78.0,
            deployments=[SignalDeployment(platform="google_ad_manager", is_live=True, type="platform")],
            pricing_options=_cpm_pricing_option(1.8),
        ),
        Signal(
            signal_id=_agent_signal_id("pet_owners"),
            signal_agent_segment_id="pet_owners",
            name="Pet Owners",
            description="Households with dogs or cats",
            signal_type="marketplace",
            data_provider="Lifestyle Data Inc",
            coverage_percentage=92.0,
            deployments=[SignalDeployment(platform="google_ad_manager", is_live=True, type="platform")],
            pricing_options=_cpm_pricing_option(1.2),
        ),
    ]
    tenant_signals = _load_tenant_signals(
        tenant_id,
        ad_server=tenant.get("ad_server") if isinstance(tenant, dict) else None,
        agent_url=tenant.get("public_agent_url") if isinstance(tenant, dict) else None,
    )

    # Filter based on request parameters using AdCP-compliant fields
    signals = []
    for signal in [*tenant_signals, *sample_signals]:
        # Apply signal_spec filter (natural language description matching)
        if req.signal_spec:
            spec_lower = req.signal_spec.lower()
            if (
                spec_lower not in signal.name.lower()
                and spec_lower not in signal.description.lower()
                and spec_lower not in signal.signal_type.lower()
            ):
                continue

        # Apply filters if provided
        if req.filters:
            # Filter by catalog_types (equivalent to old 'type' field)
            # catalog_types contains SignalCatalogType enums; compare via .value
            if req.filters.catalog_types and signal.signal_type not in [ct.value for ct in req.filters.catalog_types]:
                continue

            # Filter by data_providers
            if req.filters.data_providers and signal.data_provider not in req.filters.data_providers:
                continue

            # Filter by max_cpm (using signal's first pricing option CPM)
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

    return GetSignalsResponse(signals=signals, errors=None, context=req.context)


async def get_signals(
    adcp_major_version: int | None = None,
    account: AccountReference | None = None,
    signal_spec: str | None = None,
    signal_ids: list[SignalId] | None = None,
    destinations: list[Destination] | None = None,
    countries: list[Country] | None = None,
    filters: SignalFilters | None = None,
    max_results: int | None = None,
    pagination: PaginationRequest | None = None,
    context: ContextObject | None = None,
    ext: ExtensionObject | None = None,
    ctx: Context | ToolContext | None = None,
):
    """Optional endpoint for discovering available signals (audiences, contextual, etc.)

    MCP tool wrapper that delegates to the shared implementation.

    Args:
        adcp_major_version: Requested AdCP major version.
        account: Optional account reference.
        signal_spec: Natural-language signal discovery request.
        signal_ids: Optional explicit signal IDs to retrieve.
        destinations: Optional deployment destinations.
        countries: Optional country filters.
        filters: Optional structured signal filters.
        max_results: Optional maximum result count.
        pagination: Optional pagination request.
        context: Application level context per AdCP spec.
        ext: Extension object per AdCP spec.
        ctx: FastMCP context (automatically provided).

    Returns:
        ToolResult with GetSignalsResponse data
    """
    from src.core.transport_helpers import resolve_identity_from_context

    req = GetSignalsRequest(
        adcp_major_version=adcp_major_version,
        account=account,
        signal_spec=signal_spec,
        signal_ids=signal_ids,
        destinations=destinations,
        countries=countries,
        filters=filters,
        max_results=max_results,
        pagination=pagination,
        context=context,
        ext=ext,
    )
    identity = resolve_identity_from_context(ctx, require_valid_token=False)
    response = await _get_signals_impl(req, identity)
    return ToolResult(content=str(response), structured_content=response.model_dump(mode="json"))


async def _activate_signal_impl(
    signal_agent_segment_id: str,
    campaign_id: str = None,
    media_buy_id: str = None,
    context: ContextObject | dict | None = None,  # payload-level context
    identity: ResolvedIdentity | None = None,
) -> ActivateSignalResponse:
    """Shared implementation for activate_signal (used by both MCP and A2A).

    Args:
        signal_agent_segment_id: Universal signal identifier to activate
        campaign_id: Optional campaign ID to activate signal for
        media_buy_id: Optional media buy ID to activate signal for
        context: Application level context per adcp spec
        identity: Resolved identity from transport boundary

    Returns:
        ActivateSignalResponse with activation status
    """
    start_time = time.time()

    # Authentication required for signal activation
    principal_id = identity.principal_id if identity else None

    # Tenant is resolved at the transport boundary (resolve_identity_from_context)
    if not identity or not identity.tenant:
        raise AdCPAuthenticationError("No tenant context available")
    tenant_id = identity.tenant_id
    if tenant_id is None:
        raise AdCPAuthenticationError("No tenant context available")

    # Get the Principal object with ad server mappings
    if not principal_id:
        raise AdCPAuthenticationError("Authentication required for signal activation")
    principal = get_principal_object(principal_id, tenant_id=identity.tenant_id)

    tenant_signal_ids = {
        _canonical_signal_id(signal_id) for signal_id in TenantSignalRepository.list_signal_ids_for_tenant(tenant_id)
    }
    if signal_agent_segment_id not in tenant_signal_ids and signal_agent_segment_id not in _SAMPLE_SIGNAL_IDS:
        raise AdCPValidationError(
            f"Signal {signal_agent_segment_id!r} is not declared for tenant {tenant_id!r}",
            recovery="terminal",
        )

    # Apply testing hooks
    if not identity:
        raise AdCPValidationError("Context required for signal activation", recovery="terminal")
    testing_ctx = identity.testing_context if identity else AdCPTestContext()
    campaign_info = {"endpoint": "activate_signal", "signal_id": signal_agent_segment_id}
    # Note: apply_testing_hooks modifies response data dict, not called here as no response yet

    try:
        # In a real implementation, this would:
        # 1. Validate the signal exists and is available
        # 2. Check if the principal has permission to activate the signal
        # 3. Communicate with the signal provider's API to activate the signal
        # 4. Update the campaign or media buy configuration to include the signal

        # Mock implementation for demonstration
        activation_success = True
        requires_approval = signal_agent_segment_id.startswith("premium_")

        from src.core.schemas import Error

        if requires_approval:
            # Create a human task for approval - return error response
            errors = [
                Error(
                    code="VALIDATION_ERROR",
                    message=f"Signal {signal_agent_segment_id} requires manual approval before activation",
                )
            ]
            return ActivateSignalResponse(
                signal_id=signal_agent_segment_id,
                activation_details=None,
                errors=errors,
                context=context,
            )
        elif activation_success:
            # Success - return activation details
            decisioning_platform_segment_id = f"seg_{signal_agent_segment_id}_{uuid.uuid4().hex[:8]}"
            return ActivateSignalResponse(
                signal_id=signal_agent_segment_id,
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
            errors = [Error(code="SERVICE_UNAVAILABLE", message="Signal provider unavailable")]
            return ActivateSignalResponse(
                signal_id=signal_agent_segment_id,
                activation_details=None,
                errors=errors,
                context=context,
            )

    except Exception as e:
        logger.error(f"Error activating signal {signal_agent_segment_id}: {e}")
        from src.core.schemas import Error

        return ActivateSignalResponse(
            signal_id=signal_agent_segment_id,
            activation_details=None,
            errors=[Error(code="SERVICE_UNAVAILABLE", message=str(e))],
            context=context,
        )


async def activate_signal(
    signal_agent_segment_id: str,
    campaign_id: str = None,
    media_buy_id: str = None,
    context: dict | None = None,  # payload-level context
    ctx: Context | ToolContext | None = None,
):
    """Activate a signal for use in campaigns.

    MCP tool wrapper that delegates to the shared implementation.

    Args:
        signal_agent_segment_id: Universal signal identifier to activate
        campaign_id: Optional campaign ID to activate signal for
        media_buy_id: Optional media buy ID to activate signal for
        context: Application level context per adcp spec
        ctx: FastMCP context (automatically provided)

    Returns:
        ToolResult with ActivateSignalResponse data
    """
    from src.core.transport_helpers import resolve_identity_from_context

    identity = resolve_identity_from_context(ctx)
    response = await _activate_signal_impl(signal_agent_segment_id, campaign_id, media_buy_id, context, identity)
    return ToolResult(content=str(response), structured_content=response)


async def get_signals_raw(
    req: GetSignalsRequest,
    ctx: Context | ToolContext | None = None,
    identity: ResolvedIdentity | None = None,
) -> GetSignalsResponse:
    """Optional endpoint for discovering available signals (raw function for A2A server use).

    Delegates to the shared implementation.

    Args:
        req: Request containing query parameters for signal discovery
        ctx: FastMCP context (automatically provided)
        identity: Pre-resolved identity (preferred over ctx)

    Returns:
        GetSignalsResponse containing matching signals
    """
    if identity is None:
        from src.core.transport_helpers import resolve_identity_from_context

        identity = resolve_identity_from_context(ctx, require_valid_token=False)
    return await _get_signals_impl(req, identity)


async def activate_signal_raw(
    signal_agent_segment_id: str,
    campaign_id: str = None,
    media_buy_id: str = None,
    context: ContextObject | None = None,  # payload-level context
    ctx: Context | ToolContext | None = None,
    identity: ResolvedIdentity | None = None,
) -> ActivateSignalResponse:
    """Activate a signal for use in campaigns (raw function for A2A server use).

    Delegates to the shared implementation.

    Args:
        signal_agent_segment_id: Universal signal identifier to activate
        campaign_id: Optional campaign ID to activate signal for
        media_buy_id: Optional media buy ID to activate signal for
        context: Application level context per adcp spec
        ctx: FastMCP context (automatically provided)
        identity: Pre-resolved identity (preferred over ctx)

    Returns:
        ActivateSignalResponse with activation status
    """
    if identity is None:
        from src.core.transport_helpers import resolve_identity_from_context

        identity = resolve_identity_from_context(ctx)
    return await _activate_signal_impl(signal_agent_segment_id, campaign_id, media_buy_id, context, identity)
