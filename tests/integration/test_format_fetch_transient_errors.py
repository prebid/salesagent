"""Typed transient creative-agent errors must stay transient on the wire.

Split from the (since retired) creative-sync integration file so the intentionally
red, pre-fix repro shipped with the fix commit, not before it. The same rule is
graded on every transport by @T-UC-006-ext-g in BR-UC-006-sync-creatives.feature.
"""

from __future__ import annotations

import pytest

from tests.factories import PrincipalFactory, TenantFactory
from tests.factories.creative_asset import make_test_banner_creative
from tests.harness import CreativeSyncEnv
from tests.harness.transport import Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"

# Wire transports only — IMPL has no wire envelope by definition. A transient
# swallow at the MCP/A2A boundary must fail this matrix, not just REST.
_WIRE_TRANSPORTS = [Transport.REST, Transport.MCP, Transport.A2A]

_make_creative_asset = make_test_banner_creative  # Canonical version from tests.factories.creative_asset


class TestFormatFetchTransientErrors:
    """Typed transient errors from the creative-agent registry must stay
    transient ON THE WIRE for sync_creatives — matching create_media_buy.

    _validation.py catches bare Exception around the format
    fetch and rewraps typed AdCPRateLimitError/AdCPServiceUnavailableError into
    AdCPAdapterError; the per-item handler in _sync.py then swallows even that
    into a terminal-looking action='failed' entry — the buyer is told to fix
    the creative when the agent is rate-limited. The same 429 on
    create_media_buy propagates as RATE_LIMITED transient (eb5bba06e).
    """

    @pytest.mark.parametrize(
        "raised, wire_code",
        [
            ("rate_limit", "RATE_LIMITED"),
            ("service_unavailable", "SERVICE_UNAVAILABLE"),
        ],
    )
    @pytest.mark.parametrize("transport", _WIRE_TRANSPORTS, ids=lambda t: t.value)
    def test_typed_transient_registry_error_reaches_wire(self, integration_db, raised, wire_code, transport):
        from src.core.exceptions import AdCPRateLimitError, AdCPServiceUnavailableError

        exc = AdCPRateLimitError() if raised == "rate_limit" else AdCPServiceUnavailableError()

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")
            # get_format is already an AsyncMock from the env's happy-path
            # defaults — inject the failure via side_effect (mock-cap guard).
            env.mock["registry"].return_value.get_format.side_effect = exc

            result = env.call_via(
                transport,
                creatives=[_make_creative_asset(creative_id="c_transient", name="Transient")],
            )

            assert result.is_error, (
                f"A transient agent failure must fail the request transiently on the wire — "
                f"not return success with a terminal-looking per-item failure. Got: "
                f"{getattr(result, 'wire_response', None) or result.payload!r}"
            )
            result.assert_wire_error(wire_code, recovery="transient")


class TestCreateMediaBuyFormatFetchTransientErrors:
    """Same contract on create_media_buy: a typed transient error from the
    format-spec fetch must reach the buyer as a transient wire envelope
    (the ticket requires BOTH tools asserted on the wire).
    """

    @pytest.mark.parametrize(
        "raised, wire_code",
        [
            ("rate_limit", "RATE_LIMITED"),
            ("service_unavailable", "SERVICE_UNAVAILABLE"),
        ],
    )
    @pytest.mark.parametrize("transport", _WIRE_TRANSPORTS, ids=lambda t: t.value)
    def test_typed_transient_fetch_error_reaches_wire(self, integration_db, raised, wire_code, transport):
        from src.core.exceptions import AdCPRateLimitError, AdCPServiceUnavailableError
        from tests.factories import CreativeFactory
        from tests.harness.media_buy_create import MediaBuyCreateEnv
        from tests.integration.media_buy_helpers import _single_creative_request

        exc = AdCPRateLimitError() if raised == "rate_limit" else AdCPServiceUnavailableError()

        with MediaBuyCreateEnv() as env:
            tenant, principal, _product, _po = env.setup_media_buy_data()
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_fetch_transient",
                format="display_300x250",
                agent_url=DEFAULT_AGENT_URL,
                data={"url": "https://example.com/ad.jpg", "width": 300, "height": 250},
            )
            env.mock["format_spec"].side_effect = exc

            result = env.call_via(transport, req=_single_creative_request("c_fetch_transient"))

            assert result.is_error, (
                f"A transient fetch failure must fail create_media_buy transiently: {result.payload!r}"
            )
            result.assert_wire_error(wire_code, recovery="transient")
