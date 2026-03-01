"""Tests for MCPAuthMiddleware — centralized identity resolution for MCP tools.

Core Invariant: Identity resolution happens exactly once per MCP request in the
middleware; tool functions read the pre-resolved identity from FastMCP context
state and never call resolve_identity_from_context() directly.

These tests verify:
1. MCPAuthMiddleware class exists and inherits from Middleware
2. on_call_tool resolves identity and stores it on context state
3. Auth-required tools reject invalid tokens before tool body runs
4. Discovery tools (auth-optional) get unauthenticated identity
5. context_id is extracted from headers and stored on state
6. Middleware is registered on the MCP server
7. MCP tool wrappers do NOT call resolve_identity_from_context() directly
"""

import ast
import importlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.resolved_identity import ResolvedIdentity


class TestMCPAuthMiddlewareExists:
    """Verify MCPAuthMiddleware class structure."""

    def test_module_exists(self):
        """src.core.mcp_auth_middleware module must exist."""
        mod = importlib.import_module("src.core.mcp_auth_middleware")
        assert hasattr(mod, "MCPAuthMiddleware"), "MCPAuthMiddleware class not found"

    def test_inherits_from_middleware(self):
        """MCPAuthMiddleware must inherit from fastmcp.server.middleware.Middleware."""
        from fastmcp.server.middleware import Middleware

        from src.core.mcp_auth_middleware import MCPAuthMiddleware

        assert issubclass(MCPAuthMiddleware, Middleware), (
            "MCPAuthMiddleware must inherit from fastmcp.server.middleware.Middleware"
        )

    def test_has_on_call_tool(self):
        """MCPAuthMiddleware must override on_call_tool."""
        from src.core.mcp_auth_middleware import MCPAuthMiddleware

        # Check it's overridden (not just inherited)
        assert "on_call_tool" in MCPAuthMiddleware.__dict__, "MCPAuthMiddleware must override on_call_tool"

    def test_auth_optional_tools_defined(self):
        """AUTH_OPTIONAL_TOOLS set must be defined with discovery tools."""
        from src.core.mcp_auth_middleware import AUTH_OPTIONAL_TOOLS

        expected_discovery = {
            "get_adcp_capabilities",
            "get_products",
            "list_creative_formats",
            "list_authorized_properties",
        }
        assert expected_discovery.issubset(AUTH_OPTIONAL_TOOLS), (
            f"AUTH_OPTIONAL_TOOLS missing discovery tools: {expected_discovery - AUTH_OPTIONAL_TOOLS}"
        )


class TestMCPAuthMiddlewareBehavior:
    """Verify middleware resolves identity and stores on context state."""

    @pytest.fixture
    def middleware(self):
        from src.core.mcp_auth_middleware import MCPAuthMiddleware

        return MCPAuthMiddleware()

    @pytest.fixture
    def mock_context(self):
        """Create a mock MiddlewareContext with fastmcp_context."""
        fastmcp_ctx = MagicMock()
        state_store = {}

        async def set_state(key, value, *, serializable=True):
            state_store[key] = value

        async def get_state(key):
            return state_store.get(key)

        fastmcp_ctx.set_state = set_state
        fastmcp_ctx.get_state = get_state
        fastmcp_ctx._state_store = state_store

        ctx = MagicMock()
        ctx.fastmcp_context = fastmcp_ctx
        return ctx

    @pytest.mark.asyncio
    async def test_auth_required_tool_stores_identity(self, middleware, mock_context):
        """Auth-required tool: middleware resolves identity and stores on state."""
        mock_context.message = MagicMock()
        mock_context.message.name = "create_media_buy"  # auth-required

        mock_identity = MagicMock(spec=ResolvedIdentity)
        call_next = AsyncMock(return_value=MagicMock())

        with patch(
            "src.core.mcp_auth_middleware.resolve_identity_from_context",
            return_value=mock_identity,
        ) as mock_resolve:
            await middleware.on_call_tool(mock_context, call_next)

            # Middleware called resolve with require_valid_token=True
            mock_resolve.assert_called_once()
            call_kwargs = mock_resolve.call_args
            assert call_kwargs[1].get("require_valid_token") is True or (
                len(call_kwargs[0]) >= 2 and call_kwargs[0][1] is True
            )

        # Identity stored on state
        assert mock_context.fastmcp_context._state_store.get("identity") is mock_identity
        # Tool was called
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_discovery_tool_stores_identity_without_requiring_auth(self, middleware, mock_context):
        """Discovery tool: middleware resolves identity with require_valid_token=False."""
        mock_context.message = MagicMock()
        mock_context.message.name = "get_products"  # discovery/auth-optional

        mock_identity = MagicMock(spec=ResolvedIdentity)
        call_next = AsyncMock(return_value=MagicMock())

        with patch(
            "src.core.mcp_auth_middleware.resolve_identity_from_context",
            return_value=mock_identity,
        ) as mock_resolve:
            await middleware.on_call_tool(mock_context, call_next)

            mock_resolve.assert_called_once()
            call_kwargs = mock_resolve.call_args
            # require_valid_token should be False for discovery tools
            assert call_kwargs[1].get("require_valid_token") is False

        assert mock_context.fastmcp_context._state_store.get("identity") is mock_identity

    @pytest.mark.asyncio
    async def test_auth_failure_raises_before_tool_runs(self, middleware, mock_context):
        """Auth-required tool with invalid token: error before tool body."""
        from src.core.exceptions import AdCPAuthenticationError

        mock_context.message = MagicMock()
        mock_context.message.name = "create_media_buy"

        call_next = AsyncMock()

        with patch(
            "src.core.mcp_auth_middleware.resolve_identity_from_context",
            side_effect=AdCPAuthenticationError("Invalid token"),
        ):
            with pytest.raises(AdCPAuthenticationError):
                await middleware.on_call_tool(mock_context, call_next)

        # Tool was NOT called
        call_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_context_id_extracted_from_headers(self, middleware, mock_context):
        """x-context-id extracted from headers and stored on state."""
        mock_context.message = MagicMock()
        mock_context.message.name = "create_media_buy"

        mock_identity = MagicMock(spec=ResolvedIdentity)
        call_next = AsyncMock(return_value=MagicMock())

        with (
            patch(
                "src.core.mcp_auth_middleware.resolve_identity_from_context",
                return_value=mock_identity,
            ),
            patch(
                "src.core.mcp_auth_middleware.get_http_headers",
                return_value={"x-context-id": "test-ctx-123", "x-adcp-auth": "token"},
            ),
        ):
            await middleware.on_call_tool(mock_context, call_next)

        assert mock_context.fastmcp_context._state_store.get("context_id") == "test-ctx-123"


class TestMCPServerMiddlewareRegistration:
    """Verify middleware is registered on the MCP server."""

    def test_mcp_server_has_middleware_registered(self):
        """main.py must call mcp.add_middleware(MCPAuthMiddleware())."""
        source = Path("src/core/main.py").read_text()
        assert "add_middleware" in source, "main.py must register middleware via add_middleware()"
        assert "MCPAuthMiddleware" in source, "main.py must use MCPAuthMiddleware"


class TestGetMediaBuysImplRefactored:
    """get_media_buys _impl must accept ResolvedIdentity, not raw ctx."""

    def test_get_media_buys_impl_accepts_identity_parameter(self):
        """_get_media_buys_impl must accept identity: ResolvedIdentity parameter.

        The legacy pattern passes ctx to _impl which resolves identity inside.
        After refactoring, _impl should receive pre-resolved identity like all other tools.
        """
        import inspect

        from src.core.tools.media_buy_list import _get_media_buys_impl

        sig = inspect.signature(_get_media_buys_impl)
        params = list(sig.parameters.keys())
        assert "identity" in params, (
            f"_get_media_buys_impl must accept 'identity' parameter. "
            f"Current params: {params}. Refactor to receive ResolvedIdentity instead of ctx."
        )


class TestToolsDoNotCallResolveIdentityDirectly:
    """After middleware is in place, MCP tool wrappers should read from context state,
    not call resolve_identity_from_context() directly."""

    # MCP tool wrapper functions (registered in main.py:300-313)
    MCP_TOOL_WRAPPERS = {
        "get_adcp_capabilities": "src/core/tools/capabilities.py",
        "get_products": "src/core/tools/products.py",
        "list_creative_formats": "src/core/tools/creative_formats.py",
        "sync_creatives": "src/core/tools/creatives/sync_wrappers.py",
        "list_creatives": "src/core/tools/creatives/listing.py",
        "list_authorized_properties": "src/core/tools/properties.py",
        "create_media_buy": "src/core/tools/media_buy_create.py",
        "update_media_buy": "src/core/tools/media_buy_update.py",
        "get_media_buy_delivery": "src/core/tools/media_buy_delivery.py",
        "get_media_buys": "src/core/tools/media_buy_list.py",
        "update_performance_index": "src/core/tools/performance.py",
        "list_tasks": "src/core/tools/task_management.py",
        "get_task": "src/core/tools/task_management.py",
        "complete_task": "src/core/tools/task_management.py",
    }

    def _get_function_body_calls(self, filepath: str, func_name: str) -> list[str]:
        """Extract function call names from a specific function's body using AST."""
        source = Path(filepath).read_text()
        tree = ast.parse(source)

        calls = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == func_name:
                    for child in ast.walk(node):
                        if isinstance(child, ast.Call):
                            if isinstance(child.func, ast.Name):
                                calls.append(child.func.id)
                            elif isinstance(child.func, ast.Attribute):
                                calls.append(child.func.attr)
        return calls

    @pytest.mark.parametrize(
        "tool_name,filepath",
        [
            ("get_adcp_capabilities", "src/core/tools/capabilities.py"),
            ("get_products", "src/core/tools/products.py"),
            ("list_creative_formats", "src/core/tools/creative_formats.py"),
            ("sync_creatives", "src/core/tools/creatives/sync_wrappers.py"),
            ("list_creatives", "src/core/tools/creatives/listing.py"),
            ("list_authorized_properties", "src/core/tools/properties.py"),
            ("create_media_buy", "src/core/tools/media_buy_create.py"),
            ("update_media_buy", "src/core/tools/media_buy_update.py"),
            ("get_media_buy_delivery", "src/core/tools/media_buy_delivery.py"),
            ("get_media_buys", "src/core/tools/media_buy_list.py"),
            ("update_performance_index", "src/core/tools/performance.py"),
            ("list_tasks", "src/core/tools/task_management.py"),
            ("get_task", "src/core/tools/task_management.py"),
            ("complete_task", "src/core/tools/task_management.py"),
        ],
    )
    def test_mcp_wrapper_does_not_call_resolve_identity(self, tool_name, filepath):
        """MCP tool wrapper must NOT call resolve_identity_from_context() directly.

        After the middleware, identity is read from ctx.get_state('identity').
        """
        calls = self._get_function_body_calls(filepath, tool_name)
        assert "resolve_identity_from_context" not in calls, (
            f"MCP wrapper {tool_name} in {filepath} still calls resolve_identity_from_context(). "
            "It should read identity from ctx.get_state('identity') instead."
        )
