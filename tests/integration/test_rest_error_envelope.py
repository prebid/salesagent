"""Integration tests: REST error envelope fields survive harness reconstruction.

Verifies that suggestion (and other fields present in the wire envelope) are
faithfully restored on the reconstructed AdCPError after a REST round-trip.

Before #1417, _envelope_to_adcp_error extracted code/message/recovery/
details but NOT suggestion.  The wire body included suggestion (production code
correct), but result.error.suggestion was always None after REST dispatch.

beads: salesagent-kjfy
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

    def test_rest_reconstructed_error_has_suggestion(self, env_with_data):
        """After REST round-trip, result.error.suggestion matches the wire suggestion.

        This test FAILS before the #1417 fix because _envelope_to_adcp_error
        does not extract suggestion from the wire envelope during reconstruction.
        """
        result = env_with_data.call_via(Transport.REST, req=self._zero_budget_req())
        assert result.is_error, f"Expected error, got payload: {result.payload}"
        wire = result.wire_error_envelope
        assert wire is not None, "No wire error envelope captured"
        wire_suggestion = (wire.get("errors", [{}]) or [{}])[0].get("suggestion")
        assert wire_suggestion, "Wire envelope missing suggestion — precondition for this test"

        assert result.error.suggestion is not None, (
            f"result.error.suggestion is None after REST round-trip. "
            f"Wire had suggestion='{wire_suggestion}'. "
            "_envelope_to_adcp_error dropped it during reconstruction."
        )
        assert result.error.suggestion == wire_suggestion, (
            f"result.error.suggestion '{result.error.suggestion}' != wire suggestion '{wire_suggestion}'"
        )
