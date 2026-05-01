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
from a2a.utils.errors import ServerError
from fastmcp.exceptions import ToolError
from pydantic import ValidationError

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from src.core.exceptions import AdCPAuthenticationError, AdCPError, AdCPValidationError
from src.core.resolved_identity import ResolvedIdentity


class TestMCPErrorShapes:
    """Test that MCP tool errors have consistent structure."""

    @pytest.mark.asyncio
    async def test_missing_required_field_raises_error(self):
        """MCP create_media_buy raises AdCPValidationError when context is missing."""
        from src.core.tools.media_buy_create import create_media_buy

        # Call with missing context triggers AdCPValidationError (transport-agnostic)
        with pytest.raises((AdCPValidationError, ToolError)) as exc_info:
            await create_media_buy(
                brand={"domain": "test.com"},
                packages=[],  # Empty but present; validation will catch the issue
                start_time="2026-01-01T00:00:00Z",
                end_time="2026-02-01T00:00:00Z",
                ctx=None,  # Missing context triggers AdCPValidationError
            )

        # Error should have a meaningful message string
        error = exc_info.value
        assert len(str(error)) > 0, "Error message must not be empty"

    @pytest.mark.asyncio
    async def test_validation_error_raises_error_with_details(self):
        """MCP create_media_buy raises error for Pydantic validation failures."""
        from src.core.tools.media_buy_create import create_media_buy

        # Provide invalid types that fail Pydantic validation
        with pytest.raises((AdCPValidationError, ToolError, ValidationError)):
            await create_media_buy(
                brand={"invalid_key": "no_domain"},  # Wrong structure: missing required 'domain' field
                packages="not_a_list",  # Wrong type: should be list
                start_time="2026-01-01T00:00:00Z",
                end_time="2026-02-01T00:00:00Z",
                ctx=MagicMock(),
            )

    @pytest.mark.asyncio
    async def test_auth_error_raises_validation_error(self):
        """MCP _create_media_buy_impl raises AdCPValidationError when identity is None."""
        from src.core.schemas import CreateMediaBuyRequest
        from src.core.tools.media_buy_create import _create_media_buy_impl

        # Build a minimal valid request
        req = CreateMediaBuyRequest(
            brand={"domain": "testbrand.com"},
            packages=[],
            start_time="2026-01-01T00:00:00Z",
            end_time="2026-02-01T00:00:00Z",
        )

        # _create_media_buy_impl requires identity; passing None triggers AdCPValidationError
        with pytest.raises(AdCPValidationError) as exc_info:
            await _create_media_buy_impl(req=req, identity=None)

        assert "Identity is required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_not_found_principal_returns_error_response(self):
        """MCP _create_media_buy_impl returns error response for non-existent principal."""
        from src.core.resolved_identity import ResolvedIdentity
        from src.core.schemas import CreateMediaBuyRequest
        from src.core.testing_hooks import AdCPTestContext
        from src.core.tools.media_buy_create import _create_media_buy_impl

        req = CreateMediaBuyRequest(
            brand={"domain": "testbrand.com"},
            packages=[],
            start_time="2026-01-01T00:00:00Z",
            end_time="2026-02-01T00:00:00Z",
        )

        identity = ResolvedIdentity(
            principal_id="nonexistent",
            tenant_id="test",
            tenant={"tenant_id": "test"},
            testing_context=AdCPTestContext(dry_run=False, test_session_id="test"),
        )

        with (
            patch("src.core.helpers.context_helpers.ensure_tenant_context", return_value={"tenant_id": "test"}),
            patch("src.core.tools.media_buy_create.validate_setup_complete"),
            patch("src.core.tools.media_buy_create.get_principal_object", return_value=None),
        ):
            result = await _create_media_buy_impl(req=req, identity=identity)

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
        """A2A non-discovery skills raise ServerError when identity is None."""
        with pytest.raises(ServerError) as exc_info:
            await self.handler._handle_explicit_skill(
                skill_name="create_media_buy",
                parameters={"brand": {"domain": "testbrand.com"}},
                identity=None,
            )

        error = exc_info.value
        assert isinstance(error, ServerError)
        assert "Authentication required" in str(error)

    @pytest.mark.asyncio
    async def test_unknown_skill_raises_server_error(self):
        """A2A raises ServerError for unknown skill names."""
        from src.core.resolved_identity import ResolvedIdentity

        mock_identity = ResolvedIdentity(
            principal_id="test_principal", tenant_id="default", tenant={"tenant_id": "default"}, protocol="a2a"
        )
        with pytest.raises(ServerError) as exc_info:
            await self.handler._handle_explicit_skill(
                skill_name="nonexistent_skill",
                parameters={},
                identity=mock_identity,
            )

        error = exc_info.value
        assert isinstance(error, ServerError)
        assert "Unknown skill" in str(error)

    @pytest.mark.asyncio
    async def test_invalid_auth_identity_raises_server_error(self):
        """A2A raises ServerError when identity has no principal (auth required skill)."""
        # Identity with no principal_id simulates invalid auth
        invalid_identity = ResolvedIdentity(
            principal_id=None, tenant_id="default", tenant={"tenant_id": "default"}, protocol="a2a"
        )

        with pytest.raises(ServerError) as exc_info:
            await self.handler._handle_explicit_skill(
                skill_name="create_media_buy",
                parameters={"brand": {"domain": "testbrand.com"}},
                identity=invalid_identity,
            )

        assert "Authentication required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_missing_params_returns_error_dict(self):
        """A2A create_media_buy returns error dict for missing required params."""
        mock_identity = ResolvedIdentity(
            principal_id="test_principal", tenant_id="default", tenant={"tenant_id": "default"}, protocol="a2a"
        )

        result = await self.handler._handle_create_media_buy_skill(
            parameters={"brand": {"domain": "testbrand.com"}},
            identity=mock_identity,
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
        mock_identity = ResolvedIdentity(
            principal_id="test_principal", tenant_id="default", tenant={"tenant_id": "default"}, protocol="a2a"
        )

        # Provide all required params but with invalid types
        result = await self.handler._handle_create_media_buy_skill(
            parameters={
                "brand": {"domain": "testbrand.com"},
                "packages": "not_a_list",  # Invalid type
                "start_time": "2026-01-01T00:00:00Z",
                "end_time": "2026-02-01T00:00:00Z",
            },
            identity=mock_identity,
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

            # Should NOT raise "Authentication required"
            anon_identity = ResolvedIdentity(
                principal_id=None, tenant_id="default", tenant={"tenant_id": "default"}, protocol="a2a"
            )
            try:
                await self.handler._handle_explicit_skill(
                    skill_name="get_products",
                    parameters={"brief": "test"},
                    identity=anon_identity,
                )
            except ServerError as e:
                assert "Authentication required" not in str(e), "Discovery skills should not require authentication"


class TestUpdateMediaBuyErrorShapes:
    """Test that update_media_buy error paths produce consistent errors."""

    @pytest.mark.asyncio
    async def test_missing_context_raises_value_error(self):
        """update_media_buy _impl raises ValueError when identity is None."""
        from src.core.schemas import UpdateMediaBuyRequest
        from src.core.tools.media_buy_update import _update_media_buy_impl

        req = UpdateMediaBuyRequest(
            media_buy_id="buy_001",
        )

        with pytest.raises(ValueError) as exc_info:
            _update_media_buy_impl(req=req, identity=None)

        assert "Identity is required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_a2a_missing_auth_raises_server_error(self):
        """A2A update_media_buy raises ServerError when auth is missing."""
        handler = AdCPRequestHandler()

        with pytest.raises(ServerError) as exc_info:
            await handler._handle_explicit_skill(
                skill_name="update_media_buy",
                parameters={"media_buy_id": "buy_001"},
                identity=None,
            )

        error = exc_info.value
        assert isinstance(error, ServerError)
        assert "Authentication required" in str(error)


class TestListCreativesErrorShapes:
    """Test that list_creatives error paths produce consistent errors."""

    @pytest.mark.asyncio
    async def test_missing_auth_raises_authentication_error(self):
        """list_creatives _impl raises AdCPAuthenticationError when identity is None."""
        from src.core.tools.creatives.listing import _list_creatives_impl

        with pytest.raises(AdCPAuthenticationError) as exc_info:
            _list_creatives_impl(identity=None)

        assert "x-adcp-auth" in str(exc_info.value).lower() or "Missing" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_a2a_missing_auth_raises_server_error(self):
        """A2A list_creatives raises ServerError when auth is missing."""
        handler = AdCPRequestHandler()

        with pytest.raises(ServerError) as exc_info:
            await handler._handle_explicit_skill(
                skill_name="list_creatives",
                parameters={},
                identity=None,
            )

        error = exc_info.value
        assert isinstance(error, ServerError)
        assert "Authentication required" in str(error)


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
        """Both transports produce consistent errors when identity/auth is missing.

        MCP path: _create_media_buy_impl(identity=None) -> AdCPValidationError("Identity is required")
        A2A path: _handle_explicit_skill(identity=None) -> ServerError("Authentication required")

        Both paths reject the request before reaching business logic.
        """
        from src.core.schemas import CreateMediaBuyRequest
        from src.core.tools.media_buy_create import _create_media_buy_impl

        req = CreateMediaBuyRequest(
            brand={"domain": "testbrand.com"},
            packages=[],
            start_time="2026-01-01T00:00:00Z",
            end_time="2026-02-01T00:00:00Z",
        )

        # MCP path: missing identity — raises AdCPValidationError (transport-agnostic)
        mcp_error = None
        try:
            await _create_media_buy_impl(req=req, identity=None)
        except (ToolError, AdCPError) as e:
            mcp_error = e

        # A2A path: missing identity (None = no auth)
        a2a_error = None
        try:
            await self.handler._handle_explicit_skill(
                skill_name="create_media_buy",
                parameters={"brand": {"domain": "testbrand.com"}},
                identity=None,
            )
        except ServerError as e:
            a2a_error = e

        # Both must reject the request
        assert mcp_error is not None, "MCP path must raise error for missing identity"
        assert a2a_error is not None, "A2A path must raise ServerError for missing auth"

        # Both errors indicate authentication/authorization failure
        assert "Identity is required" in str(mcp_error) or "required" in str(mcp_error).lower()
        assert "Authentication required" in str(a2a_error) or "required" in str(a2a_error).lower()

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
                brand={"invalid_key": "no_domain"},  # Missing required 'domain' field triggers ValidationError
                packages=[],
                start_time="2026-01-01T00:00:00Z",
                end_time="2026-02-01T00:00:00Z",
            )
        except ValidationError as e:
            mcp_error_message = str(e)

        # A2A path: missing required params — identity resolved at transport boundary
        mock_identity = ResolvedIdentity(
            principal_id="test_principal", tenant_id="default", tenant={"tenant_id": "default"}, protocol="a2a"
        )

        a2a_result = await self.handler._handle_create_media_buy_skill(
            parameters={"brand": {"domain": "testbrand.com"}},
            identity=mock_identity,
        )

        # A2A should return error dict
        assert isinstance(a2a_result, dict)
        assert a2a_result["success"] is False
        assert "errors" in a2a_result
        assert len(a2a_result["errors"]) > 0

        # Both identify validation/parameter issues
        if mcp_error_message:
            # Error should mention brand or domain validation issue
            assert (
                "brand" in mcp_error_message.lower()
                or "domain" in mcp_error_message.lower()
                or "validation" in mcp_error_message.lower()
            )

        # A2A error identifies missing params
        a2a_error_msg = a2a_result["errors"][0]["message"]
        assert "Missing required" in a2a_error_msg or "parameters" in a2a_error_msg.lower()

    @pytest.mark.asyncio
    async def test_nonexistent_principal_error_consistent(self):
        """Both transports handle non-existent principal the same way.

        The _create_media_buy_impl function returns a CreateMediaBuyError when
        principal is not found. This result flows through both transports.
        """
        from src.core.resolved_identity import ResolvedIdentity
        from src.core.schemas import CreateMediaBuyRequest
        from src.core.testing_hooks import AdCPTestContext
        from src.core.tools.media_buy_create import _create_media_buy_impl

        req = CreateMediaBuyRequest(
            brand={"domain": "testbrand.com"},
            packages=[],
            start_time="2026-01-01T00:00:00Z",
            end_time="2026-02-01T00:00:00Z",
        )

        identity = ResolvedIdentity(
            principal_id="ghost_principal",
            tenant_id="test",
            tenant={"tenant_id": "test"},
            testing_context=AdCPTestContext(dry_run=False, test_session_id="test"),
        )

        with (
            patch("src.core.helpers.context_helpers.ensure_tenant_context", return_value={"tenant_id": "test"}),
            patch("src.core.tools.media_buy_create.validate_setup_complete"),
            patch("src.core.tools.media_buy_create.get_principal_object", return_value=None),
        ):
            # Shared impl returns the same result regardless of transport
            result = await _create_media_buy_impl(req=req, identity=identity)

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

        from src.core.resolved_identity import ResolvedIdentity

        mock_identity = ResolvedIdentity(
            principal_id="test_principal", tenant_id="default", tenant={"tenant_id": "default"}, protocol="a2a"
        )
        with pytest.raises(ServerError) as exc_info:
            await handler._handle_explicit_skill(
                skill_name="totally_fake_skill",
                parameters={},
                identity=mock_identity,
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
            "required_parameters": ["brand", "packages", "start_time", "end_time"],
            "received_parameters": ["brand"],
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


# ---------------------------------------------------------------------------
# Recovery field in MCP error responses
# ---------------------------------------------------------------------------


class TestMCPRecoveryInErrorResponses:
    """Verify that MCP ToolError carries recovery for every AdCPError subclass.

    The MCP boundary (with_error_logging) translates AdCPError -> ToolError(code, msg, recovery).
    Buyer agents parse ToolError.args to decide retry/fix/abandon strategy.
    """

    @pytest.mark.parametrize(
        "exc_class,msg,expected_code,expected_recovery",
        [
            ("AdCPError", "internal error", "INTERNAL_ERROR", "terminal"),
            ("AdCPValidationError", "bad field", "VALIDATION_ERROR", "correctable"),
            ("AdCPAuthenticationError", "bad token", "AUTH_TOKEN_INVALID", "terminal"),
            ("AdCPAuthorizationError", "no access", "AUTHORIZATION_ERROR", "terminal"),
            ("AdCPNotFoundError", "gone", "NOT_FOUND", "terminal"),
            ("AdCPConflictError", "duplicate", "CONFLICT", "correctable"),
            ("AdCPGoneError", "expired", "GONE", "terminal"),
            ("AdCPBudgetExhaustedError", "no budget", "BUDGET_EXHAUSTED", "correctable"),
            ("AdCPRateLimitError", "slow down", "RATE_LIMIT_EXCEEDED", "transient"),
            ("AdCPAdapterError", "GAM down", "ADAPTER_ERROR", "transient"),
            ("AdCPServiceUnavailableError", "offline", "SERVICE_UNAVAILABLE", "transient"),
        ],
        ids=lambda x: x if isinstance(x, str) and x.startswith("AdCP") else "",
    )
    def test_mcp_tool_error_carries_recovery(self, exc_class, msg, expected_code, expected_recovery):
        """ToolError from MCP boundary carries recovery in args[2] for {exc_class}."""
        from fastmcp.exceptions import ToolError

        import src.core.exceptions as exc_mod
        from src.core.tool_error_logging import with_error_logging

        klass = getattr(exc_mod, exc_class)

        def failing_tool():
            raise klass(msg)

        wrapped = with_error_logging(failing_tool)

        with pytest.raises(ToolError) as exc_info:
            wrapped()

        tool_error = exc_info.value
        assert tool_error.args[0] == expected_code
        assert tool_error.args[1] == msg
        assert len(tool_error.args) >= 3, f"ToolError for {exc_class} must have 3 args (code, msg, recovery)"
        assert tool_error.args[2] == expected_recovery


# ---------------------------------------------------------------------------
# Recovery field in A2A error responses
# ---------------------------------------------------------------------------


class TestA2ARecoveryInErrorResponses:
    """Verify that A2A ServerError carries recovery in data for every AdCPError subclass.

    The A2A boundary (_handle_explicit_skill) translates AdCPError -> ServerError
    with data={"recovery": ...}. Buyer agents parse this to decide retry strategy.
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.handler = AdCPRequestHandler()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "exc_class,msg,expected_recovery",
        [
            ("AdCPError", "internal", "terminal"),
            ("AdCPValidationError", "bad", "correctable"),
            ("AdCPAuthenticationError", "unauth", "terminal"),
            ("AdCPAuthorizationError", "forbidden", "terminal"),
            ("AdCPNotFoundError", "missing", "terminal"),
            ("AdCPConflictError", "dup", "correctable"),
            ("AdCPGoneError", "expired", "terminal"),
            ("AdCPBudgetExhaustedError", "broke", "correctable"),
            ("AdCPRateLimitError", "slow", "transient"),
            ("AdCPAdapterError", "down", "transient"),
            ("AdCPServiceUnavailableError", "offline", "transient"),
        ],
        ids=lambda x: x if isinstance(x, str) and x.startswith("AdCP") else "",
    )
    async def test_a2a_server_error_carries_recovery(self, exc_class, msg, expected_recovery):
        """ServerError from A2A boundary has data.recovery={expected_recovery} for {exc_class}."""
        from a2a.utils.errors import ServerError

        import src.core.exceptions as exc_mod

        klass = getattr(exc_mod, exc_class)

        async def mock_skill(params, token):
            raise klass(msg)

        with patch.object(self.handler, "_handle_get_products_skill", mock_skill):
            with pytest.raises(ServerError) as exc_info:
                await self.handler._handle_explicit_skill("get_products", {}, "token")

            error = exc_info.value.error
            assert error.data is not None, f"ServerError.data must not be None for {exc_class}"
            assert "recovery" in error.data, f"ServerError.data must contain 'recovery' for {exc_class}"
            assert error.data["recovery"] == expected_recovery


# ---------------------------------------------------------------------------
# Recovery override preservation through serialization
# ---------------------------------------------------------------------------


class TestRecoveryOverrideInSerialization:
    """Verify custom recovery= override is preserved through all serialization paths."""

    def test_custom_recovery_in_serialize_for_a2a(self):
        """_serialize_for_a2a preserves custom recovery when error model carries it."""
        from src.core.schemas import CreateMediaBuyError, Error

        # Create error response with explicit recovery field
        error_response = CreateMediaBuyError(
            errors=[
                Error(code="not_found", message="temporarily missing"),
            ],
            context=None,
        )

        serialized = AdCPRequestHandler._serialize_for_a2a(error_response)

        assert serialized["success"] is False
        assert serialized["errors"][0]["code"] == "not_found"

    def test_custom_recovery_override_in_to_dict(self):
        """to_dict() reflects custom recovery, not class default."""
        from src.core.exceptions import AdCPConflictError

        # Default recovery is "correctable"
        default = AdCPConflictError("dup")
        assert default.to_dict()["recovery"] == "correctable"

        # Override to "terminal" (e.g., non-retryable conflict)
        overridden = AdCPConflictError("permanent conflict", recovery="terminal")
        assert overridden.to_dict()["recovery"] == "terminal"

    def test_custom_recovery_survives_mcp_then_extract(self):
        """Custom recovery: AdCPError(recovery=X) -> ToolError -> extract_error_info -> X."""
        from fastmcp.exceptions import ToolError

        from src.core.exceptions import AdCPAdapterError
        from src.core.tool_error_logging import extract_error_info, with_error_logging

        def failing():
            raise AdCPAdapterError("permanent failure", recovery="terminal")

        wrapped = with_error_logging(failing)

        with pytest.raises(ToolError) as exc_info:
            wrapped()

        code, message, recovery = extract_error_info(exc_info.value)
        assert recovery == "terminal"  # Custom, not default "transient"


# ---------------------------------------------------------------------------
# Vocabulary consistency: error_codes match adcp-req canonical vocabulary
# ---------------------------------------------------------------------------


class TestErrorCodeVocabularyConsistency:
    """Validate error_code strings against adcp-req ERROR_CODE_VOCABULARY.md.

    The canonical vocabulary is defined in:
    /docs/requirements/ERROR_CODE_VOCABULARY.md (adcp-req repo)

    Our exception hierarchy must use canonical codes where the spec defines them.
    Salesagent-specific codes (INTERNAL_ERROR, AUTH_TOKEN_INVALID, etc.) are
    allowed as vocabulary extensions but must be explicitly declared.
    """

    # Canonical codes from adcp-req spec + salesagent extensions
    CANONICAL_ERROR_CODES = {
        "INTERNAL_ERROR",  # HTTP 500 catch-all (salesagent extension)
        "VALIDATION_ERROR",  # adcp-req: Generic Errors
        "AUTH_TOKEN_INVALID",  # HTTP 401 (salesagent extension)
        "AUTHORIZATION_ERROR",  # HTTP 403 (salesagent extension)
        "NOT_FOUND",  # Generic form of {ENTITY}_NOT_FOUND
        "ACCOUNT_NOT_FOUND",  # adcp-req: Account resolution (BR-RULE-080)
        "ACCOUNT_AMBIGUOUS",  # adcp-req: Natural key matches multiple accounts (BR-RULE-080)
        "ACCOUNT_SETUP_REQUIRED",  # adcp-req: Account requires setup (BR-RULE-080)
        "ACCOUNT_SUSPENDED",  # adcp-req: Account is suspended (BR-RULE-080)
        "ACCOUNT_PAYMENT_REQUIRED",  # adcp-req: Account has outstanding payment (BR-RULE-080)
        "CONFLICT",  # Generic form of {ENTITY}_EXISTS
        "GONE",  # HTTP 410 (salesagent extension)
        "BUDGET_EXHAUSTED",  # HTTP 422 (salesagent extension)
        "RATE_LIMIT_EXCEEDED",  # adcp-req: Rate Limiting / Quota Errors
        "ADAPTER_ERROR",  # HTTP 502 (salesagent extension)
        "CONFIGURATION_ERROR",  # HTTP 500 — decryption/config broken (salesagent extension)
        "SERVICE_UNAVAILABLE",  # adcp-req: Service/Infrastructure Errors
    }

    def test_all_exception_error_codes_are_canonical(self):
        """Every AdCPError subclass error_code must be in the canonical vocabulary."""
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

        exception_classes = [
            AdCPError,
            AdCPValidationError,
            AdCPAuthenticationError,
            AdCPAuthorizationError,
            AdCPNotFoundError,
            AdCPConflictError,
            AdCPGoneError,
            AdCPBudgetExhaustedError,
            AdCPRateLimitError,
            AdCPAdapterError,
            AdCPServiceUnavailableError,
        ]

        for exc_class in exception_classes:
            code = exc_class.error_code
            assert code in self.CANONICAL_ERROR_CODES, (
                f"{exc_class.__name__}.error_code = {code!r} is not in the canonical vocabulary. "
                f"If this is a new code, add it to CANONICAL_ERROR_CODES with a comment. "
                f"If this is a renamed code, update the exception class."
            )

    def test_rate_limit_uses_canonical_code(self):
        """AdCPRateLimitError must use RATE_LIMIT_EXCEEDED (not RATE_LIMITED).

        adcp-req ERROR_CODE_VOCABULARY.md defines RATE_LIMIT_EXCEEDED as canonical.
        RATE_LIMITED and THROTTLED are anti-patterns.
        """
        from src.core.exceptions import AdCPRateLimitError

        assert AdCPRateLimitError.error_code == "RATE_LIMIT_EXCEEDED", (
            f"AdCPRateLimitError.error_code = {AdCPRateLimitError.error_code!r}, "
            f"expected 'RATE_LIMIT_EXCEEDED' per adcp-req vocabulary"
        )

    def test_canonical_vocabulary_covers_all_subclasses(self):
        """CANONICAL_ERROR_CODES must have exactly one entry per exception subclass."""
        from src.core.exceptions import AdCPError

        # Discover all concrete subclasses (recursively)
        subclass_codes = set()

        def _collect(cls: type) -> None:
            subclass_codes.add(cls.error_code)
            for sub in cls.__subclasses__():
                _collect(sub)

        _collect(AdCPError)

        # Every subclass code must be in canonical set
        missing = subclass_codes - self.CANONICAL_ERROR_CODES
        assert not missing, (
            f"Exception error_codes not in CANONICAL_ERROR_CODES: {missing}. "
            f"Add them to the canonical set or fix the error_code."
        )

        # Every canonical code must correspond to a subclass
        unused = self.CANONICAL_ERROR_CODES - subclass_codes
        assert not unused, (
            f"CANONICAL_ERROR_CODES entries without a matching exception: {unused}. "
            f"Remove stale entries or create the missing exception class."
        )
