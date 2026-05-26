"""Signals protocol wiring on the modern ``core/`` platform."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from adcp.decisioning import create_adcp_server_from_platform
from adcp.server.mcp_tools import DISCOVERY_TOOLS

from core.main import AUTH_OPTIONAL_TOOLS
from core.platforms.gam import GamPlatform
from core.platforms.mock import MockSellerPlatform
from src.core.exceptions import AdCPValidationError
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import GetSignalsRequest
from src.core.tenant_context import TenantContext
from src.core.tools.signals import _get_signals_impl, current_signal_feed_version


def _advertised_tools(platform) -> frozenset[str]:
    handler, executor, _registry = create_adcp_server_from_platform(
        platform,
        auto_emit_completion_webhooks=False,
        validate_at_init=False,
    )
    try:
        return handler.get_advertised_tools()
    finally:
        executor.shutdown(wait=True)


def test_get_signals_allows_public_catalog_discovery() -> None:
    assert "get_signals" in (DISCOVERY_TOOLS | AUTH_OPTIONAL_TOOLS)


@pytest.mark.parametrize("platform", [MockSellerPlatform(), GamPlatform()])
def test_platforms_advertise_owned_signal_discovery_only(platform) -> None:
    advertised = _advertised_tools(platform)
    assert "get_signals" in advertised
    assert "activate_signal" not in advertised


@pytest.mark.parametrize("platform", [MockSellerPlatform(), GamPlatform()])
def test_owned_signal_platforms_do_not_expose_activation(platform) -> None:
    assert not hasattr(platform, "activate_signal")


@pytest.mark.parametrize("platform", [MockSellerPlatform(), GamPlatform()])
def test_platforms_declare_signals_protocol(platform) -> None:
    protocols = {protocol.value for protocol in platform.capabilities.supported_protocols}
    assert "signals" in protocols


@pytest.mark.parametrize("platform", [MockSellerPlatform(), GamPlatform()])
def test_platforms_declare_catalog_signals_capability(platform) -> None:
    assert platform.capabilities.signals is not None
    assert platform.capabilities.signals.features is not None
    assert platform.capabilities.signals.features.catalog_signals is True
    assert {getattr(mode, "value", mode) for mode in platform.capabilities.signals.discovery_modes} == {
        "brief",
        "wholesale",
    }


@pytest.mark.asyncio
async def test_get_signals_filters_by_structured_signal_id() -> None:
    identity = ResolvedIdentity(
        principal_id="buyer_1",
        tenant_id="tenant_1",
        tenant={"ad_server": "google_ad_manager"},
        protocol="mcp",
    )
    req = GetSignalsRequest(
        signal_ids=[
            {
                "source": "agent",
                "agent_url": "https://salesagent.adcontextprotocol.org/signals",
                "id": "auto_intenders_q1_2025",
            }
        ]
    )

    with patch("src.core.tools.signals._load_tenant_signals", return_value=[]):
        response = await _get_signals_impl(req, identity)

    assert [signal.signal_agent_segment_id for signal in response.signals] == ["auto_intenders_q1_2025"]


@pytest.mark.asyncio
async def test_get_signals_matches_natural_language_signal_spec_tokens() -> None:
    identity = ResolvedIdentity(
        principal_id="buyer_1",
        tenant_id="tenant_1",
        tenant={"ad_server": "google_ad_manager"},
        protocol="mcp",
    )
    req = GetSignalsRequest(signal_spec="Adults interested in electric vehicles")

    with patch("src.core.tools.signals._load_tenant_signals", return_value=[]):
        response = await _get_signals_impl(req, identity)

    assert response.signals
    assert response.signals[0].signal_agent_segment_id == "auto_intenders_q1_2025"


@pytest.mark.asyncio
async def test_get_signals_treats_audience_as_broad_catalog_query() -> None:
    identity = ResolvedIdentity(
        principal_id="buyer_1",
        tenant_id="tenant_1",
        tenant={"ad_server": "google_ad_manager"},
        protocol="mcp",
    )
    req = GetSignalsRequest(signal_spec="audience", pagination={"max_results": 1})

    with patch("src.core.tools.signals._load_tenant_signals", return_value=[]):
        response = await _get_signals_impl(req, identity)

    assert len(response.signals) == 1
    assert response.pagination is not None
    assert response.pagination.has_more is True
    assert response.pagination.cursor == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize("signal_spec", ["EV", "AI", "adults"])
async def test_get_signals_short_or_stopword_specs_do_not_match_all(signal_spec: str) -> None:
    identity = ResolvedIdentity(
        principal_id="buyer_1",
        tenant_id="tenant_1",
        tenant={"ad_server": "google_ad_manager"},
        protocol="mcp",
    )
    req = GetSignalsRequest(signal_spec=signal_spec)

    with patch("src.core.tools.signals._load_tenant_signals", return_value=[]):
        response = await _get_signals_impl(req, identity)

    assert len(response.signals) < 6


@pytest.mark.asyncio
async def test_get_signals_supports_pagination() -> None:
    identity = ResolvedIdentity(
        principal_id="buyer_1",
        tenant_id="tenant_1",
        tenant={"ad_server": "google_ad_manager"},
        protocol="mcp",
    )
    req = GetSignalsRequest(pagination={"max_results": 2})

    with patch("src.core.tools.signals._load_tenant_signals", return_value=[]):
        response = await _get_signals_impl(req, identity)

    assert len(response.signals) == 2
    assert response.pagination is not None
    assert response.pagination.has_more is True
    assert response.pagination.cursor == "2"


@pytest.mark.asyncio
async def test_get_signals_allows_unauthenticated_discovery() -> None:
    identity = ResolvedIdentity(
        tenant_id="tenant_1",
        tenant={"ad_server": "google_ad_manager"},
        protocol="mcp",
    )

    with patch("src.core.tools.signals._load_tenant_signals", return_value=[]):
        response = await _get_signals_impl(GetSignalsRequest(), identity)

    assert response.signals


@pytest.mark.asyncio
async def test_get_signals_rejects_invalid_discovery_mode() -> None:
    identity = ResolvedIdentity(
        principal_id="buyer_1",
        tenant_id="tenant_1",
        tenant={"ad_server": "google_ad_manager"},
        protocol="mcp",
    )

    req = GetSignalsRequest.model_construct(discovery_mode="deep")

    with pytest.raises(AdCPValidationError, match="brief.*wholesale"):
        await _get_signals_impl(req, identity)


@pytest.mark.asyncio
async def test_get_signals_rejects_version_preconditions_for_brief_refresh() -> None:
    identity = ResolvedIdentity(
        principal_id="buyer_1",
        tenant_id="tenant_1",
        tenant={"ad_server": "google_ad_manager"},
        protocol="mcp",
    )

    req = GetSignalsRequest(if_wholesale_feed_version="feed-v1")

    with pytest.raises(AdCPValidationError, match="discovery_mode='wholesale'"):
        await _get_signals_impl(req, identity)


@pytest.mark.asyncio
async def test_get_signals_wholesale_returns_version_and_unchanged() -> None:
    identity = ResolvedIdentity(
        tenant_id="tenant_1",
        tenant={"ad_server": "google_ad_manager"},
        protocol="mcp",
    )

    req = GetSignalsRequest(discovery_mode="wholesale")
    with patch("src.core.tools.signals._load_tenant_signals", return_value=[]):
        first = await _get_signals_impl(req, identity)
        second = await _get_signals_impl(
            GetSignalsRequest(
                discovery_mode="wholesale",
                if_wholesale_feed_version=first.wholesale_feed_version,
                if_pricing_version=first.pricing_version,
            ),
            identity,
        )

    assert first.signals
    assert first.wholesale_feed_version
    assert first.pricing_version == first.wholesale_feed_version
    assert first.cache_scope.value == "public"
    assert second.unchanged is True
    assert second.signals is None
    assert second.wholesale_feed_version == first.wholesale_feed_version


@pytest.mark.asyncio
async def test_current_signal_feed_version_matches_wholesale_get_signals_version() -> None:
    identity = ResolvedIdentity(
        tenant_id="tenant_1",
        tenant={"ad_server": None, "public_agent_url": None},
        protocol="mcp",
    )

    with patch("src.core.tools.signals._load_tenant_signals", return_value=[]):
        response = await _get_signals_impl(GetSignalsRequest(discovery_mode="wholesale"), identity)
        current_version = current_signal_feed_version("tenant_1")

    assert current_version == response.wholesale_feed_version


@pytest.mark.asyncio
async def test_get_signals_uses_public_agent_url_from_tenant_context() -> None:
    tenant = TenantContext.from_dict(
        {
            "tenant_id": "tenant_1",
            "ad_server": "google_ad_manager",
            "public_agent_url": "https://publisher.example.com/adcp",
        }
    )
    identity = ResolvedIdentity(
        tenant_id=tenant.tenant_id,
        tenant=tenant,
        protocol="mcp",
    )

    with patch("src.core.tools.signals._load_tenant_signals", return_value=[]) as load_tenant_signals:
        await _get_signals_impl(GetSignalsRequest(discovery_mode="wholesale"), identity)

    load_tenant_signals.assert_called_once_with(
        "tenant_1",
        ad_server="google_ad_manager",
        agent_url="https://publisher.example.com/adcp",
    )
