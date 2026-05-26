"""AdCP tool implementation.

This module contains tool implementations following the MCP/A2A shared
implementation pattern from CLAUDE.md.
"""

import hashlib
import json
import logging
import re
import time
import uuid
from typing import Any

from src.core.exceptions import AdCPAuthenticationError, AdCPValidationError

logger = logging.getLogger(__name__)

_SIGNAL_SPEC_STOPWORDS = {
    "adult",
    "adults",
    "audience",
    "for",
    "interested",
    "people",
    "targeting",
    "the",
    "users",
    "with",
}

from adcp.types import PaginationResponse
from adcp.types.generated_poc.core.vendor_pricing_option import VendorPricingOption
from adcp.types.generated_poc.signals.get_signals_response import Range

from src.core.auth import get_principal_object
from src.core.database.models import TenantSignal
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    ActivateSignalResponse,
    GetSignalsRequest,
    GetSignalsResponse,
    Signal,
    SignalDeployment,
)
from src.core.signal_ids import adcp_safe_signal_id
from src.core.testing_hooks import AdCPTestContext


def _cpm_pricing_option(cpm: float, currency: str = "USD") -> list[VendorPricingOption]:
    """Build a single-element pricing_options list for a CPM signal.

    adcp 4.4.3 unified signal pricing onto the shared VendorPricingOption
    discriminated union (model='cpm' is VendorPricingOption7 = cpm + pricing_option_id).
    """
    return [
        VendorPricingOption.model_validate(
            {"pricing_option_id": f"cpm_{currency.lower()}", "model": "cpm", "cpm": cpm, "currency": currency}
        )
    ]


def _tenant_signal_to_adcp(
    ts: TenantSignal,
    *,
    ad_server: str | None,
    agent_url: str | None,
) -> Signal:
    """Translate an operator-authored ``TenantSignal`` row to the AdCP ``Signal``
    wire shape.

    ``adapter_config`` is intentionally elided — operator-authored data, not
    for storefront consumption. The storefront uses ``value_type`` /
    ``categories`` / ``range`` to render UI; activation (and any adapter-side
    resolution) happens through ``activate_signal`` / ``create_media_buy``.
    """
    range_obj: Range | None = None
    if ts.range_min is not None or ts.range_max is not None:
        range_obj = Range(min=ts.range_min, max=ts.range_max)

    # AdCP validates ``signal_id.agent_url`` as a URL; the sample signals
    # use the public salesagent host. Fall back to the same when the tenant
    # hasn't set ``public_agent_url`` so projection doesn't fail validation.
    resolved_agent_url = agent_url or "https://salesagent.adcontextprotocol.org/signals"
    wire_id = adcp_safe_signal_id(ts.signal_id)

    signal_kwargs: dict = {
        "signal_id": {
            "source": "agent",
            "agent_url": resolved_agent_url,
            "id": wire_id,
        },
        "signal_agent_segment_id": wire_id,
        "name": ts.name,
        "description": ts.description or f"{ts.name} signal",
        # Operator-declared signals are the publisher's first-party data
        # by default. Distinguishing marketplace / custom variants would
        # warrant a column on TenantSignal — keep the default simple.
        "signal_type": "owned",
        "data_provider": ts.data_provider or "publisher",
        # No coverage measurement yet — declare 100% (signal applies to
        # any inventory the operator says it applies to) until we wire
        # up coverage stats.
        "coverage_percentage": 100.0,
        "deployments": [
            SignalDeployment(
                platform=ad_server or "mock",
                is_live=True,
                type="platform",
            )
        ],
        # Publisher's own signals are zero-cost on the publisher's own
        # inventory. Operators can layer paid signals via the signals-agent
        # path when those land.
        "pricing_options": _cpm_pricing_option(0.0),
    }
    if ts.value_type:
        signal_kwargs["value_type"] = ts.value_type
    if ts.categories:
        signal_kwargs["categories"] = list(ts.categories)
    if range_obj is not None:
        signal_kwargs["range"] = range_obj
    if ts.tags:
        # AdCP Signal allows extra fields. Tags aren't part of the
        # tightest wire schema, but a storefront that supports them can
        # filter on them — and they're round-tripped via extra='allow'.
        signal_kwargs["tags"] = list(ts.tags)
    return Signal.model_validate(signal_kwargs)


def _load_tenant_signals(
    tenant_id: str,
    *,
    ad_server: str | None,
    agent_url: str | None,
) -> list[Signal]:
    """Read operator-authored ``TenantSignal`` rows for the tenant and project
    them onto the AdCP ``Signal`` shape.
    """
    from src.core.database.repositories.uow import TenantSignalUoW

    with TenantSignalUoW(tenant_id) as uow:
        assert uow.tenant_signals is not None
        rows = uow.tenant_signals.list_all()
        return [_tenant_signal_to_adcp(ts, ad_server=ad_server, agent_url=agent_url) for ts in rows]


def _dump_model(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return value
    return {}


def _signal_id_matches(requested: Any, signal: Signal) -> bool:
    requested_dump = _dump_model(requested)
    actual_dump = _dump_model(signal.signal_id)
    if not requested_dump or not actual_dump:
        return False
    if requested_dump.get("id") != actual_dump.get("id"):
        return False
    for discriminator in ("source", "agent_url", "data_provider_domain"):
        if requested_dump.get(discriminator) and requested_dump.get(discriminator) != actual_dump.get(discriminator):
            return False
    return True


def _signal_matches_destination(signal: Signal, requested_destinations: list[Any] | None) -> bool:
    if not requested_destinations:
        return True
    requested = {_dump_model(dest).get("platform") for dest in requested_destinations}
    requested.discard(None)
    if not requested:
        return True
    return any(deployment.platform in requested for deployment in signal.deployments if deployment.type == "platform")


def _signal_matches_spec(signal: Signal, signal_spec: str | None) -> bool:
    """Loose natural-language matching for discovery prompts."""
    if not signal_spec:
        return True

    spec_lower = signal_spec.lower()
    searchable_text = " ".join(
        [
            signal.name,
            signal.description,
            signal.signal_type.value,
            signal.data_provider or "",
        ]
    ).lower()
    if spec_lower in searchable_text:
        return True

    spec_tokens = _signal_search_tokens(spec_lower)
    if not spec_tokens:
        return False
    signal_tokens = _signal_search_tokens(searchable_text)
    return bool(spec_tokens & signal_tokens)


def _signal_search_tokens(value: str) -> set[str]:
    tokens = {token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 1}
    normalized = {_normalize_signal_token(token) for token in tokens}
    return {token for token in normalized if token not in _SIGNAL_SPEC_STOPWORDS}


def _normalize_signal_token(token: str) -> str:
    if token in {"autos", "automotive", "cars", "ev", "evs", "vehicle", "vehicles"}:
        return "auto"
    if token.endswith("s") and len(token) > 4:
        return token[:-1]
    return token


def _pagination_window(total: int, cursor: str | None, limit: int | None) -> tuple[int, int, PaginationResponse | None]:
    if limit is None:
        return 0, total, None
    try:
        offset = int(cursor or "0")
    except ValueError:
        offset = 0
    offset = max(offset, 0)
    next_offset = offset + limit
    has_more = next_offset < total
    return (
        offset,
        next_offset,
        PaginationResponse(has_more=has_more, cursor=str(next_offset) if has_more else None, total_count=total),
    )


def _signal_feed_version(signals: list[Signal]) -> str:
    """Return an opaque version token for the projected wholesale feed."""
    payload = [
        signal.model_dump(mode="json", exclude_none=True) if hasattr(signal, "model_dump") else signal
        for signal in signals
    ]
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return f"sigfeed_{digest[:24]}"


def _sample_signals() -> list[Signal]:
    """Return the built-in demo signals included in the public feed."""
    return [
        Signal(
            signal_id={
                "source": "agent",
                "agent_url": "https://salesagent.adcontextprotocol.org/signals",
                "id": "auto_intenders_q1_2025",
            },
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
            signal_id={
                "source": "agent",
                "agent_url": "https://salesagent.adcontextprotocol.org/signals",
                "id": "luxury_travel_enthusiasts",
            },
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
            signal_id={
                "source": "agent",
                "agent_url": "https://salesagent.adcontextprotocol.org/signals",
                "id": "sports_content",
            },
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
            signal_id={
                "source": "agent",
                "agent_url": "https://salesagent.adcontextprotocol.org/signals",
                "id": "finance_content",
            },
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
            signal_id={
                "source": "agent",
                "agent_url": "https://salesagent.adcontextprotocol.org/signals",
                "id": "urban_millennials",
            },
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
            signal_id={
                "source": "agent",
                "agent_url": "https://salesagent.adcontextprotocol.org/signals",
                "id": "pet_owners",
            },
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


def _build_signal_feed(
    tenant_id: str,
    *,
    ad_server: str | None,
    agent_url: str | None,
) -> list[Signal]:
    signals = _sample_signals()
    signals.extend(_load_tenant_signals(tenant_id, ad_server=ad_server, agent_url=agent_url))
    return signals


def _validate_signal_discovery_request(req: GetSignalsRequest) -> None:
    discovery_mode = getattr(req.discovery_mode, "value", req.discovery_mode)
    if discovery_mode not in (None, "brief", "wholesale"):
        raise AdCPValidationError(
            "get_signals supports discovery_mode='brief' and discovery_mode='wholesale'",
            details={"supported_discovery_modes": ["brief", "wholesale"]},
        )
    if discovery_mode == "wholesale" and (req.signal_spec is not None or req.signal_ids is not None):
        raise AdCPValidationError(
            "get_signals discovery_mode='wholesale' does not accept signal_spec or signal_ids",
            details={"discovery_mode": "wholesale"},
        )
    if discovery_mode != "wholesale" and (
        req.if_wholesale_feed_version is not None or req.if_pricing_version is not None
    ):
        raise AdCPValidationError(
            "get_signals version preconditions require discovery_mode='wholesale'",
            details={"discovery_mode": discovery_mode or "brief"},
        )
    if req.if_pricing_version is not None and req.if_wholesale_feed_version is None:
        raise AdCPValidationError(
            "if_pricing_version must be sent with if_wholesale_feed_version",
            details={"field": "if_pricing_version"},
        )


async def _get_signals_impl(req: GetSignalsRequest, identity: ResolvedIdentity | None = None) -> GetSignalsResponse:
    """Shared implementation for get_signals (used by both MCP and A2A).

    Args:
        req: Request containing query parameters for signal discovery
        identity: Resolved identity from transport boundary

    Returns:
        GetSignalsResponse with matching signals
    """
    _validate_signal_discovery_request(req)

    # Tenant is resolved at the transport boundary (resolve_identity_from_context)
    assert identity is not None, "identity is required for signals"
    tenant = identity.tenant
    if not tenant:
        raise AdCPAuthenticationError("No tenant context available")

    signals = []
    tenant_ad_server = tenant.get("ad_server") if isinstance(tenant, dict) else getattr(tenant, "ad_server", None)
    tenant_agent_url = (
        tenant.get("public_agent_url") if isinstance(tenant, dict) else getattr(tenant, "public_agent_url", None)
    )
    assert identity.tenant_id is not None  # resolved by transport wrapper
    feed_signals = _build_signal_feed(
        identity.tenant_id,
        ad_server=tenant_ad_server,
        agent_url=tenant_agent_url,
    )
    discovery_mode = getattr(req.discovery_mode, "value", req.discovery_mode) or "brief"
    cache_scope = "public"

    # Filter based on request parameters using AdCP-compliant fields
    for signal in feed_signals:
        if req.signal_ids and not any(_signal_id_matches(signal_id, signal) for signal_id in req.signal_ids):
            continue

        if not _signal_matches_destination(signal, req.destinations):
            continue

        # Apply signal_spec filter (natural language description matching)
        if not _signal_matches_spec(signal, req.signal_spec):
            continue

        # Apply filters if provided
        if req.filters:
            # Filter by catalog_types (equivalent to old 'type' field)
            # signal.signal_type is SignalCatalogType enum; req.filters.catalog_types
            # is also list[SignalCatalogType] — compare enum-to-enum directly.
            if req.filters.catalog_types and signal.signal_type not in req.filters.catalog_types:
                continue

            # Filter by data_providers
            if req.filters.data_providers and signal.data_provider not in req.filters.data_providers:
                continue

            # Filter by max_cpm against the first pricing option (adcp 4.4
            # replaced the singleton ``pricing`` field with ``pricing_options``).
            if req.filters.max_cpm is not None and signal.pricing_options:
                first_cpm = signal.pricing_options[0].cpm
                if first_cpm is not None and first_cpm > req.filters.max_cpm:
                    continue

            # Filter by min_coverage_percentage
            if (
                req.filters.min_coverage_percentage is not None
                and signal.coverage_percentage < req.filters.min_coverage_percentage
            ):
                continue

        signals.append(signal)

    wholesale_feed_version = _signal_feed_version(signals)
    pricing_version = wholesale_feed_version
    if (
        discovery_mode == "wholesale"
        and req.if_wholesale_feed_version == wholesale_feed_version
        and (req.if_pricing_version is None or req.if_pricing_version == pricing_version)
    ):
        return GetSignalsResponse(
            signals=None,
            errors=None,
            context=req.context,
            wholesale_feed_version=wholesale_feed_version,
            pricing_version=pricing_version,
            cache_scope=cache_scope,
            unchanged=True,
        )

    # Apply pagination first-class when provided. ``max_results`` remains
    # supported for callers using the older flat field.
    limit = req.__dict__.get("max_results")
    cursor = None
    if req.pagination is not None:
        limit = req.pagination.max_results or limit
        cursor = req.pagination.cursor
    start, end, pagination = _pagination_window(len(signals), cursor, limit)
    signals = signals[start:end]

    # Signals are already constructed as local types (extending library types),
    # so no conversion needed — pass directly to response.
    response_kwargs: dict[str, Any] = {
        "signals": signals,
        "errors": None,
        "pagination": pagination,
        "context": req.context,
    }
    if discovery_mode == "wholesale":
        response_kwargs.update(
            {
                "wholesale_feed_version": wholesale_feed_version,
                "pricing_version": pricing_version,
                "cache_scope": cache_scope,
            }
        )
    return GetSignalsResponse(**response_kwargs)


def current_signal_feed_version(tenant_id: str, *, ad_server: str | None = None, agent_url: str | None = None) -> str:
    """Return the current public signal wholesale-feed version for webhook payloads."""
    return _signal_feed_version(_build_signal_feed(tenant_id, ad_server=ad_server, agent_url=agent_url))


async def _activate_signal_impl(
    signal_agent_segment_id: str,
    campaign_id: str = None,
    media_buy_id: str = None,
    context: dict | None = None,  # payload-level context
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

    # Get the Principal object with ad server mappings
    if not principal_id:
        raise AdCPAuthenticationError("Authentication required for signal activation")
    principal = get_principal_object(principal_id, tenant_id=identity.tenant_id)

    # Apply testing hooks
    if not identity:
        raise AdCPValidationError("Context required for signal activation", recovery="terminal")
    testing_ctx = identity.testing_context if identity else AdCPTestContext()
    campaign_info = {"endpoint": "activate_signal", "signal_id": signal_agent_segment_id}
    # Note: apply_testing_hooks modifies response data dict, not called here as no response yet

    try:
        from src.core.database.repositories.uow import TenantSignalUoW
        from src.core.schemas import Error

        # Operator-declared signals (the publisher's first-party adapter
        # capability map) are immediately usable on the publisher's own
        # inventory — no external provisioning. ``activate_signal`` validates
        # the signal exists and returns a stable handle the buyer can pass
        # in ``audience_include`` / ``audience_exclude`` on
        # ``create_media_buy``. For these signals, the
        # decisioning_platform_segment_id is the signal_id itself: stable
        # across calls, no synthetic UUID drift.
        assert identity.tenant_id is not None  # resolved by transport wrapper
        with TenantSignalUoW(identity.tenant_id) as uow:
            assert uow.tenant_signals is not None
            tenant_signal = uow.tenant_signals.get_by_id(signal_agent_segment_id)
            # Snapshot the stable signal_id while the row is still
            # session-bound — accessing it after the UoW exits would trip
            # DetachedInstanceError on attribute refresh.
            resolved_signal_id = tenant_signal.signal_id if tenant_signal is not None else None

        if resolved_signal_id is not None:
            return ActivateSignalResponse(
                signal_id=signal_agent_segment_id,
                activation_details={
                    "decisioning_platform_segment_id": resolved_signal_id,
                    "estimated_activation_duration_minutes": 0.0,
                    "status": "deployed",
                },
                errors=None,
                context=context,
            )

        # Fall-through: signal not declared on tenant_signals. Today this
        # covers the hardcoded sample signals in get_signals (legacy demo
        # data) — they get the mock activation flow. A future signals-agent
        # path would call out to an external agent here; for now we preserve
        # the demo behavior so existing buyers don't break.
        if signal_agent_segment_id.startswith("premium_"):
            return ActivateSignalResponse(
                signal_id=signal_agent_segment_id,
                activation_details=None,
                errors=[
                    Error(
                        code="APPROVAL_REQUIRED",
                        message=f"Signal {signal_agent_segment_id} requires manual approval before activation",
                    )
                ],
                context=context,
            )

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

    except Exception as e:
        logger.error(f"Error activating signal {signal_agent_segment_id}: {e}")
        from src.core.schemas import Error

        return ActivateSignalResponse(
            signal_id=signal_agent_segment_id,
            activation_details=None,
            errors=[Error(code="ACTIVATION_ERROR", message=str(e))],
            context=context,
        )
