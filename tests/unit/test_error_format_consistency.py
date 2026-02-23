#!/usr/bin/env python3
"""
Tests for error format consistency across MCP and A2A transports.

Verifies that:
1. MCP tool errors have consistent structure (ToolError with message)
2. A2A skill errors have consistent JSON-RPC error structure (ServerError)
3. The SAME error scenario produces consistent error types/messages across transports

These are unit tests that mock database/adapter calls to isolate error formatting.
"""

from unittest.mock import MagicMock, patch

import pytest
from a2a.types import InvalidRequestError
from a2a.utils.errors import ServerError
from fastmcp.exceptions import ToolError
from pydantic import ValidationError

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler


class TestMCPErrorShapes:
    """Test that MCP tool errors have consistent structure."""

    @pytest.mark.asyncio
    async def test_missing_required_field_raises_tool_error(self):
        """MCP create_media_buy raises ToolError when required fields are missing."""
        from src.core.tools.media_buy_create import create_media_buy

        # Call with missing required fields (no packages, start_time, end_time)
        with pytest.raises(ToolError) as exc_info:
            await create_media_buy(
                buyer_ref="test_buyer",
                brand_manifest={"name": "Test"},
                packages=[],  # Empty but present; validation will catch the issue
                start_time="2026-01-01T00:00:00Z",
                end_time="2026-02-01T00:00:00Z",
                ctx=None,  # Missing context triggers ToolError
            )

        # ToolError should have a meaningful message string
        error = exc_info.value
        assert isinstance(error, ToolError)
        assert len(str(error)) > 0, "ToolError message must not be empty"

    @pytest.mark.asyncio
    async def test_validation_error_raises_tool_error_with_details(self):
        """MCP create_media_buy raises ToolError for Pydantic validation failures."""
        from src.core.tools.media_buy_create import create_media_buy

        # Provide invalid types that fail Pydantic validation
        with pytest.raises((ToolError, ValidationError)):
            await create_media_buy(
                buyer_ref="test_buyer",
                brand_manifest=12345,  # Wrong type: should be dict or str
                packages="not_a_list",  # Wrong type: should be list
                start_time="2026-01-01T00:00:00Z",
                end_time="2026-02-01T00:00:00Z",
                ctx=MagicMock(),
            )

    @pytest.mark.asyncio
    async def test_auth_error_raises_tool_error(self):
        """MCP _create_media_buy_impl raises ToolError when context is None."""
        from src.core.schemas import CreateMediaBuyRequest
        from src.core.tools.media_buy_create import _create_media_buy_impl

        # Build a minimal valid request
        req = CreateMediaBuyRequest(
            buyer_ref="test_buyer",
            brand_manifest={"name": "Test Brand"},
            packages=[],
            start_time="2026-01-01T00:00:00Z",
            end_time="2026-02-01T00:00:00Z",
        )

        # _create_media_buy_impl requires context; passing None triggers ToolError
        with pytest.raises(ToolError) as exc_info:
            await _create_media_buy_impl(req=req, ctx=None)

        assert "Context is required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_not_found_principal_returns_error_response(self):
        """MCP _create_media_buy_impl returns error response for non-existent principal."""
        from src.core.schemas import CreateMediaBuyRequest
        from src.core.tools.media_buy_create import _create_media_buy_impl

        req = CreateMediaBuyRequest(
            buyer_ref="test_buyer",
            brand_manifest={"name": "Test Brand"},
            packages=[],
            start_time="2026-01-01T00:00:00Z",
            end_time="2026-02-01T00:00:00Z",
        )

        mock_ctx = MagicMock()
        mock_ctx.headers = {}

        with (
            patch("src.core.tools.media_buy_create.get_testing_context") as mock_testing,
            patch("src.core.tools.media_buy_create.get_principal_id_from_context", return_value="nonexistent"),
            patch("src.core.tools.media_buy_create.get_current_tenant", return_value={"tenant_id": "test"}),
            patch("src.core.tools.media_buy_create.validate_setup_complete"),
            patch("src.core.tools.media_buy_create.get_principal_object", return_value=None),
        ):
            mock_testing.return_value = MagicMock(dry_run=False, test_session_id="test")

            result = await _create_media_buy_impl(req=req, ctx=mock_ctx)

        # Should return a CreateMediaBuyResult with error response
        assert hasattr(result, "response")
        response = result.response
        assert hasattr(response, "errors")
        assert response.errors is not None
        assert len(response.errors) > 0
        assert response.errors[0].code == "authentication_error"


class TestA2AErrorShapes:
    """Test that A2A skill errors have consistent JSON-RPC error structure."""

    def setup_method(self):
        """Set up test fixtures."""
        self.handler = AdCPRequestHandler()

    @pytest.mark.asyncio
    async def test_auth_required_error_is_server_error(self):
        """A2A non-discovery skills raise ServerError when auth is missing."""
        with pytest.raises(ServerError) as exc_info:
            await self.handler._handle_explicit_skill(
                skill_name="create_media_buy",
                parameters={"brand_manifest": {"name": "Test"}},
                auth_token=None,
            )

        error = exc_info.value
        assert isinstance(error, ServerError)
        assert "Authentication token required" in str(error)

    @pytest.mark.asyncio
    async def test_unknown_skill_raises_server_error(self):
        """A2A raises ServerError for unknown skill names."""
        with pytest.raises(ServerError) as exc_info:
            await self.handler._handle_explicit_skill(
                skill_name="nonexistent_skill",
                parameters={},
                auth_token="some_token",
            )

        error = exc_info.value
        assert isinstance(error, ServerError)
        assert "Unknown skill" in str(error)

    @pytest.mark.asyncio
    async def test_invalid_auth_token_raises_server_error(self):
        """A2A raises ServerError when auth token is invalid."""
        with patch.object(self.handler, "_create_tool_context_from_a2a") as mock_create:
            mock_create.side_effect = ServerError(InvalidRequestError(message="Invalid authentication token"))

            with pytest.raises(ServerError) as exc_info:
                await self.handler._handle_explicit_skill(
                    skill_name="create_media_buy",
                    parameters={"brand_manifest": {"name": "Test"}},
                    auth_token="invalid_token",
                )

            assert "Invalid authentication token" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_missing_params_returns_error_dict(self):
        """A2A create_media_buy returns error dict for missing required params."""
        with (
            patch.object(self.handler, "_create_tool_context_from_a2a") as mock_create,
        ):
            mock_ctx = MagicMock()
            mock_ctx.principal_id = "test_principal"
            mock_create.return_value = mock_ctx

            result = await self.handler._handle_create_media_buy_skill(
                parameters={"brand_manifest": {"name": "Test"}},
                auth_token="test_token",
            )

        # A2A handler returns dict with consistent error structure
        assert isinstance(result, dict)
        assert result["success"] is False
        assert "message" in result
        assert "Missing required AdCP parameters" in result["message"]
        assert "errors" in result
        assert len(result["errors"]) > 0
        assert result["errors"][0]["code"] == "validation_error"

    @pytest.mark.asyncio
    async def test_validation_error_returns_error_dict(self):
        """A2A create_media_buy returns error dict for invalid parameter types."""
        with (
            patch.object(self.handler, "_create_tool_context_from_a2a") as mock_create,
        ):
            mock_ctx = MagicMock()
            mock_ctx.principal_id = "test_principal"
            mock_create.return_value = mock_ctx

            # Provide all required params but with invalid types
            result = await self.handler._handle_create_media_buy_skill(
                parameters={
                    "brand_manifest": {"name": "Test"},
                    "packages": "not_a_list",  # Invalid type
                    "start_time": "2026-01-01T00:00:00Z",
                    "end_time": "2026-02-01T00:00:00Z",
                },
                auth_token="test_token",
            )

        # Should return error dict (not raise)
        assert isinstance(result, dict)
        assert result["success"] is False
        assert "errors" in result
        assert result["errors"][0]["code"] == "validation_error"

    @pytest.mark.asyncio
    async def test_discovery_skill_no_auth_does_not_raise_auth_error(self):
        """Discovery skills (get_products, etc.) do not require auth."""
        with patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_tool:
            mock_tool.return_value = {"products": []}

            # Should NOT raise "Authentication token required"
            try:
                await self.handler._handle_explicit_skill(
                    skill_name="get_products",
                    parameters={"brief": "test"},
                    auth_token=None,
                )
            except ServerError as e:
                assert "Authentication token required" not in str(e), (
                    "Discovery skills should not require authentication"
                )


class TestUpdateMediaBuyErrorShapes:
    """Test that update_media_buy error paths produce consistent errors."""

    @pytest.mark.asyncio
    async def test_missing_context_raises_value_error(self):
        """update_media_buy _impl raises ValueError when context is None."""
        from src.core.schemas import UpdateMediaBuyRequest
        from src.core.tools.media_buy_update import _update_media_buy_impl

        req = UpdateMediaBuyRequest(
            media_buy_id="buy_001",
        )

        with pytest.raises(ValueError) as exc_info:
            _update_media_buy_impl(req=req, ctx=None)

        assert "Context is required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_a2a_missing_auth_raises_server_error(self):
        """A2A update_media_buy raises ServerError when auth is missing."""
        handler = AdCPRequestHandler()

        with pytest.raises(ServerError) as exc_info:
            await handler._handle_explicit_skill(
                skill_name="update_media_buy",
                parameters={"media_buy_id": "buy_001"},
                auth_token=None,
            )

        error = exc_info.value
        assert isinstance(error, ServerError)
        assert "Authentication token required" in str(error)


class TestListCreativesErrorShapes:
    """Test that list_creatives error paths produce consistent errors."""

    @pytest.mark.asyncio
    async def test_missing_auth_raises_tool_error(self):
        """list_creatives _impl raises ToolError when auth header is missing."""
        from src.core.tools.creatives.listing import _list_creatives_impl

        with pytest.raises(ToolError) as exc_info:
            _list_creatives_impl(ctx=None)

        assert "x-adcp-auth" in str(exc_info.value).lower() or "Missing" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_a2a_missing_auth_raises_server_error(self):
        """A2A list_creatives raises ServerError when auth is missing."""
        handler = AdCPRequestHandler()

        with pytest.raises(ServerError) as exc_info:
            await handler._handle_explicit_skill(
                skill_name="list_creatives",
                parameters={},
                auth_token=None,
            )

        error = exc_info.value
        assert isinstance(error, ServerError)
        assert "Authentication token required" in str(error)


class TestCrossTransportErrorConsistency:
    """Test that the SAME error scenario produces consistent errors across transports.

    The key insight: both MCP and A2A paths call shared _impl() functions.
    This test verifies that the error type and message content are consistent
    regardless of which transport triggers the error.
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.handler = AdCPRequestHandler()

    @pytest.mark.asyncio
    async def test_missing_context_error_consistent(self):
        """Both transports produce consistent errors when context/auth is missing.

        MCP path: _create_media_buy_impl(ctx=None) -> ToolError("Context is required")
        A2A path: _handle_explicit_skill(auth_token=None) -> ServerError("Authentication token required")

        Both paths reject the request before reaching business logic.
        """
        from src.core.schemas import CreateMediaBuyRequest
        from src.core.tools.media_buy_create import _create_media_buy_impl

        req = CreateMediaBuyRequest(
            buyer_ref="test_buyer",
            brand_manifest={"name": "Test Brand"},
            packages=[],
            start_time="2026-01-01T00:00:00Z",
            end_time="2026-02-01T00:00:00Z",
        )

        # MCP path: missing context
        mcp_error = None
        try:
            await _create_media_buy_impl(req=req, ctx=None)
        except ToolError as e:
            mcp_error = e

        # A2A path: missing auth token
        a2a_error = None
        try:
            await self.handler._handle_explicit_skill(
                skill_name="create_media_buy",
                parameters={"brand_manifest": {"name": "Test"}},
                auth_token=None,
            )
        except ServerError as e:
            a2a_error = e

        # Both must reject the request
        assert mcp_error is not None, "MCP path must raise ToolError for missing context"
        assert a2a_error is not None, "A2A path must raise ServerError for missing auth"

        # Both errors indicate authentication/authorization failure
        assert "Context is required" in str(mcp_error) or "required" in str(mcp_error).lower()
        assert "Authentication token required" in str(a2a_error) or "required" in str(a2a_error).lower()

    @pytest.mark.asyncio
    async def test_missing_required_params_error_consistent(self):
        """Both transports report missing required parameters consistently.

        MCP path: CreateMediaBuyRequest validation -> ToolError with field details
        A2A path: _handle_create_media_buy_skill -> dict with errors array

        Both should mention the missing fields.
        """
        from src.core.schemas import CreateMediaBuyRequest

        # MCP path: test the request validation itself
        mcp_error_message = None
        try:
            CreateMediaBuyRequest(
                buyer_ref="test_buyer",
                brand_manifest=12345,  # Invalid type triggers ValidationError
                packages=[],
                start_time="2026-01-01T00:00:00Z",
                end_time="2026-02-01T00:00:00Z",
            )
        except ValidationError as e:
            mcp_error_message = str(e)

        # A2A path: missing required params
        with patch.object(self.handler, "_create_tool_context_from_a2a") as mock_create:
            mock_ctx = MagicMock()
            mock_ctx.principal_id = "test_principal"
            mock_create.return_value = mock_ctx

            a2a_result = await self.handler._handle_create_media_buy_skill(
                parameters={"brand_manifest": {"name": "Test"}},
                auth_token="test_token",
            )

        # A2A should return error dict
        assert isinstance(a2a_result, dict)
        assert a2a_result["success"] is False
        assert "errors" in a2a_result
        assert len(a2a_result["errors"]) > 0

        # Both identify validation/parameter issues
        if mcp_error_message:
            assert "brand_manifest" in mcp_error_message.lower() or "validation" in mcp_error_message.lower()

        # A2A error identifies missing params
        a2a_error_msg = a2a_result["errors"][0]["message"]
        assert "Missing required" in a2a_error_msg or "parameters" in a2a_error_msg.lower()

    @pytest.mark.asyncio
    async def test_nonexistent_principal_error_consistent(self):
        """Both transports handle non-existent principal the same way.

        The _create_media_buy_impl function returns a CreateMediaBuyError when
        principal is not found. This result flows through both transports.
        """
        from src.core.schemas import CreateMediaBuyRequest
        from src.core.tools.media_buy_create import _create_media_buy_impl

        req = CreateMediaBuyRequest(
            buyer_ref="test_buyer",
            brand_manifest={"name": "Test Brand"},
            packages=[],
            start_time="2026-01-01T00:00:00Z",
            end_time="2026-02-01T00:00:00Z",
        )

        mock_ctx = MagicMock()
        mock_ctx.headers = {}

        with (
            patch("src.core.tools.media_buy_create.get_testing_context") as mock_testing,
            patch("src.core.tools.media_buy_create.get_principal_id_from_context", return_value="ghost_principal"),
            patch("src.core.tools.media_buy_create.get_current_tenant", return_value={"tenant_id": "test"}),
            patch("src.core.tools.media_buy_create.validate_setup_complete"),
            patch("src.core.tools.media_buy_create.get_principal_object", return_value=None),
        ):
            mock_testing.return_value = MagicMock(dry_run=False, test_session_id="test")

            # Shared impl returns the same result regardless of transport
            result = await _create_media_buy_impl(req=req, ctx=mock_ctx)

        # The result contains an error response with authentication_error code
        response = result.response
        assert hasattr(response, "errors")
        assert response.errors is not None
        assert len(response.errors) > 0
        error = response.errors[0]

        assert error.code == "authentication_error"
        assert "not found" in error.message.lower()

        # When this flows through A2A's _serialize_for_a2a, it becomes:
        serialized = AdCPRequestHandler._serialize_for_a2a(response)
        assert serialized["success"] is False, "Serialized response must have success=False"
        assert "errors" in serialized
        assert len(serialized["errors"]) > 0
        assert serialized["errors"][0]["code"] == "authentication_error"

    @pytest.mark.asyncio
    async def test_unknown_skill_only_affects_a2a(self):
        """Unknown skill errors only apply to A2A (MCP validates tool names separately).

        MCP uses FastMCP's tool registry which rejects unknown tools at the protocol level.
        A2A uses _handle_explicit_skill which maps skill names to handlers.
        """
        handler = AdCPRequestHandler()

        with pytest.raises(ServerError) as exc_info:
            await handler._handle_explicit_skill(
                skill_name="totally_fake_skill",
                parameters={},
                auth_token="some_token",
            )

        error_str = str(exc_info.value)
        assert "Unknown skill" in error_str or "totally_fake_skill" in error_str

    @pytest.mark.asyncio
    async def test_serialize_for_a2a_error_response_structure(self):
        """Verify _serialize_for_a2a produces consistent structure for error models."""
        from src.core.schemas import CreateMediaBuyError, Error

        # Create an error response like the impl would
        error_response = CreateMediaBuyError(
            errors=[
                Error(code="validation_error", message="Missing required field: packages"),
            ],
            context=None,
        )

        serialized = AdCPRequestHandler._serialize_for_a2a(error_response)

        # Verify consistent error structure
        assert isinstance(serialized, dict)
        assert "success" in serialized
        assert serialized["success"] is False
        assert "errors" in serialized
        assert len(serialized["errors"]) > 0
        assert serialized["errors"][0]["code"] == "validation_error"
        assert "message" in serialized  # Protocol message field added by serializer

    @pytest.mark.asyncio
    async def test_serialize_for_a2a_passes_dict_through(self):
        """Verify _serialize_for_a2a passes dict responses through unchanged.

        A2A handlers may return early-exit error dicts directly (e.g., for
        missing required parameters). These should pass through as-is.
        """
        error_dict = {
            "success": False,
            "message": "Missing required AdCP parameters: ['packages', 'start_time', 'end_time']",
            "required_parameters": ["brand_manifest", "packages", "start_time", "end_time"],
            "received_parameters": ["brand_manifest"],
            "errors": [
                {
                    "code": "validation_error",
                    "message": "Missing required AdCP parameters: ['packages', 'start_time', 'end_time']",
                }
            ],
        }

        serialized = AdCPRequestHandler._serialize_for_a2a(error_dict)

        # Dict should pass through unchanged
        assert serialized == error_dict
        assert serialized["success"] is False
        assert serialized["errors"][0]["code"] == "validation_error"
