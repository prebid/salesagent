"""Unit tests for build_two_layer_error_envelope().

This serializer is the single source of truth for AdCP spec-compliant
two-layer error responses. Boundary translators (MCP, A2A, REST) and
ContextManager.fail_workflow_step_for_exception all call this so wire
responses and persisted workflow_step.response_data share the same shape.

The two-layer model is normative since AdCP spec 3.0.0 (error-handling.mdx).
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
        """Internal error_code is translated through ERROR_CODE_MAPPING.

        NOT_FOUND is an INTERNAL_CODES entry mapped to INVALID_REQUEST (a
        spec STANDARD code). AUTH_TOKEN_INVALID is itself a STANDARD code so
        passes through unchanged — it is not in ERROR_CODE_MAPPING.
        """
        exc = AdCPError("resource gone")
        exc.error_code = "NOT_FOUND"
        envelope = build_two_layer_error_envelope(exc)
        assert envelope["adcp_error"]["code"] == "INVALID_REQUEST"
        assert envelope["errors"][0]["code"] == "INVALID_REQUEST"

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
    """exc.context echoes into envelope.context when present (3.0.0 spec)."""

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

    def test_dict_context_is_shallow_copied(self):
        """Mutating the source dict context must not mutate emitted envelope context.

        The three serialization paths (``to_dict``, ``to_adcp_error``,
        ``build_two_layer_error_envelope``) all funnel through
        ``_serialize_context`` and must shallow-copy dict context so an
        exception held across multiple serializations doesn't leak mutations
        from one envelope into another (aliasing footgun once both layers
        may be mutated independently).
        """
        source_ctx = {"correlation_id": "orig"}
        exc = AdCPNotFoundError("not found", context=source_ctx)
        envelope = build_two_layer_error_envelope(exc)

        source_ctx["correlation_id"] = "mutated"
        source_ctx["new_key"] = "added"

        assert envelope["context"]["correlation_id"] == "orig"
        assert "new_key" not in envelope["context"]

    def test_context_object_uses_exclude_none(self):
        """``ContextObject`` serialization must drop unset optional fields.

        ``_serialize_context`` invokes ``model_dump(mode="json", exclude_none=True)``
        so the wire envelope only carries populated fields — matches the
        spec's emit-only-populated-fields norm.
        """
        from adcp.types import ContextObject

        ctx = ContextObject(correlation_id="cid")
        exc = AdCPNotFoundError("x", context=ctx)
        envelope = build_two_layer_error_envelope(exc)

        # Every emitted field must be non-None — exclude_none drops unset optionals
        assert envelope["context"]["correlation_id"] == "cid"
        assert all(v is not None for v in envelope["context"].values())

    def test_three_paths_emit_consistent_context(self):
        """``to_dict``, ``to_adcp_error``, and envelope emit identical context payloads.

        Single source of truth (``_serialize_context``) means all three
        serialization paths must produce byte-identical context dicts for
        the same input — boundary handlers can swap between them without
        observable shape differences.
        """
        from adcp.types import ContextObject

        for ctx_input in (ContextObject(correlation_id="abc"), {"correlation_id": "xyz"}):
            exc = AdCPNotFoundError("x", context=ctx_input)

            flat = exc.to_dict()
            payload = exc.to_adcp_error()
            envelope = build_two_layer_error_envelope(exc)

            assert flat["context"] == envelope["context"]
            assert payload["errors"][0]["details"]["context"] == envelope["context"]


class TestRestAndA2AReconstructionAgree:
    """``parse_rest_error`` and ``_envelope_to_adcp_error`` agree byte-for-byte.

    Both reconstruct an AdCPError subclass from an envelope dict — the REST
    body comes from the FastAPI ``adcp_error_handler``, the A2A body comes
    from the explicit-skill dispatcher's artifact DataPart. The DRY invariant
    says they must produce identical exceptions for identical envelope input,
    so storyboard runners that hit either transport see the same typed result.
    """

    def test_validation_error_envelope_reconstructs_identically(self):
        from src.core.exceptions import AdCPValidationError
        from tests.harness._base import _envelope_to_adcp_error

        source = AdCPValidationError("bad input", details={"field": "budget"})
        envelope = build_two_layer_error_envelope(source)

        from tests.harness._base import BaseTestEnv

        # parse_rest_error is a method — call via a minimal subclass instance
        env = BaseTestEnv.__new__(BaseTestEnv)  # bypass __init__
        rest_exc = env.parse_rest_error(400, envelope)
        a2a_exc = _envelope_to_adcp_error(envelope)

        assert isinstance(rest_exc, AdCPValidationError)
        assert isinstance(a2a_exc, AdCPValidationError)
        assert rest_exc.error_code == a2a_exc.error_code == "VALIDATION_ERROR"
        assert rest_exc.message == a2a_exc.message == "bad input"
        assert rest_exc.recovery == a2a_exc.recovery == "correctable"
        assert rest_exc.details == a2a_exc.details == {"field": "budget"}

    def test_not_found_envelope_reconstructs_identically(self):
        from src.core.exceptions import AdCPMediaBuyNotFoundError
        from tests.harness._base import BaseTestEnv, _envelope_to_adcp_error

        source = AdCPMediaBuyNotFoundError("buy_x missing")
        envelope = build_two_layer_error_envelope(source)

        env = BaseTestEnv.__new__(BaseTestEnv)
        rest_exc = env.parse_rest_error(404, envelope)
        a2a_exc = _envelope_to_adcp_error(envelope)

        assert type(rest_exc) is type(a2a_exc) is AdCPMediaBuyNotFoundError
        assert rest_exc.error_code == a2a_exc.error_code == "MEDIA_BUY_NOT_FOUND"
        assert rest_exc.recovery == a2a_exc.recovery == "correctable"

    def test_rest_falls_back_to_status_when_envelope_lacks_code(self):
        """REST keeps its HTTP-status fallback for unstructured bodies."""
        from src.core.exceptions import AdCPRateLimitError
        from tests.harness._base import BaseTestEnv

        env = BaseTestEnv.__new__(BaseTestEnv)
        # Body without any envelope or legacy keys → status code fallback kicks in
        exc = env.parse_rest_error(429, {"message": "slow down"})
        assert isinstance(exc, AdCPRateLimitError)


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

    def test_substrate_subclasses_present_with_standard_codes(self):
        """Each substrate subclass exists and pins a code in STANDARD_ERROR_CODES.

        Codes used only in advisory-on-success Pattern A construction
        (BUDGET_EXCEEDED, CREATIVE_REJECTED, PRODUCT_UNAVAILABLE) intentionally
        have no dedicated exception subclass — they would be substrate without
        a production raise site (P3 violation). Wire envelopes carrying those
        codes round-trip via the base AdCPError fallback in the harness.
        """
        import importlib

        from adcp.server.helpers import STANDARD_ERROR_CODES

        exc_mod = importlib.import_module("src.core.exceptions")
        substrate = {
            "AdCPMediaBuyNotFoundError": "MEDIA_BUY_NOT_FOUND",
            "AdCPPackageNotFoundError": "PACKAGE_NOT_FOUND",
            "AdCPBudgetTooLowError": "BUDGET_TOO_LOW",
            "AdCPCapabilityNotSupportedError": "UNSUPPORTED_FEATURE",
        }
        for class_name, expected_code in substrate.items():
            cls = getattr(exc_mod, class_name, None)
            assert cls is not None, f"{class_name} missing from src.core.exceptions"
            assert cls.error_code == expected_code, (
                f"{class_name}.error_code={cls.error_code!r}, expected {expected_code!r}"
            )
            assert expected_code in STANDARD_ERROR_CODES, f"{expected_code!r} missing from STANDARD_ERROR_CODES"


class TestWireBytesIdenticalAcrossTransports:
    """Pin the "byte-identical by construction" claim with actual wire bytes.

    Prior tests asserted in-memory dict equality via shared parsing helpers,
    which is tautological (both transports get parsed through the same
    unwrapper). This class instead drives real transports — a REST TestClient
    hitting an endpoint that raises AdCPError, and the A2A failed-skill
    builder used by the production dispatcher — then serializes both wire
    envelopes with ``json.dumps(sort_keys=True)`` and asserts byte equality.

    If the byte-identical claim ever drifts (e.g. one transport starts
    emitting an extra field or a different key order), this test fails
    rather than silently letting the docstrings overclaim.
    """

    def _rest_envelope_bytes(self, exc: AdCPError) -> str:
        """Drive the REST handler via TestClient and return the response body
        serialized with sorted keys."""
        import json
        from unittest.mock import patch

        from starlette.testclient import TestClient

        from src.app import app

        with patch(
            "src.core.tools.capabilities.get_adcp_capabilities_raw",
            side_effect=exc,
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/capabilities")

        # response.content is the actual wire bytes the buyer sees. Round-trip
        # through json so we compare semantic equality (sorted keys) rather
        # than orderdict insertion order.
        return json.dumps(response.json(), sort_keys=True)

    def _a2a_envelope_bytes(self, exc: AdCPError) -> str:
        """Drive the A2A failed-skill builder used by the dispatcher and
        return the embedded envelope serialized with sorted keys."""
        import json

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        result = AdCPRequestHandler._build_failed_skill_result("test_skill", exc)
        # The DataPart carries the envelope under "error_envelope" — extract
        # so we compare envelopes head-to-head, not wrapper-vs-envelope.
        return json.dumps(result["error_envelope"], sort_keys=True)

    def test_validation_error_envelope_matches_across_transports(self):
        exc = AdCPValidationError("budget must be positive", field="budget")
        rest_bytes = self._rest_envelope_bytes(exc)
        a2a_bytes = self._a2a_envelope_bytes(exc)
        assert rest_bytes == a2a_bytes, (
            f"REST and A2A envelopes drifted apart for AdCPValidationError:\n  REST: {rest_bytes}\n  A2A : {a2a_bytes}"
        )

    def test_not_found_error_envelope_matches_across_transports(self):
        exc = AdCPNotFoundError("media buy missing")
        rest_bytes = self._rest_envelope_bytes(exc)
        a2a_bytes = self._a2a_envelope_bytes(exc)
        assert rest_bytes == a2a_bytes, (
            f"REST and A2A envelopes drifted apart for AdCPNotFoundError:\n  REST: {rest_bytes}\n  A2A : {a2a_bytes}"
        )
