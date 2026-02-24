"""Tests for error boundary translation — AdCPError at each transport boundary.

Validates that:
- MCP boundary: AdCPError → ToolError with preserved error_code and message
- A2A boundary: AdCPError → ServerError with correct JSON-RPC error code
- REST boundary: AdCPError → proper HTTP status code (existing handler)
- ValueError and PermissionError are caught at boundaries
- extract_error_info handles AdCPError instances

beads: salesagent-pyeu
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
    """extract_error_info must recognize AdCPError and extract error_code + message."""

    def test_adcp_validation_error_extracts_code_and_message(self):
        """AdCPValidationError → ('VALIDATION_ERROR', 'bad field')."""
        from src.core.tool_error_logging import extract_error_info

        exc = AdCPValidationError("bad field")
        code, message = extract_error_info(exc)
        assert code == "VALIDATION_ERROR"
        assert message == "bad field"

    def test_adcp_auth_error_extracts_code_and_message(self):
        """AdCPAuthenticationError → ('AUTHENTICATION_ERROR', 'bad token')."""
        from src.core.tool_error_logging import extract_error_info

        exc = AdCPAuthenticationError("bad token")
        code, message = extract_error_info(exc)
        assert code == "AUTHENTICATION_ERROR"
        assert message == "bad token"

    def test_adcp_not_found_extracts_code_and_message(self):
        """AdCPNotFoundError → ('NOT_FOUND', 'resource missing')."""
        from src.core.tool_error_logging import extract_error_info

        exc = AdCPNotFoundError("resource missing")
        code, message = extract_error_info(exc)
        assert code == "NOT_FOUND"
        assert message == "resource missing"

    def test_adcp_adapter_error_extracts_code_and_message(self):
        """AdCPAdapterError → ('ADAPTER_ERROR', 'GAM down')."""
        from src.core.tool_error_logging import extract_error_info

        exc = AdCPAdapterError("GAM down")
        code, message = extract_error_info(exc)
        assert code == "ADAPTER_ERROR"
        assert message == "GAM down"

    def test_adcp_base_error_extracts_code_and_message(self):
        """AdCPError base → ('INTERNAL_ERROR', 'something broke')."""
        from src.core.tool_error_logging import extract_error_info

        exc = AdCPError("something broke")
        code, message = extract_error_info(exc)
        assert code == "INTERNAL_ERROR"
        assert message == "something broke"


# ---------------------------------------------------------------------------
# MCP Boundary: with_error_logging translates AdCPError → ToolError
# ---------------------------------------------------------------------------


class TestMCPBoundaryAdCPErrorTranslation:
    """with_error_logging must catch AdCPError and re-raise as ToolError."""

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

    def test_adcp_auth_becomes_tool_error(self):
        """AdCPAuthenticationError from tool → ToolError with AUTHENTICATION_ERROR code."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import with_error_logging

        def failing_tool():
            raise AdCPAuthenticationError("bad token")

        wrapped = with_error_logging(failing_tool)

        with pytest.raises(ToolError) as exc_info:
            wrapped()

        assert "AUTHENTICATION_ERROR" in str(exc_info.value) or (
            exc_info.value.args and exc_info.value.args[0] == "AUTHENTICATION_ERROR"
        )

    @pytest.mark.asyncio
    async def test_async_adcp_validation_becomes_tool_error(self):
        """Async: AdCPValidationError → ToolError with preserved code."""
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
    """_handle_explicit_skill must catch AdCPError and raise ServerError with proper code."""

    @pytest.mark.asyncio
    async def test_adcp_validation_becomes_invalid_params(self):
        """AdCPValidationError → ServerError(InvalidParamsError)."""
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

    @pytest.mark.asyncio
    async def test_adcp_auth_becomes_invalid_request(self):
        """AdCPAuthenticationError → ServerError(InvalidRequestError)."""
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

    @pytest.mark.asyncio
    async def test_adcp_adapter_becomes_internal_error(self):
        """AdCPAdapterError → ServerError(InternalError)."""
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
    """REST endpoints propagate AdCPError to the app-level exception handler."""

    def test_adcp_validation_from_impl_returns_400(self):
        """AdCPValidationError raised in _impl → REST returns 400."""
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

    def test_adcp_auth_from_impl_returns_401(self):
        """AdCPAuthenticationError raised in _impl → REST returns 401."""
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
            assert body["error_code"] == "AUTHENTICATION_ERROR"

    def test_adcp_not_found_from_impl_returns_404(self):
        """AdCPNotFoundError raised in _impl → REST returns 404."""
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

    def test_adcp_adapter_from_impl_returns_502(self):
        """AdCPAdapterError raised in _impl → REST returns 502."""
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
