"""Tests for error boundary translation — AdCPError at each transport boundary.

Validates that:
- MCP boundary: AdCPError → ToolError with preserved error_code, message, and recovery
- A2A boundary: AdCPError → A2AError with correct JSON-RPC error code and recovery
- REST boundary: AdCPError → proper HTTP status code with recovery field
- ValueError and PermissionError are caught at boundaries
- extract_error_info handles AdCPError instances

beads: salesagent-pyeu, salesagent-d50c
"""

from unittest.mock import patch

import pytest

from src.core.exceptions import (
    AdCPAdapterError,
    AdCPAuthenticationError,
    AdCPError,
    AdCPNotFoundError,
    AdCPValidationError,
)

# ---------------------------------------------------------------------------
# Wire-shape helpers — each boundary now produces the AdCP spec two-layer
# envelope. Tests assert the envelope shape via these helpers instead of
# repeating the structural checks in every test body.
# ---------------------------------------------------------------------------


def _assert_mcp_envelope(exc, code, recovery=None, message_substr=None):
    """Verify a ToolError raised by the MCP boundary carries the envelope."""
    from src.core.tool_error_logging import AdCPToolError

    assert isinstance(exc, AdCPToolError), f"expected AdCPToolError, got {type(exc).__name__}"
    err = exc.envelope["errors"][0]
    assert err["code"] == code, f"errors[0].code={err['code']!r}, expected {code!r}"
    assert (
        exc.envelope["adcp_error"]["code"] == code
    ), f"adcp_error.code={exc.envelope['adcp_error']['code']!r}, expected {code!r}"
    if recovery is not None:
        assert err.get("recovery") == recovery, f"errors[0].recovery={err.get('recovery')!r}, expected {recovery!r}"
    if message_substr is not None:
        assert message_substr in err.get("message", "")


def _assert_a2a_envelope(exc_data, code, recovery):
    """Verify A2AError.data carries the envelope plus backward-compat keys."""
    assert isinstance(exc_data, dict)
    assert exc_data["adcp_error"]["code"] == code
    assert exc_data["errors"][0]["code"] == code
    assert exc_data["adcp_error"]["recovery"] == recovery
    assert exc_data["errors"][0]["recovery"] == recovery
    # Backward-compat top-level keys consumed by the test harness unwrapper.
    assert exc_data["error_code"] == code
    assert exc_data["recovery"] == recovery


def _assert_rest_envelope(body, code, recovery=None, message_substr=None):
    """Verify a REST JSON body has the two-layer envelope shape."""
    assert body["adcp_error"]["code"] == code, f"adcp_error.code={body['adcp_error']['code']!r}, expected {code!r}"
    assert body["errors"][0]["code"] == code, f"errors[0].code={body['errors'][0]['code']!r}, expected {code!r}"
    if recovery is not None:
        assert body["adcp_error"]["recovery"] == recovery
        assert body["errors"][0]["recovery"] == recovery
    if message_substr is not None:
        assert message_substr in body["errors"][0]["message"]


# ---------------------------------------------------------------------------
# MCP Boundary: extract_error_info
# ---------------------------------------------------------------------------


class TestExtractErrorInfoAdCPError:
    """extract_error_info must recognize AdCPError and extract error_code + message + recovery."""

    def test_adcp_validation_error_extracts_code_and_message(self):
        """AdCPValidationError → ('VALIDATION_ERROR', 'bad field', 'correctable')."""
        from src.core.tool_error_logging import extract_error_info

        exc = AdCPValidationError("bad field")
        code, message, recovery = extract_error_info(exc)
        assert code == "VALIDATION_ERROR"
        assert message == "bad field"
        assert recovery == "correctable"

    def test_adcp_auth_error_extracts_code_and_message(self):
        """AdCPAuthenticationError → ('AUTH_REQUIRED', 'bad token', 'terminal')."""
        from src.core.tool_error_logging import extract_error_info

        exc = AdCPAuthenticationError("bad token")
        code, message, recovery = extract_error_info(exc)
        assert code == "AUTH_REQUIRED"
        assert message == "bad token"
        assert recovery == "terminal"

    def test_adcp_not_found_extracts_code_and_message(self):
        """AdCPNotFoundError → ('NOT_FOUND', 'resource missing', 'terminal')."""
        from src.core.tool_error_logging import extract_error_info

        exc = AdCPNotFoundError("resource missing")
        code, message, recovery = extract_error_info(exc)
        assert code == "NOT_FOUND"
        assert message == "resource missing"
        assert recovery == "terminal"

    def test_adcp_adapter_error_extracts_code_and_message(self):
        """AdCPAdapterError → ('SERVICE_UNAVAILABLE', 'GAM down', 'transient')."""
        from src.core.tool_error_logging import extract_error_info

        exc = AdCPAdapterError("GAM down")
        code, message, recovery = extract_error_info(exc)
        assert code == "SERVICE_UNAVAILABLE"
        assert message == "GAM down"
        assert recovery == "transient"

    def test_adcp_conflict_error_extracts_code_and_message(self):
        """AdCPConflictError → ('CONFLICT', 'duplicate key', 'correctable')."""
        from src.core.exceptions import AdCPConflictError
        from src.core.tool_error_logging import extract_error_info

        exc = AdCPConflictError("duplicate key")
        code, message, recovery = extract_error_info(exc)
        assert code == "CONFLICT"
        assert message == "duplicate key"
        assert recovery == "correctable"

    def test_adcp_gone_error_extracts_code_and_message(self):
        """AdCPGoneError → ('INVALID_STATE', 'proposal expired', 'terminal')."""
        from src.core.exceptions import AdCPGoneError
        from src.core.tool_error_logging import extract_error_info

        exc = AdCPGoneError("proposal expired")
        code, message, recovery = extract_error_info(exc)
        assert code == "INVALID_STATE"
        assert message == "proposal expired"
        assert recovery == "terminal"

    def test_adcp_budget_exhausted_error_extracts_code_and_message(self):
        """AdCPBudgetExhaustedError → ('BUDGET_EXHAUSTED', 'budget limit reached', 'correctable')."""
        from src.core.exceptions import AdCPBudgetExhaustedError
        from src.core.tool_error_logging import extract_error_info

        exc = AdCPBudgetExhaustedError("budget limit reached")
        code, message, recovery = extract_error_info(exc)
        assert code == "BUDGET_EXHAUSTED"
        assert message == "budget limit reached"
        assert recovery == "correctable"

    def test_adcp_service_unavailable_error_extracts_code_and_message(self):
        """AdCPServiceUnavailableError → ('SERVICE_UNAVAILABLE', 'product unavailable', 'transient')."""
        from src.core.exceptions import AdCPServiceUnavailableError
        from src.core.tool_error_logging import extract_error_info

        exc = AdCPServiceUnavailableError("product unavailable")
        code, message, recovery = extract_error_info(exc)
        assert code == "SERVICE_UNAVAILABLE"
        assert message == "product unavailable"
        assert recovery == "transient"

    def test_adcp_base_error_extracts_code_and_message(self):
        """AdCPError base → ('INTERNAL_ERROR', 'something broke', 'terminal')."""
        from src.core.tool_error_logging import extract_error_info

        exc = AdCPError("something broke")
        code, message, recovery = extract_error_info(exc)
        assert code == "INTERNAL_ERROR"
        assert message == "something broke"
        assert recovery == "terminal"

    def test_adcp_rate_limit_error_extracts_transient_recovery(self):
        """AdCPRateLimitError → ('RATE_LIMITED', 'too fast', 'transient')."""
        from src.core.exceptions import AdCPRateLimitError
        from src.core.tool_error_logging import extract_error_info

        exc = AdCPRateLimitError("too fast")
        code, message, recovery = extract_error_info(exc)
        assert code == "RATE_LIMITED"
        assert message == "too fast"
        assert recovery == "transient"

    def test_plain_exception_returns_none_recovery(self):
        """Non-AdCPError exceptions return None for recovery."""
        from src.core.tool_error_logging import extract_error_info

        exc = RuntimeError("unexpected")
        code, message, recovery = extract_error_info(exc)
        assert code == "RuntimeError"
        assert message == "unexpected"
        assert recovery is None

    def test_tool_error_with_recovery_arg(self):
        """ToolError with 3 args extracts recovery from third arg."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import extract_error_info

        exc = ToolError("SERVICE_UNAVAILABLE", "GAM down", "transient")
        code, message, recovery = extract_error_info(exc)
        assert code == "SERVICE_UNAVAILABLE"
        assert message == "GAM down"
        assert recovery == "transient"

    def test_tool_error_without_recovery_returns_none(self):
        """ToolError with 2 args returns None for recovery."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import extract_error_info

        exc = ToolError("VALIDATION_ERROR", "bad field")
        code, message, recovery = extract_error_info(exc)
        assert code == "VALIDATION_ERROR"
        assert message == "bad field"
        assert recovery is None


# ---------------------------------------------------------------------------
# MCP Boundary: with_error_logging translates AdCPError → ToolError
# ---------------------------------------------------------------------------


class TestMCPBoundaryAdCPErrorTranslation:
    """with_error_logging must catch AdCPError and re-raise as ToolError with recovery."""

    def test_adcp_validation_becomes_tool_error(self):
        """AdCPValidationError from tool → ToolError with VALIDATION_ERROR code."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import with_error_logging

        def failing_tool():
            raise AdCPValidationError("bad field")

        wrapped = with_error_logging(failing_tool)

        with pytest.raises(ToolError) as exc_info:
            wrapped()

        # ToolError should carry the error code from AdCPError
        assert "VALIDATION_ERROR" in str(exc_info.value) or (
            exc_info.value.args and exc_info.value.args[0] == "VALIDATION_ERROR"
        )

    def test_adcp_validation_tool_error_carries_recovery(self):
        """AdCPValidationError → ToolError envelope carries 'correctable' recovery."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import with_error_logging

        def failing_tool():
            raise AdCPValidationError("bad field")

        wrapped = with_error_logging(failing_tool)

        with pytest.raises(ToolError) as exc_info:
            wrapped()

        _assert_mcp_envelope(exc_info.value, "VALIDATION_ERROR", recovery="correctable")

    def test_adcp_adapter_tool_error_carries_transient_recovery(self):
        """AdCPAdapterError → ToolError envelope carries 'transient' recovery."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import with_error_logging

        def failing_tool():
            raise AdCPAdapterError("GAM down")

        wrapped = with_error_logging(failing_tool)

        with pytest.raises(ToolError) as exc_info:
            wrapped()

        _assert_mcp_envelope(exc_info.value, "SERVICE_UNAVAILABLE", recovery="transient", message_substr="GAM down")

    def test_adcp_auth_becomes_tool_error(self):
        """AdCPAuthenticationError from tool → ToolError envelope with AUTH_REQUIRED + terminal."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import with_error_logging

        def failing_tool():
            raise AdCPAuthenticationError("bad token")

        wrapped = with_error_logging(failing_tool)

        with pytest.raises(ToolError) as exc_info:
            wrapped()

        _assert_mcp_envelope(exc_info.value, "AUTH_REQUIRED", recovery="terminal")

    @pytest.mark.asyncio
    async def test_async_adcp_validation_becomes_tool_error(self):
        """Async: AdCPValidationError → ToolError envelope with preserved code and recovery."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import with_error_logging

        async def failing_tool():
            raise AdCPValidationError("bad field")

        wrapped = with_error_logging(failing_tool)

        with pytest.raises(ToolError) as exc_info:
            await wrapped()

        _assert_mcp_envelope(exc_info.value, "VALIDATION_ERROR", recovery="correctable")

    def test_tool_error_still_passes_through(self):
        """Existing ToolError behavior must be preserved — re-raised unchanged."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import with_error_logging

        def failing_tool():
            raise ToolError("EXISTING_CODE", "existing message")

        wrapped = with_error_logging(failing_tool)

        with pytest.raises(ToolError) as exc_info:
            wrapped()

        # Should be the same ToolError, not wrapped
        assert exc_info.value.args[0] == "EXISTING_CODE"

    def test_valueerror_becomes_tool_error(self):
        """ValueError from tool → ToolError with VALIDATION_ERROR code."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import with_error_logging

        def failing_tool():
            raise ValueError("invalid input")

        wrapped = with_error_logging(failing_tool)

        with pytest.raises(ToolError) as exc_info:
            wrapped()

        assert "VALIDATION_ERROR" in str(exc_info.value) or (
            exc_info.value.args and exc_info.value.args[0] == "VALIDATION_ERROR"
        )

    def test_permission_error_becomes_tool_error(self):
        """PermissionError from tool → ToolError with AUTH_REQUIRED code."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import with_error_logging

        def failing_tool():
            raise PermissionError("access denied")

        wrapped = with_error_logging(failing_tool)

        with pytest.raises(ToolError) as exc_info:
            wrapped()

        assert "AUTH_REQUIRED" in str(exc_info.value) or (
            exc_info.value.args and exc_info.value.args[0] == "AUTH_REQUIRED"
        )


# ---------------------------------------------------------------------------
# A2A Boundary: AdCPError → A2AError with proper JSON-RPC error code
# ---------------------------------------------------------------------------


class TestA2ABoundaryAdCPErrorTranslation:
    """_handle_explicit_skill must catch AdCPError and raise A2AError with proper code and recovery."""

    @pytest.mark.asyncio
    async def test_adcp_validation_becomes_invalid_params(self):
        """AdCPValidationError → InvalidParamsError with correctable recovery."""
        from a2a.types import InvalidParamsError

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()

        # Mock a skill handler that raises AdCPValidationError
        async def mock_skill(params, token):
            raise AdCPValidationError("invalid param")

        with patch.object(handler, "_handle_get_products_skill", mock_skill):
            with pytest.raises(InvalidParamsError) as exc_info:
                await handler._handle_explicit_skill("get_products", {}, "token")

            # a2a-sdk 1.0: error attributes are directly on the exception
            assert "invalid param" in exc_info.value.message
            _assert_a2a_envelope(exc_info.value.data, "VALIDATION_ERROR", "correctable")

    @pytest.mark.asyncio
    async def test_adcp_auth_becomes_invalid_request(self):
        """AdCPAuthenticationError → InvalidRequestError with terminal recovery."""
        from a2a.types import InvalidRequestError

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()

        async def mock_skill(params, token):
            raise AdCPAuthenticationError("bad token")

        with patch.object(handler, "_handle_get_products_skill", mock_skill):
            with pytest.raises(InvalidRequestError) as exc_info:
                await handler._handle_explicit_skill("get_products", {}, "token")

            # a2a-sdk 1.0: error attributes are directly on the exception
            assert "bad token" in exc_info.value.message
            _assert_a2a_envelope(exc_info.value.data, "AUTH_REQUIRED", "terminal")

    @pytest.mark.asyncio
    async def test_adcp_adapter_becomes_internal_error(self):
        """AdCPAdapterError → InternalError with transient recovery."""
        from a2a.types import InternalError

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()

        async def mock_skill(params, token):
            raise AdCPAdapterError("GAM down")

        with patch.object(handler, "_handle_get_products_skill", mock_skill):
            with pytest.raises(InternalError) as exc_info:
                await handler._handle_explicit_skill("get_products", {}, "token")

            # a2a-sdk 1.0: error attributes are directly on the exception
            assert "GAM down" in exc_info.value.message
            _assert_a2a_envelope(exc_info.value.data, "SERVICE_UNAVAILABLE", "transient")

    @pytest.mark.asyncio
    async def test_server_error_still_passes_through(self):
        """Existing A2AError behavior preserved — re-raised unchanged."""
        from a2a.types import MethodNotFoundError

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()

        async def mock_skill(params, token):
            raise MethodNotFoundError(message="not found")

        with patch.object(handler, "_handle_get_products_skill", mock_skill):
            with pytest.raises(MethodNotFoundError) as exc_info:
                await handler._handle_explicit_skill("get_products", {}, "token")

            # a2a-sdk 1.0: MethodNotFoundError is an A2AError subclass, re-raised as-is
            assert exc_info.value.message == "not found"


# ---------------------------------------------------------------------------
# REST Boundary: AdCPError → HTTP status code via exception handler
# ---------------------------------------------------------------------------


class TestRESTBoundaryAdCPErrorTranslation:
    """REST endpoints propagate AdCPError to the app-level exception handler with recovery."""

    def test_adcp_validation_from_impl_returns_400(self):
        """AdCPValidationError raised in _impl → REST returns 400 with correctable recovery."""
        from starlette.testclient import TestClient

        from src.app import app

        with patch(
            "src.core.tools.capabilities.get_adcp_capabilities_raw",
            side_effect=AdCPValidationError("invalid request"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/capabilities")
            assert response.status_code == 400
            _assert_rest_envelope(
                response.json(), "VALIDATION_ERROR", recovery="correctable", message_substr="invalid request"
            )

    def test_adcp_auth_from_impl_returns_401(self):
        """AdCPAuthenticationError raised in _impl → REST returns 401 with terminal recovery."""
        from starlette.testclient import TestClient

        from src.app import app

        with patch(
            "src.core.tools.capabilities.get_adcp_capabilities_raw",
            side_effect=AdCPAuthenticationError("token expired"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/capabilities")
            assert response.status_code == 401
            _assert_rest_envelope(response.json(), "AUTH_REQUIRED", recovery="terminal")

    def test_adcp_not_found_from_impl_returns_404(self):
        """AdCPNotFoundError raised in _impl → REST returns 404 with terminal recovery."""
        from starlette.testclient import TestClient

        from src.app import app

        with patch(
            "src.core.tools.capabilities.get_adcp_capabilities_raw",
            side_effect=AdCPNotFoundError("resource not found"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/capabilities")
            assert response.status_code == 404
            # AdCPNotFoundError's NOT_FOUND is INTERNAL_CODES; envelope translates
            # to INVALID_REQUEST so the wire code stays in STANDARD_ERROR_CODES.
            _assert_rest_envelope(response.json(), "INVALID_REQUEST", recovery="terminal")

    def test_adcp_adapter_from_impl_returns_502(self):
        """AdCPAdapterError raised in _impl → REST returns 502 with transient recovery."""
        from starlette.testclient import TestClient

        from src.app import app

        with patch(
            "src.core.tools.capabilities.get_adcp_capabilities_raw",
            side_effect=AdCPAdapterError("GAM unavailable"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/capabilities")
            assert response.status_code == 502
            _assert_rest_envelope(response.json(), "SERVICE_UNAVAILABLE", recovery="transient")

    def test_adcp_conflict_from_impl_returns_409(self):
        """AdCPConflictError raised in _impl → REST returns 409 with correctable recovery."""
        from starlette.testclient import TestClient

        from src.app import app
        from src.core.exceptions import AdCPConflictError

        with patch(
            "src.core.tools.capabilities.get_adcp_capabilities_raw",
            side_effect=AdCPConflictError("duplicate key"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/capabilities")
            assert response.status_code == 409
            _assert_rest_envelope(response.json(), "CONFLICT", recovery="correctable")

    def test_adcp_service_unavailable_from_impl_returns_503(self):
        """AdCPServiceUnavailableError raised in _impl → REST returns 503 with transient recovery."""
        from starlette.testclient import TestClient

        from src.app import app
        from src.core.exceptions import AdCPServiceUnavailableError

        with patch(
            "src.core.tools.capabilities.get_adcp_capabilities_raw",
            side_effect=AdCPServiceUnavailableError("product unavailable"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/capabilities")
            assert response.status_code == 503
            _assert_rest_envelope(response.json(), "SERVICE_UNAVAILABLE", recovery="transient")


# ---------------------------------------------------------------------------
# to_dict() serialization: recovery field present and correct
# ---------------------------------------------------------------------------


class TestToDictRecoveryField:
    """AdCPError.to_dict() must include recovery in the serialized dict."""

    def test_to_dict_includes_recovery_for_all_subclasses(self):
        """Every AdCPError subclass produces recovery in to_dict() output."""
        from src.core.exceptions import (
            AdCPAdapterError,
            AdCPAuthenticationError,
            AdCPAuthorizationError,
            AdCPBudgetExhaustedError,
            AdCPConflictError,
            AdCPError,
            AdCPGoneError,
            AdCPNotFoundError,
            AdCPRateLimitError,
            AdCPServiceUnavailableError,
            AdCPValidationError,
        )

        cases = [
            (AdCPError("internal"), "terminal"),
            (AdCPValidationError("bad field"), "correctable"),
            (AdCPAuthenticationError("bad token"), "terminal"),
            (AdCPAuthorizationError("forbidden"), "terminal"),
            (AdCPNotFoundError("missing"), "terminal"),
            (AdCPConflictError("duplicate"), "correctable"),
            (AdCPGoneError("expired"), "terminal"),
            (AdCPBudgetExhaustedError("no budget"), "correctable"),
            (AdCPRateLimitError("slow down"), "transient"),
            (AdCPAdapterError("GAM down"), "transient"),
            (AdCPServiceUnavailableError("unavailable"), "transient"),
        ]

        for exc, expected_recovery in cases:
            d = exc.to_dict()
            assert "recovery" in d, f"{type(exc).__name__}.to_dict() missing 'recovery' key"
            assert (
                d["recovery"] == expected_recovery
            ), f"{type(exc).__name__}.to_dict() recovery={d['recovery']!r}, expected {expected_recovery!r}"

    def test_to_dict_custom_recovery_override(self):
        """Custom recovery= kwarg overrides class default in to_dict() output."""
        from src.core.exceptions import AdCPNotFoundError

        # Default is "terminal"
        default_exc = AdCPNotFoundError("gone")
        assert default_exc.to_dict()["recovery"] == "terminal"

        # Override to "correctable"
        overridden = AdCPNotFoundError("temporary", recovery="correctable")
        assert overridden.to_dict()["recovery"] == "correctable"

    def test_to_dict_roundtrip_preserves_all_fields(self):
        """Serialize to dict, reconstruct, verify recovery survives the roundtrip."""
        from src.core.exceptions import AdCPAdapterError

        original = AdCPAdapterError("GAM timeout", details={"retry_after": 30})
        d = original.to_dict()

        # Verify all fields present
        assert d == {
            "error_code": "SERVICE_UNAVAILABLE",
            "message": "GAM timeout",
            "recovery": "transient",
            "details": {"retry_after": 30},
        }


# ---------------------------------------------------------------------------
# Custom recovery override preservation through all boundaries
# ---------------------------------------------------------------------------


class TestCustomRecoveryOverrideMCPBoundary:
    """Custom recovery= override must propagate through MCP boundary (with_error_logging)."""

    def test_custom_recovery_propagates_through_mcp_boundary(self):
        """AdCPNotFoundError(recovery='transient') -> ToolError carries 'transient' not 'terminal'."""
        from fastmcp.exceptions import ToolError

        from src.core.exceptions import AdCPNotFoundError
        from src.core.tool_error_logging import with_error_logging

        def failing_tool():
            raise AdCPNotFoundError("temporarily missing", recovery="transient")

        wrapped = with_error_logging(failing_tool)

        with pytest.raises(ToolError) as exc_info:
            wrapped()

        # AdCPNotFoundError's NOT_FOUND code maps to INVALID_REQUEST at the wire
        # boundary so output is spec-compliant; custom recovery still propagates.
        _assert_mcp_envelope(
            exc_info.value, "INVALID_REQUEST", recovery="transient", message_substr="temporarily missing"
        )

    def test_custom_recovery_in_extract_error_info(self):
        """extract_error_info returns overridden recovery, not class default."""
        from src.core.exceptions import AdCPValidationError
        from src.core.tool_error_logging import extract_error_info

        # Override correctable -> terminal
        exc = AdCPValidationError("fatal validation", recovery="terminal")
        code, message, recovery = extract_error_info(exc)
        assert code == "VALIDATION_ERROR"
        assert recovery == "terminal"  # Custom, not default "correctable"


class TestCustomRecoveryOverrideA2ABoundary:
    """Custom recovery= override must propagate through A2A boundary (_adcp_to_a2a_error)."""

    @pytest.mark.asyncio
    async def test_custom_recovery_propagates_through_a2a_boundary(self):
        """AdCPNotFoundError(recovery='transient') -> InternalError.data has 'transient'."""
        from a2a.types import InternalError

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
        from src.core.exceptions import AdCPNotFoundError

        handler = AdCPRequestHandler()

        async def mock_skill(params, token):
            raise AdCPNotFoundError("temporarily missing", recovery="transient")

        with patch.object(handler, "_handle_get_products_skill", mock_skill):
            with pytest.raises(InternalError) as exc_info:
                await handler._handle_explicit_skill("get_products", {}, "token")

            # a2a-sdk 1.0: data is the spec envelope plus legacy keys; custom recovery propagates.
            # NOT_FOUND is internal; wire-translated to INVALID_REQUEST.
            _assert_a2a_envelope(exc_info.value.data, "INVALID_REQUEST", "transient")


class TestCustomRecoveryOverrideRESTBoundary:
    """Custom recovery= override must propagate through REST boundary (exception handler)."""

    def test_custom_recovery_propagates_through_rest_boundary(self):
        """AdCPAdapterError(recovery='terminal') -> REST JSON body has 'terminal'."""
        from starlette.testclient import TestClient

        from src.app import app
        from src.core.exceptions import AdCPAdapterError

        with patch(
            "src.core.tools.capabilities.get_adcp_capabilities_raw",
            side_effect=AdCPAdapterError("permanent failure", recovery="terminal"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/capabilities")
            assert response.status_code == 502
            _assert_rest_envelope(response.json(), "SERVICE_UNAVAILABLE", recovery="terminal")


# ---------------------------------------------------------------------------
# Roundtrip: raise → catch at boundary → serialize → deserialize → check recovery
# ---------------------------------------------------------------------------


class TestRecoveryRoundtrip:
    """Full roundtrip through raise -> boundary catch -> serialize -> verify recovery."""

    def test_mcp_roundtrip_all_subclasses(self):
        """All 11 AdCPError subclasses: raise -> with_error_logging -> ToolError -> extract_error_info."""
        from src.core.exceptions import (
            AdCPAdapterError,
            AdCPAuthenticationError,
            AdCPAuthorizationError,
            AdCPBudgetExhaustedError,
            AdCPConflictError,
            AdCPError,
            AdCPGoneError,
            AdCPNotFoundError,
            AdCPRateLimitError,
            AdCPServiceUnavailableError,
            AdCPValidationError,
        )
        from src.core.tool_error_logging import extract_error_info, with_error_logging

        # AdCPError (INTERNAL_ERROR) and AdCPNotFoundError (NOT_FOUND) hold internal
        # codes; the boundary translates to STANDARD_ERROR_CODES (SERVICE_UNAVAILABLE
        # and INVALID_REQUEST respectively). Other subclasses already use STANDARD codes.
        cases = [
            (AdCPError, "internal", "SERVICE_UNAVAILABLE", "terminal"),
            (AdCPValidationError, "bad", "VALIDATION_ERROR", "correctable"),
            (AdCPAuthenticationError, "unauth", "AUTH_REQUIRED", "terminal"),
            (AdCPAuthorizationError, "forbidden", "AUTH_REQUIRED", "terminal"),
            (AdCPNotFoundError, "missing", "INVALID_REQUEST", "terminal"),
            (AdCPConflictError, "dup", "CONFLICT", "correctable"),
            (AdCPGoneError, "expired", "INVALID_STATE", "terminal"),
            (AdCPBudgetExhaustedError, "broke", "BUDGET_EXHAUSTED", "correctable"),
            (AdCPRateLimitError, "slow", "RATE_LIMITED", "transient"),
            (AdCPAdapterError, "down", "SERVICE_UNAVAILABLE", "transient"),
            (AdCPServiceUnavailableError, "offline", "SERVICE_UNAVAILABLE", "transient"),
        ]

        for exc_class, msg, expected_code, expected_recovery in cases:

            def make_tool(klass=exc_class, message=msg):
                def failing():
                    raise klass(message)

                return failing

            from fastmcp.exceptions import ToolError

            wrapped = with_error_logging(make_tool())

            with pytest.raises(ToolError) as exc_info:
                wrapped()

            tool_error = exc_info.value

            # Step 1: ToolError carries the spec-compliant envelope
            _assert_mcp_envelope(tool_error, expected_code, recovery=expected_recovery)

            # Step 2: extract_error_info can read it back
            code, message_out, recovery = extract_error_info(tool_error)
            assert code == expected_code, f"{exc_class.__name__}: roundtrip code mismatch"
            assert recovery == expected_recovery, f"{exc_class.__name__}: roundtrip recovery mismatch"

    @pytest.mark.asyncio
    async def test_a2a_roundtrip_all_subclasses(self):
        """All 11 AdCPError subclasses: raise -> _handle_explicit_skill -> A2AError.data.recovery."""
        from a2a.types import InternalError as A2AInternalError
        from a2a.types import InvalidParamsError, InvalidRequestError
        from a2a.utils.errors import A2AError

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
        from src.core.exceptions import (
            AdCPAdapterError,
            AdCPAuthenticationError,
            AdCPAuthorizationError,
            AdCPBudgetExhaustedError,
            AdCPConflictError,
            AdCPError,
            AdCPGoneError,
            AdCPNotFoundError,
            AdCPRateLimitError,
            AdCPServiceUnavailableError,
            AdCPValidationError,
        )

        # a2a-sdk 1.0: _adcp_to_a2a_error isinstance dispatch maps to exception types:
        # - Validation/Conflict/BudgetExhausted -> InvalidParamsError
        # - Authentication/Authorization -> InvalidRequestError
        # - Everything else (including NotFound, Gone) -> InternalError
        cases = [
            (AdCPError, "internal", A2AInternalError, "terminal"),
            (AdCPValidationError, "bad", InvalidParamsError, "correctable"),
            (AdCPAuthenticationError, "unauth", InvalidRequestError, "terminal"),
            (AdCPAuthorizationError, "forbidden", InvalidRequestError, "terminal"),
            (AdCPNotFoundError, "missing", A2AInternalError, "terminal"),
            (AdCPConflictError, "dup", InvalidParamsError, "correctable"),
            (AdCPGoneError, "expired", A2AInternalError, "terminal"),
            (AdCPBudgetExhaustedError, "broke", InvalidParamsError, "correctable"),
            (AdCPRateLimitError, "slow", A2AInternalError, "transient"),
            (AdCPAdapterError, "down", A2AInternalError, "transient"),
            (AdCPServiceUnavailableError, "offline", A2AInternalError, "transient"),
        ]

        handler = AdCPRequestHandler()

        for exc_class, msg, expected_a2a_type, expected_recovery in cases:

            async def mock_skill(params, token, klass=exc_class, message=msg):
                raise klass(message)

            with patch.object(handler, "_handle_get_products_skill", mock_skill):
                with pytest.raises(A2AError) as exc_info:
                    await handler._handle_explicit_skill("get_products", {}, "token")

                # a2a-sdk 1.0: check exception type and attributes directly
                assert isinstance(
                    exc_info.value, expected_a2a_type
                ), f"{exc_class.__name__}: expected {expected_a2a_type.__name__}, got {type(exc_info.value).__name__}"
                # The envelope serializes the wire-translated code (translates
                # NOT_FOUND/INTERNAL_ERROR/etc. to STANDARD_ERROR_CODES targets).
                exc_instance = exc_class(msg)
                _assert_a2a_envelope(exc_info.value.data, exc_instance.wire_error_code, expected_recovery)

    def test_rest_roundtrip_all_subclasses(self):
        """All 11 AdCPError subclasses: raise -> REST handler -> JSON body -> verify recovery."""
        from starlette.testclient import TestClient

        from src.app import app
        from src.core.exceptions import (
            AdCPAdapterError,
            AdCPAuthenticationError,
            AdCPAuthorizationError,
            AdCPBudgetExhaustedError,
            AdCPConflictError,
            AdCPError,
            AdCPGoneError,
            AdCPNotFoundError,
            AdCPRateLimitError,
            AdCPServiceUnavailableError,
            AdCPValidationError,
        )

        # Same internal-code -> standard-code translation as the MCP/A2A roundtrip
        # tests above. HTTP status_code is preserved (it comes from the exception
        # class directly, not from the wire code translation).
        cases = [
            (AdCPError, "internal", 500, "SERVICE_UNAVAILABLE", "terminal"),
            (AdCPValidationError, "bad", 400, "VALIDATION_ERROR", "correctable"),
            (AdCPAuthenticationError, "unauth", 401, "AUTH_REQUIRED", "terminal"),
            (AdCPAuthorizationError, "forbidden", 403, "AUTH_REQUIRED", "terminal"),
            (AdCPNotFoundError, "missing", 404, "INVALID_REQUEST", "terminal"),
            (AdCPConflictError, "dup", 409, "CONFLICT", "correctable"),
            (AdCPGoneError, "expired", 410, "INVALID_STATE", "terminal"),
            (AdCPBudgetExhaustedError, "broke", 422, "BUDGET_EXHAUSTED", "correctable"),
            (AdCPRateLimitError, "slow", 429, "RATE_LIMITED", "transient"),
            (AdCPAdapterError, "down", 502, "SERVICE_UNAVAILABLE", "transient"),
            (AdCPServiceUnavailableError, "offline", 503, "SERVICE_UNAVAILABLE", "transient"),
        ]

        for exc_class, msg, expected_status, expected_code, expected_recovery in cases:
            with patch(
                "src.core.tools.capabilities.get_adcp_capabilities_raw",
                side_effect=exc_class(msg),
            ):
                client = TestClient(app, raise_server_exceptions=False)
                response = client.get("/api/v1/capabilities")
                assert (
                    response.status_code == expected_status
                ), f"{exc_class.__name__}: status {response.status_code}, expected {expected_status}"
                _assert_rest_envelope(response.json(), expected_code, recovery=expected_recovery)
