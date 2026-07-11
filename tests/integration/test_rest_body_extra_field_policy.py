"""Integration tests: Pattern #7 extra-field policy on api_v1 REST *Body models (dev-forbid arm).

Dev/CI (default ENVIRONMENT): SalesAgentBaseModel binds ``extra="forbid"`` at
class definition, so an unknown TOP-LEVEL key in a REST request body raises
FastAPI's ``RequestValidationError``, which the ``request_validation_error_handler``
in src/app.py translates into the two-layer ``INVALID_REQUEST`` wire envelope
(HTTP 400) — never FastAPI's default raw ``422 {"detail": [...]}``.

The production counterpart (``extra="ignore"``: unknown field silently dropped)
binds at import time and cannot be flipped in-process; it is pinned by the
subprocess test in tests/unit/test_schema_validation_modes.py
(TestProductionModeRestBodyIgnoresExtra).

Spec grounding: AdCP v3.1.1 create-media-buy request schema sets top-level
``additionalProperties: true`` — prod-ignore is the spec-compliant contract;
dev-forbid is the internal Pattern #7 drift gate, stricter than spec (graded
as a project gate, not a BR-* spec obligation).

beads: salesagent-cyz0 (GH #1442)
"""

from datetime import UTC, datetime, timedelta

import pytest

from tests.harness.transport import Transport
from tests.helpers import assert_envelope_shape

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestRestDevModeRejectsUnknownTopLevelField:
    """POST /api/v1/media-buys with an unknown top-level key → INVALID_REQUEST envelope."""

    @pytest.fixture
    def env_with_data(self, integration_db):
        from tests.harness.media_buy_create import MediaBuyCreateEnv

        with MediaBuyCreateEnv() as env:
            env.setup_media_buy_data()
            yield env

    @staticmethod
    def _valid_kwargs() -> dict:
        """Flat create_media_buy kwargs that form a fully valid request body.

        MediaBuyCreateEnv.build_rest_body passes flat kwargs through to the
        wire UNFILTERED (adding only a fresh idempotency_key), so an unknown
        key added on top of these genuinely reaches the HTTP body — the test
        is not vacuous via harness-side filtering.
        """
        now = datetime.now(UTC)
        return {
            "brand": {"domain": "testbrand.com"},
            "start_time": (now + timedelta(days=1)).isoformat(),
            "end_time": (now + timedelta(days=8)).isoformat(),
            "packages": [
                {"product_id": "prod_1", "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"},
            ],
        }

    def test_unknown_top_level_field_yields_invalid_request_envelope(self, env_with_data):
        """Unknown top-level body key → HTTP 400 + two-layer INVALID_REQUEST wire envelope.

        Pins the exact wire contract: code INVALID_REQUEST (not any other 4xx,
        not FastAPI's raw 422 detail list), recovery=correctable, the pydantic
        extra-forbid message, and the offending field name surfaced as
        ``errors[0].field`` — so the failure is attributable to the unknown
        field, not to some other validation problem in the body.
        """
        result = env_with_data.call_via(
            Transport.REST,
            **self._valid_kwargs(),
            nonsense_field="bar",
        )

        assert result.is_error, f"Expected rejection of unknown top-level field, got payload: {result.payload}"
        assert result.envelope["status_code"] == 400, (
            f"Expected HTTP 400 from the INVALID_REQUEST handler, got {result.envelope['status_code']} "
            f"(422 would mean FastAPI's default RequestValidationError leaked past the handler)"
        )
        wire = result.wire_error_envelope
        assert wire is not None, "No wire error envelope captured on REST — handler did not emit the AdCP envelope"
        assert_envelope_shape(
            wire,
            "INVALID_REQUEST",
            recovery="correctable",
            message_substr="Extra inputs are not permitted",
        )
        assert wire["errors"][0].get("field") == "nonsense_field", (
            f"errors[0].field must name the unknown key so the rejection is attributable "
            f"to the extra-field policy, got: {wire['errors'][0]}"
        )

    def test_same_body_without_unknown_field_succeeds(self, env_with_data):
        """Control: the identical body WITHOUT the unknown key is accepted.

        Proves the INVALID_REQUEST in the sibling test is caused by the unknown
        top-level field alone, not by any other defect in the base body.
        """
        result = env_with_data.call_via(Transport.REST, **self._valid_kwargs())

        assert result.is_success, f"Control body must succeed; error: {result.error}"
