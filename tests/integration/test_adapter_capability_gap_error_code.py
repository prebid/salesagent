"""Integration test: adapter capability gaps reach the wire as UNSUPPORTED_FEATURE.

An adapter that cannot fulfill a buyer-correctable part of the request (e.g.
GAM rejecting postal/device targeting) raises AdCPCapabilityNotSupportedError;
_create_media_buy_impl's ``except AdCPError: raise`` arm must pass it through
so the buyer sees UNSUPPORTED_FEATURE / recovery=correctable — per the pinned
spec (v3.1.1 enums/error-code.json: "check get_adcp_capabilities and remove
unsupported fields"). Before this fix the GAM targeting manager raised bare
ValueError, which the generic handler wrapped into SERVICE_UNAVAILABLE /
transient — telling the buyer to retry a request that can never succeed.

The second test pins the OTHER half of the routing: a genuinely unclassified
adapter exception must STILL surface as SERVICE_UNAVAILABLE / transient (that
is the correct signal for an unknown operational fault). The raise-site
classification itself is graded by tests/unit/test_gam_targeting_v3.py; these
two pin the impl boundary's dispatch on real wire transports.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tests.harness.transport import Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# Real-wire transports only: IMPL has no wire envelope (tests/CLAUDE.md).
_WIRE_TRANSPORTS = [Transport.REST, Transport.MCP, Transport.A2A]


def _req():
    from src.core.schemas import CreateMediaBuyRequest

    now = datetime.now(UTC)
    return CreateMediaBuyRequest(
        brand={"domain": "testbrand.com"},
        start_time=(now + timedelta(days=1)).isoformat(),
        end_time=(now + timedelta(days=8)).isoformat(),
        packages=[{"product_id": "prod_1", "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}],
        idempotency_key=f"cap-key-{uuid.uuid4().hex}",
    )


@pytest.fixture
def env(integration_db):
    from tests.harness.media_buy_create import MediaBuyCreateEnv

    with MediaBuyCreateEnv() as env:
        tenant, _principal = env.setup_default_data()
        env.setup_product_chain(tenant)
        yield env


class TestAdapterCapabilityGapErrorCode:
    @pytest.mark.parametrize("transport", _WIRE_TRANSPORTS)
    def test_typed_capability_gap_passes_through_as_unsupported_feature(self, env, transport):
        from src.core.exceptions import AdCPCapabilityNotSupportedError
        from tests.helpers import assert_envelope_shape

        env.mock["adapter"].return_value.create_media_buy.side_effect = AdCPCapabilityNotSupportedError(
            "Postal code targeting requested but not implemented in GAM static mapping. "
            "Cannot fulfill buyer contract for postal areas: ['10001']."
        )

        result = env.call_via(transport, req=_req())

        assert result.is_error, f"Expected error, got payload: {result.payload}"
        assert_envelope_shape(
            result.wire_error_envelope,
            "UNSUPPORTED_FEATURE",
            recovery="correctable",
            message_substr="Postal code targeting",
        )

    @pytest.mark.parametrize("transport", _WIRE_TRANSPORTS)
    def test_untyped_adapter_fault_stays_service_unavailable(self, env, transport):
        from tests.helpers import assert_envelope_shape

        env.mock["adapter"].return_value.create_media_buy.side_effect = RuntimeError("GAM SOAP timeout")

        result = env.call_via(transport, req=_req())

        assert result.is_error, f"Expected error, got payload: {result.payload}"
        assert_envelope_shape(
            result.wire_error_envelope,
            "SERVICE_UNAVAILABLE",
            recovery="transient",
        )
