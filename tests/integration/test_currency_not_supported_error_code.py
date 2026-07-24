"""Integration test: currency-not-supported emits UNSUPPORTED_FEATURE at the wire.

When a create_media_buy request resolves to a currency the tenant does not
carry in its CurrencyLimit table, production must reject it with the
capability-gap code UNSUPPORTED_FEATURE (not the generic VALIDATION_ERROR).
This is a seller-capability gap, not a malformed request — see UC-002 ext-d
and the update-path sibling (media_buy_update.py), which already emits
UNSUPPORTED_FEATURE.

Covers the create-path currency check (src/core/tools/media_buy_create.py).
beads: salesagent-gh8p.3
"""

from datetime import UTC, datetime, timedelta

import pytest

from tests.harness._idempotency import fresh_idempotency_key
from tests.harness.transport import Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# Real-wire transports only: IMPL has no wire envelope (see tests/CLAUDE.md
# "Error Verification Policy").
_WIRE_TRANSPORTS = [Transport.REST, Transport.MCP, Transport.A2A]


class TestCurrencyNotSupportedErrorCode:
    """Unsupported currency on create -> UNSUPPORTED_FEATURE wire envelope."""

    @pytest.fixture
    def env_with_unsupported_currency_product(self, integration_db):
        from tests.harness.media_buy_create import MediaBuyCreateEnv

        with MediaBuyCreateEnv() as env:
            # Tenant gets an auto USD CurrencyLimit only.
            tenant, _principal, _product, _po = env.setup_media_buy_data()
            # A product whose only pricing option is EUR — a currency the tenant
            # does NOT carry in CurrencyLimit. request_currency resolves to EUR.
            env.setup_product_chain(tenant, product_id="prod_eur", currency="EUR")
            env._commit_factory_data()
            yield env

    def _eur_req(self):
        from src.core.schemas import CreateMediaBuyRequest

        now = datetime.now(UTC)
        return CreateMediaBuyRequest(
            brand={"domain": "testbrand.com"},
            start_time=(now + timedelta(days=1)).isoformat(),
            end_time=(now + timedelta(days=8)).isoformat(),
            packages=[{"product_id": "prod_eur", "budget": 5000.0, "pricing_option_id": "cpm_eur_fixed"}],
            idempotency_key=fresh_idempotency_key("cur-key"),
        )

    @pytest.mark.parametrize("transport", _WIRE_TRANSPORTS)
    def test_unsupported_currency_returns_unsupported_feature(self, env_with_unsupported_currency_product, transport):
        from tests.helpers import assert_envelope_shape

        env = env_with_unsupported_currency_product
        result = env.call_via(transport, req=self._eur_req())

        assert result.is_error, f"Expected error, got payload: {result.payload}"
        assert_envelope_shape(
            result.wire_error_envelope,
            "UNSUPPORTED_FEATURE",
            recovery="correctable",
            message_substr="not supported",
        )
        # Buyer remediation: the suggestion names the offending dimension.
        errors = (result.wire_error_envelope or {}).get("errors", [])
        assert errors and "currency" in (errors[0].get("suggestion") or "").lower(), (
            f"Wire suggestion must mention currency: {errors}"
        )
