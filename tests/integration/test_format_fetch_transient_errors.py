"""mpo1 repro: typed transient creative-agent errors must stay transient on the wire.

Split from test_creative_sync_behavioral.py so the (intentionally red, pre-fix)
repro ships with the salesagent-mpo1 fix commit, not before it.
"""

from __future__ import annotations

import pytest
from adcp.types import FormatId as AdcpFormatId

from tests.factories import PrincipalFactory, TenantFactory
from tests.factories.creative_asset import build_assets, image_spec, make_creative_asset_minimal
from tests.harness import CreativeSyncEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"


def _make_creative_asset(**overrides):
    """Build a minimal valid CreativeAsset for testing."""
    defaults = {
        "creative_id": "c_test_1",
        "name": "Test Banner",
        "format_id": AdcpFormatId(agent_url=DEFAULT_AGENT_URL, id="display_300x250"),
        "assets": build_assets(image_spec("banner")),
    }
    defaults.update(overrides)
    return make_creative_asset_minimal(**defaults)


class TestFormatFetchTransientErrors:
    """Typed transient errors from the creative-agent registry must stay
    transient ON THE WIRE for sync_creatives — matching create_media_buy.

    salesagent-mpo1: _validation.py catches bare Exception around the format
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
    def test_typed_transient_registry_error_reaches_wire(self, integration_db, raised, wire_code):
        from src.core.exceptions import AdCPRateLimitError, AdCPServiceUnavailableError
        from tests.harness.transport import Transport
        from tests.helpers import assert_envelope_shape

        exc = (
            AdCPRateLimitError("Creative agent rate limited (429)")
            if raised == "rate_limit"
            else AdCPServiceUnavailableError("Creative agent unavailable (503)")
        )

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")
            # get_format is already an AsyncMock from the env's happy-path
            # defaults — inject the failure via side_effect (mock-cap guard).
            env.mock["registry"].return_value.get_format.side_effect = exc

            result = env.call_via(
                Transport.REST,
                creatives=[_make_creative_asset(creative_id="c_transient", name="Transient")],
            )

            assert result.is_error, (
                f"A transient agent failure must fail the request transiently on the wire — "
                f"not return success with a terminal-looking per-item failure. Got: "
                f"{getattr(result, 'wire_response', None) or result.payload!r}"
            )
            assert_envelope_shape(
                result.wire_error_envelope,
                wire_code,
                recovery="transient",
            )


class TestCreateMediaBuyFormatFetchTransientErrors:
    """Same contract on create_media_buy: a typed transient error from the
    format-spec fetch must reach the buyer as a transient wire envelope
    (the ticket requires BOTH tools asserted on the wire). salesagent-mpo1.
    """

    @pytest.mark.parametrize(
        "raised, wire_code",
        [
            ("rate_limit", "RATE_LIMITED"),
            ("service_unavailable", "SERVICE_UNAVAILABLE"),
        ],
    )
    def test_typed_transient_fetch_error_reaches_wire(self, integration_db, raised, wire_code):
        from src.core.exceptions import AdCPRateLimitError, AdCPServiceUnavailableError
        from tests.factories import CreativeFactory
        from tests.harness.media_buy_create import MediaBuyCreateEnv
        from tests.harness.transport import Transport
        from tests.helpers import assert_envelope_shape
        from tests.integration.media_buy_helpers import _make_create_request

        exc = (
            AdCPRateLimitError("Creative agent rate limited (429)")
            if raised == "rate_limit"
            else AdCPServiceUnavailableError("Creative agent unavailable (503)")
        )

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

            result = env.call_via(
                Transport.REST,
                req=_make_create_request(
                    packages=[
                        {
                            "product_id": "prod_1",
                            "budget": 5000.0,
                            "pricing_option_id": "cpm_usd_fixed",
                            "creative_ids": ["c_fetch_transient"],
                        }
                    ]
                ),
            )

            assert result.is_error, (
                f"A transient fetch failure must fail create_media_buy transiently: {result.payload!r}"
            )
            assert_envelope_shape(
                result.wire_error_envelope,
                wire_code,
                recovery="transient",
            )
