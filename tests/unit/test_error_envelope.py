"""Unit tests for build_two_layer_error_envelope().

This serializer is the single source of truth for AdCP spec-compliant
two-layer error responses. Boundary translators (MCP, A2A, REST) and
ContextManager.fail_step both call this so wire responses and persisted
workflow_step.response_data are byte-identical by construction.

The two-layer model is normative since AdCP spec 3.0.6 (CHANGELOG 91b6e2c).
Storyboard runners (e.g., @adcp/sdk@6.11.0) read errors[0].code AND
adcp_error.code; missing either layer triggers MCP_ERROR synthesis.
"""

from __future__ import annotations

from src.core.exceptions import (
    AdCPError,
    AdCPNotFoundError,
    AdCPValidationError,
    build_two_layer_error_envelope,
)


class TestEnvelopeShape:
    """Both adcp_error.code (envelope) AND errors[0].code (payload) must be present."""

    def test_envelope_has_both_layers(self):
        exc = AdCPNotFoundError("media buy not found")
        envelope = build_two_layer_error_envelope(exc)

        assert "adcp_error" in envelope, "envelope-level adcp_error key missing"
        assert "errors" in envelope, "payload-level errors[] key missing"
        assert envelope["errors"], "errors[] must contain at least one entry"

    def test_codes_match_across_layers(self):
        """envelope.adcp_error.code == envelope.errors[0].code."""
        exc = AdCPNotFoundError("missing")
        envelope = build_two_layer_error_envelope(exc)
        assert envelope["adcp_error"]["code"] == envelope["errors"][0]["code"]

    def test_wire_translation_applied(self):
        """Internal error_code is translated through ERROR_CODE_MAPPING."""
        exc = AdCPError("bad token")
        exc.error_code = "AUTH_TOKEN_INVALID"
        envelope = build_two_layer_error_envelope(exc)
        assert envelope["adcp_error"]["code"] == "AUTH_REQUIRED"
        assert envelope["errors"][0]["code"] == "AUTH_REQUIRED"

    def test_message_present_in_both_layers(self):
        exc = AdCPValidationError("budget must be positive")
        envelope = build_two_layer_error_envelope(exc)
        assert envelope["adcp_error"]["message"] == "budget must be positive"
        assert envelope["errors"][0]["message"] == "budget must be positive"

    def test_recovery_present_in_both_layers(self):
        exc = AdCPValidationError("...")
        envelope = build_two_layer_error_envelope(exc)
        assert envelope["adcp_error"]["recovery"] == "correctable"
        assert envelope["errors"][0]["recovery"] == "correctable"

    def test_returns_plain_dict_not_pydantic(self):
        """Wire transports need a JSON-serializable dict."""
        exc = AdCPNotFoundError("x")
        envelope = build_two_layer_error_envelope(exc)
        assert isinstance(envelope, dict)
        assert isinstance(envelope["errors"], list)
        assert isinstance(envelope["adcp_error"], dict)


class TestContextEcho:
    """exc.context echoes into envelope.context when present (3.0.6 spec)."""

    def test_context_echoed_when_present(self):
        from adcp.types import ContextObject

        ctx = ContextObject(correlation_id="abc-123")
        exc = AdCPNotFoundError("not found", context=ctx)
        envelope = build_two_layer_error_envelope(exc)
        assert "context" in envelope
        assert envelope["context"]["correlation_id"] == "abc-123"

    def test_context_omitted_when_none(self):
        exc = AdCPNotFoundError("not found")
        envelope = build_two_layer_error_envelope(exc)
        assert "context" not in envelope

    def test_context_accepts_plain_dict(self):
        """Builder tolerates dict context (for paths without ContextObject access)."""
        exc = AdCPNotFoundError("not found", context={"correlation_id": "dict-ctx"})
        envelope = build_two_layer_error_envelope(exc)
        assert envelope["context"]["correlation_id"] == "dict-ctx"


class TestOptionalFieldsPropagate:
    """field, suggestion, details propagate into both layers."""

    def test_field_propagates(self):
        exc = AdCPValidationError("invalid", field="budget")
        envelope = build_two_layer_error_envelope(exc)
        assert envelope["errors"][0]["field"] == "budget"
        assert envelope["adcp_error"]["field"] == "budget"

    def test_suggestion_propagates(self):
        exc = AdCPValidationError("too low", suggestion="set budget >= 100")
        envelope = build_two_layer_error_envelope(exc)
        assert envelope["errors"][0]["suggestion"] == "set budget >= 100"
        assert envelope["adcp_error"]["suggestion"] == "set budget >= 100"

    def test_details_propagate(self):
        exc = AdCPValidationError("multi-field", details={"min": 100, "got": 50})
        envelope = build_two_layer_error_envelope(exc)
        assert envelope["errors"][0]["details"] == {"min": 100, "got": 50}
        assert envelope["adcp_error"]["details"] == {"min": 100, "got": 50}


class TestAdCPErrorContextAttribute:
    """AdCPError.__init__ accepts a context keyword for spec-compliant echo."""

    def test_accepts_context_kwarg(self):
        from adcp.types import ContextObject

        ctx = ContextObject(correlation_id="xyz")
        exc = AdCPNotFoundError("x", context=ctx)
        assert exc.context is ctx

    def test_context_defaults_to_none(self):
        exc = AdCPNotFoundError("x")
        assert exc.context is None

    def test_to_adcp_error_does_not_include_context(self):
        """The payload-only helper preserves SDK adcp_error() shape; envelope is for context."""
        from adcp.types import ContextObject

        exc = AdCPNotFoundError("x", context=ContextObject(correlation_id="abc"))
        payload = exc.to_adcp_error()
        # SDK adcp_error() does not have context — only build_two_layer_error_envelope adds it
        assert "context" not in payload


class TestTypedSubclasses:
    """7 new subclasses pin their wire error_code to STANDARD_ERROR_CODES entries."""

    def test_media_buy_not_found(self):
        from src.core.exceptions import AdCPMediaBuyNotFoundError, AdCPNotFoundError

        exc = AdCPMediaBuyNotFoundError("buy_x missing")
        assert isinstance(exc, AdCPNotFoundError)
        assert exc.error_code == "MEDIA_BUY_NOT_FOUND"
        assert exc.status_code == 404

    def test_package_not_found(self):
        from src.core.exceptions import AdCPNotFoundError, AdCPPackageNotFoundError

        exc = AdCPPackageNotFoundError("package_x missing")
        assert isinstance(exc, AdCPNotFoundError)
        assert exc.error_code == "PACKAGE_NOT_FOUND"
        assert exc.status_code == 404

    def test_creative_rejected(self):
        from src.core.exceptions import AdCPCreativeRejectedError

        exc = AdCPCreativeRejectedError("policy violation")
        assert exc.error_code == "CREATIVE_REJECTED"
        assert exc.status_code == 422
        assert exc.recovery == "correctable"

    def test_budget_exceeded(self):
        from src.core.exceptions import AdCPBudgetExceededError

        exc = AdCPBudgetExceededError("over ceiling")
        assert exc.error_code == "BUDGET_EXCEEDED"
        assert exc.status_code == 422
        assert exc.recovery == "correctable"

    def test_budget_too_low(self):
        from src.core.exceptions import AdCPBudgetTooLowError

        exc = AdCPBudgetTooLowError("below minimum")
        assert exc.error_code == "BUDGET_TOO_LOW"
        assert exc.status_code == 422
        assert exc.recovery == "correctable"

    def test_capability_not_supported(self):
        from src.core.exceptions import AdCPCapabilityNotSupportedError

        exc = AdCPCapabilityNotSupportedError("vCPM unsupported")
        assert exc.error_code == "UNSUPPORTED_FEATURE"
        assert exc.status_code == 422
        assert exc.recovery == "correctable"

    def test_product_unavailable(self):
        from src.core.exceptions import AdCPProductUnavailableError

        exc = AdCPProductUnavailableError("product offline")
        assert exc.error_code == "PRODUCT_UNAVAILABLE"
        assert exc.status_code == 422
        assert exc.recovery == "correctable"

    def test_all_subclass_codes_are_standard(self):
        """Every new subclass code must be in STANDARD_ERROR_CODES."""
        from adcp.server.helpers import STANDARD_ERROR_CODES

        from src.core.exceptions import (
            AdCPBudgetExceededError,
            AdCPBudgetTooLowError,
            AdCPCapabilityNotSupportedError,
            AdCPCreativeRejectedError,
            AdCPMediaBuyNotFoundError,
            AdCPPackageNotFoundError,
            AdCPProductUnavailableError,
        )

        for cls in (
            AdCPMediaBuyNotFoundError,
            AdCPPackageNotFoundError,
            AdCPCreativeRejectedError,
            AdCPBudgetExceededError,
            AdCPBudgetTooLowError,
            AdCPCapabilityNotSupportedError,
            AdCPProductUnavailableError,
        ):
            assert (
                cls.error_code in STANDARD_ERROR_CODES
            ), f"{cls.__name__}.error_code={cls.error_code!r} is not in STANDARD_ERROR_CODES"
