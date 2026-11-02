"""Integration tests: the REST error envelope carries what the buyer needs.

Verifies that suggestion (and the other error-object fields) are present on the
WIRE after a REST round-trip -- what the buyer actually receives.

Historically this file guarded a harness reconstruction: before #1417
_envelope_to_adcp_error rebuilt an AdCPSalesAgentError from the wire body and dropped
suggestion while doing so. That reconstruction was deleted by
salesagent-3dawm.15, so the file now asserts the wire directly and the
reconstruction-specific test is gone with the mechanism it guarded.

"""

import pytest

from tests.harness.transport import Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestRestErrorSuggestionPreservation:
    """REST round-trip must preserve the suggestion field on reconstructed errors.

    Regression for #1417.
    """

    @pytest.fixture
    def env_with_data(self, integration_db):
        from tests.harness.media_buy_create import MediaBuyCreateEnv

        with MediaBuyCreateEnv() as env:
            env.setup_media_buy_data()
            yield env

    def _zero_budget_req(self):
        """Build a create request with a zero-budget package (triggers BUDGET_TOO_LOW)."""
        import uuid
        from datetime import UTC, datetime, timedelta

        from src.core.schemas import CreateMediaBuyRequest

        now = datetime.now(UTC)
        return CreateMediaBuyRequest(
            account={"account_id": "acct_test"},
            brand={"domain": "testbrand.com"},
            start_time=(now + timedelta(days=1)).isoformat(),
            end_time=(now + timedelta(days=8)).isoformat(),
            packages=[{"product_id": "prod_1", "budget": 0.0, "pricing_option_id": "cpm_usd_fixed"}],
            # idempotency_key is REQUIRED on CreateMediaBuyRequest (AdCP 3.0.1, #1312);
            # this builder constructs the request directly so it must supply one (16-255
            # chars). The zero-budget VALIDATION_ERROR path runs after request construction.
            idempotency_key=f"int-key-{uuid.uuid4().hex}",
        )

    def test_rest_wire_envelope_contains_suggestion(self, env_with_data):
        """Wire envelope errors[0] includes suggestion field (production is correct)."""
        result = env_with_data.call_via(Transport.REST, req=self._zero_budget_req())
        assert result.is_error, f"Expected error, got payload: {result.payload}"
        wire = result.wire_error_envelope
        assert wire is not None, "No wire error envelope captured"
        errors = wire.get("errors", [])
        assert errors, "Wire envelope has no errors"
        suggestion = errors[0].get("suggestion")
        assert suggestion, f"Wire errors[0] missing suggestion: {errors[0]}"

    # test_rest_reconstructed_error_has_suggestion is DELETED, not migrated. Its whole
    # subject was "the harness reconstruction preserves suggestion" -- it compared
    # result.error.suggestion against the wire's, to catch _envelope_to_adcp_error
    # dropping the field. There is no reconstruction any more (salesagent-3dawm.15):
    # nothing rebuilds a production error from wire bytes, so nothing can drop a field
    # while doing so. Keeping the test would require re-adding the mechanism it
    # guards.
    #
    # The claim that SURVIVES -- the buyer actually receives a suggestion -- is
    # asserted by the sibling above, directly on the wire envelope, which is where it
    # always belonged.
