"""Tests for error boundary translation — AdCPError at each transport boundary.

Validates that:
- MCP boundary: AdCPError → ToolError with preserved error_code, message, and recovery
- A2A boundary: AdCPError → ServerError with correct JSON-RPC error code and recovery
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
        """AdCPAuthenticationError → ('AUTH_TOKEN_INVALID', 'bad token', 'terminal')."""
        from src.core.tool_error_logging import extract_error_info

        exc = AdCPAuthenticationError("bad token")
        code, message, recovery = extract_error_info(exc)
        assert code == "AUTH_TOKEN_INVALID"
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
        """AdCPAdapterError → ('ADAPTER_ERROR', 'GAM down', 'transient')."""
        from src.core.tool_error_logging import extract_error_info

        exc = AdCPAdapterError("GAM down")
        code, message, recovery = extract_error_info(exc)
        assert code == "ADAPTER_ERROR"
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
        """AdCPGoneError → ('GONE', 'proposal expired', 'terminal')."""
        from src.core.exceptions import AdCPGoneError
        from src.core.tool_error_logging import extract_error_info

        exc = AdCPGoneError("proposal expired")
        code, message, recovery = extract_error_info(exc)
        assert code == "GONE"
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
        assert code == "RATE_LIMIT_EXCEEDED"
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

        exc = ToolError("ADAPTER_ERROR", "GAM down", "transient")
        code, message, recovery = extract_error_info(exc)
        assert code == "ADAPTER_ERROR"
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
        """AdCPValidationError → ToolError carries 'correctable' recovery in args[2]."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import with_error_logging

        def failing_tool():
            raise AdCPValidationError("bad field")

        wrapped = with_error_logging(failing_tool)

        with pytest.raises(ToolError) as exc_info:
            wrapped()

        assert len(exc_info.value.args) >= 3
        assert exc_info.value.args[2] == "correctable"

    def test_adcp_adapter_tool_error_carries_transient_recovery(self):
        """AdCPAdapterError → ToolError carries 'transient' recovery in args[2]."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import with_error_logging

        def failing_tool():
            raise AdCPAdapterError("GAM down")

        wrapped = with_error_logging(failing_tool)

        with pytest.raises(ToolError) as exc_info:
            wrapped()

        assert exc_info.value.args[0] == "ADAPTER_ERROR"
        assert exc_info.value.args[1] == "GAM down"
        assert exc_info.value.args[2] == "transient"

    def test_adcp_auth_becomes_tool_error(self):
        """AdCPAuthenticationError from tool → ToolError with AUTH_TOKEN_INVALID code."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import with_error_logging

        def failing_tool():
            raise AdCPAuthenticationError("bad token")

        wrapped = with_error_logging(failing_tool)

        with pytest.raises(ToolError) as exc_info:
            wrapped()

        assert "AUTH_TOKEN_INVALID" in str(exc_info.value) or (
            exc_info.value.args and exc_info.value.args[0] == "AUTH_TOKEN_INVALID"
        )
        assert exc_info.value.args[2] == "terminal"

    @pytest.mark.asyncio
    async def test_async_adcp_validation_becomes_tool_error(self):
        """Async: AdCPValidationError → ToolError with preserved code and recovery."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import with_error_logging

        async def failing_tool():
            raise AdCPValidationError("bad field")

        wrapped = with_error_logging(failing_tool)

        with pytest.raises(ToolError) as exc_info:
            await wrapped()

        assert "VALIDATION_ERROR" in str(exc_info.value) or (
            exc_info.value.args and exc_info.value.args[0] == "VALIDATION_ERROR"
        )
        assert exc_info.value.args[2] == "correctable"

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
        """PermissionError from tool → ToolError with AUTHORIZATION_ERROR code."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import with_error_logging

        def failing_tool():
            raise PermissionError("access denied")

        wrapped = with_error_logging(failing_tool)

        with pytest.raises(ToolError) as exc_info:
            wrapped()

        assert "AUTHORIZATION_ERROR" in str(exc_info.value) or (
            exc_info.value.args and exc_info.value.args[0] == "AUTHORIZATION_ERROR"
        )


# ---------------------------------------------------------------------------
# A2A Boundary: AdCPError → ServerError with proper JSON-RPC error code
# ---------------------------------------------------------------------------


class TestA2ABoundaryAdCPErrorTranslation:
    """_handle_explicit_skill must catch AdCPError and raise ServerError with proper code and recovery."""

    @pytest.mark.asyncio
    async def test_adcp_validation_becomes_invalid_params(self):
        """AdCPValidationError → ServerError(InvalidParamsError) with correctable recovery."""
        from a2a.utils.errors import ServerError

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()

        # Mock a skill handler that raises AdCPValidationError
        async def mock_skill(params, token):
            raise AdCPValidationError("invalid param")

        with patch.object(handler, "_handle_get_products_skill", mock_skill):
            with pytest.raises(ServerError) as exc_info:
                await handler._handle_explicit_skill("get_products", {}, "token")

            # ServerError should contain InvalidParamsError (code -32602)
            error = exc_info.value.error
            assert error.code == -32602
            assert "invalid param" in error.message
            assert error.data == {"recovery": "correctable", "error_code": "VALIDATION_ERROR"}

    @pytest.mark.asyncio
    async def test_adcp_auth_becomes_invalid_request(self):
        """AdCPAuthenticationError → ServerError(InvalidRequestError) with terminal recovery."""
        from a2a.utils.errors import ServerError

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()

        async def mock_skill(params, token):
            raise AdCPAuthenticationError("bad token")

        with patch.object(handler, "_handle_get_products_skill", mock_skill):
            with pytest.raises(ServerError) as exc_info:
                await handler._handle_explicit_skill("get_products", {}, "token")

            error = exc_info.value.error
            assert error.code == -32600
            assert "bad token" in error.message
            assert error.data == {"recovery": "terminal", "error_code": "AUTH_TOKEN_INVALID"}

    @pytest.mark.asyncio
    async def test_adcp_adapter_becomes_internal_error(self):
        """AdCPAdapterError → ServerError(InternalError) with transient recovery."""
        from a2a.utils.errors import ServerError

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()

        async def mock_skill(params, token):
            raise AdCPAdapterError("GAM down")

        with patch.object(handler, "_handle_get_products_skill", mock_skill):
            with pytest.raises(ServerError) as exc_info:
                await handler._handle_explicit_skill("get_products", {}, "token")

            error = exc_info.value.error
            assert error.code == -32603
            assert "GAM down" in error.message
            assert error.data == {"recovery": "transient", "error_code": "ADAPTER_ERROR"}

    @pytest.mark.asyncio
    async def test_server_error_still_passes_through(self):
        """Existing ServerError behavior preserved — re-raised unchanged."""
        from a2a.types import MethodNotFoundError
        from a2a.utils.errors import ServerError

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()

        async def mock_skill(params, token):
            raise ServerError(MethodNotFoundError(message="not found"))

        with patch.object(handler, "_handle_get_products_skill", mock_skill):
            with pytest.raises(ServerError) as exc_info:
                await handler._handle_explicit_skill("get_products", {}, "token")

            # Should be the same ServerError, not wrapped in another
            assert exc_info.value.error.code == -32601


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
            body = response.json()
            assert body["error_code"] == "VALIDATION_ERROR"
            assert "invalid request" in body["message"]
            assert body["recovery"] == "correctable"

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
            body = response.json()
            assert body["error_code"] == "AUTH_TOKEN_INVALID"
            assert body["recovery"] == "terminal"

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
            body = response.json()
            assert body["error_code"] == "NOT_FOUND"
            assert body["recovery"] == "terminal"

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
            body = response.json()
            assert body["error_code"] == "ADAPTER_ERROR"
            assert body["recovery"] == "transient"

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
            body = response.json()
            assert body["error_code"] == "CONFLICT"
            assert body["recovery"] == "correctable"

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
            body = response.json()
            assert body["error_code"] == "SERVICE_UNAVAILABLE"
            assert body["recovery"] == "transient"


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
            assert d["recovery"] == expected_recovery, (
                f"{type(exc).__name__}.to_dict() recovery={d['recovery']!r}, expected {expected_recovery!r}"
            )

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
            "error_code": "ADAPTER_ERROR",
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

        assert exc_info.value.args[0] == "NOT_FOUND"
        assert exc_info.value.args[1] == "temporarily missing"
        assert exc_info.value.args[2] == "transient"  # Custom override, not default "terminal"

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
        """AdCPNotFoundError(recovery='transient') -> ServerError.data has 'transient'."""
        from a2a.utils.errors import ServerError

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
        from src.core.exceptions import AdCPNotFoundError

        handler = AdCPRequestHandler()

        async def mock_skill(params, token):
            raise AdCPNotFoundError("temporarily missing", recovery="transient")

        with patch.object(handler, "_handle_get_products_skill", mock_skill):
            with pytest.raises(ServerError) as exc_info:
                await handler._handle_explicit_skill("get_products", {}, "token")

            error = exc_info.value.error
            assert error.data == {"recovery": "transient", "error_code": "NOT_FOUND"}  # Custom, not "terminal"


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
            body = response.json()
            assert body["error_code"] == "ADAPTER_ERROR"
            assert body["recovery"] == "terminal"  # Custom, not default "transient"


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

        cases = [
            (AdCPError, "internal", "INTERNAL_ERROR", "terminal"),
            (AdCPValidationError, "bad", "VALIDATION_ERROR", "correctable"),
            (AdCPAuthenticationError, "unauth", "AUTH_TOKEN_INVALID", "terminal"),
            (AdCPAuthorizationError, "forbidden", "AUTHORIZATION_ERROR", "terminal"),
            (AdCPNotFoundError, "missing", "NOT_FOUND", "terminal"),
            (AdCPConflictError, "dup", "CONFLICT", "correctable"),
            (AdCPGoneError, "expired", "GONE", "terminal"),
            (AdCPBudgetExhaustedError, "broke", "BUDGET_EXHAUSTED", "correctable"),
            (AdCPRateLimitError, "slow", "RATE_LIMIT_EXCEEDED", "transient"),
            (AdCPAdapterError, "down", "ADAPTER_ERROR", "transient"),
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

            # Step 1: ToolError carries the right args
            assert tool_error.args[0] == expected_code, f"{exc_class.__name__}: code mismatch"
            assert tool_error.args[2] == expected_recovery, f"{exc_class.__name__}: recovery mismatch in ToolError"

            # Step 2: extract_error_info can read it back
            code, message_out, recovery = extract_error_info(tool_error)
            assert code == expected_code, f"{exc_class.__name__}: roundtrip code mismatch"
            assert recovery == expected_recovery, f"{exc_class.__name__}: roundtrip recovery mismatch"

    @pytest.mark.asyncio
    async def test_a2a_roundtrip_all_subclasses(self):
        """All 11 AdCPError subclasses: raise -> _handle_explicit_skill -> ServerError.data.recovery."""
        from a2a.utils.errors import ServerError

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

        # JSON-RPC codes match _adcp_to_a2a_error isinstance dispatch:
        # - Validation/Conflict/BudgetExhausted -> InvalidParamsError (-32602)
        # - Authentication/Authorization -> InvalidRequestError (-32600)
        # - Everything else (including NotFound, Gone) -> InternalError (-32603)
        cases = [
            (AdCPError, "internal", -32603, "terminal"),
            (AdCPValidationError, "bad", -32602, "correctable"),
            (AdCPAuthenticationError, "unauth", -32600, "terminal"),
            (AdCPAuthorizationError, "forbidden", -32600, "terminal"),
            (AdCPNotFoundError, "missing", -32603, "terminal"),
            (AdCPConflictError, "dup", -32602, "correctable"),
            (AdCPGoneError, "expired", -32603, "terminal"),
            (AdCPBudgetExhaustedError, "broke", -32602, "correctable"),
            (AdCPRateLimitError, "slow", -32603, "transient"),
            (AdCPAdapterError, "down", -32603, "transient"),
            (AdCPServiceUnavailableError, "offline", -32603, "transient"),
        ]

        handler = AdCPRequestHandler()

        for exc_class, msg, expected_jsonrpc_code, expected_recovery in cases:

            async def mock_skill(params, token, klass=exc_class, message=msg):
                raise klass(message)

            with patch.object(handler, "_handle_get_products_skill", mock_skill):
                with pytest.raises(ServerError) as exc_info:
                    await handler._handle_explicit_skill("get_products", {}, "token")

                error = exc_info.value.error
                assert error.code == expected_jsonrpc_code, (
                    f"{exc_class.__name__}: JSON-RPC code {error.code}, expected {expected_jsonrpc_code}"
                )
                assert error.data["recovery"] == expected_recovery, (
                    f"{exc_class.__name__}: recovery={error.data.get('recovery')!r}, expected {expected_recovery!r}"
                )
                assert error.data["error_code"] == exc_class.error_code, (
                    f"{exc_class.__name__}: error_code={error.data.get('error_code')!r}, expected {exc_class.error_code!r}"
                )

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

        cases = [
            (AdCPError, "internal", 500, "INTERNAL_ERROR", "terminal"),
            (AdCPValidationError, "bad", 400, "VALIDATION_ERROR", "correctable"),
            (AdCPAuthenticationError, "unauth", 401, "AUTH_TOKEN_INVALID", "terminal"),
            (AdCPAuthorizationError, "forbidden", 403, "AUTHORIZATION_ERROR", "terminal"),
            (AdCPNotFoundError, "missing", 404, "NOT_FOUND", "terminal"),
            (AdCPConflictError, "dup", 409, "CONFLICT", "correctable"),
            (AdCPGoneError, "expired", 410, "GONE", "terminal"),
            (AdCPBudgetExhaustedError, "broke", 422, "BUDGET_EXHAUSTED", "correctable"),
            (AdCPRateLimitError, "slow", 429, "RATE_LIMIT_EXCEEDED", "transient"),
            (AdCPAdapterError, "down", 502, "ADAPTER_ERROR", "transient"),
            (AdCPServiceUnavailableError, "offline", 503, "SERVICE_UNAVAILABLE", "transient"),
        ]

        for exc_class, msg, expected_status, expected_code, expected_recovery in cases:
            with patch(
                "src.core.tools.capabilities.get_adcp_capabilities_raw",
                side_effect=exc_class(msg),
            ):
                client = TestClient(app, raise_server_exceptions=False)
                response = client.get("/api/v1/capabilities")
                assert response.status_code == expected_status, (
                    f"{exc_class.__name__}: status {response.status_code}, expected {expected_status}"
                )
                body = response.json()
                assert body["error_code"] == expected_code, (
                    f"{exc_class.__name__}: error_code={body['error_code']!r}, expected {expected_code!r}"
                )
                assert body["recovery"] == expected_recovery, (
                    f"{exc_class.__name__}: recovery={body['recovery']!r}, expected {expected_recovery!r}"
                )
