"""Tests for the new automatic context management system."""

from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pytest
from fastmcp.server import Context as FastMCPContext
from pydantic import BaseModel

from src.core.mcp_context_wrapper import MCPContextWrapper
from src.core.resolved_identity import ResolvedIdentity
from src.core.tool_context import ToolContext


class RequestModel(BaseModel):
    """Test request model."""

    query: str


class ResponseModel(BaseModel):
    """Test response model."""

    result: str
    message: str | None = None


class MockSetup:
    """Centralized mock setup to reduce duplicate mocking."""

    @staticmethod
    def create_fastmcp_context():
        """Create a mock FastMCP context with standard test data."""
        context = Mock(spec=FastMCPContext)
        headers = {
            "x-adcp-auth": "test_token_123",
            "x-context-id": "ctx_test123",
            "X-Test-Session-ID": "test_session_123",
            "X-Force-Error": "none",
        }

        # FastMCP Context uses meta dict to store headers
        context.meta = {"headers": headers}

        return context

    @staticmethod
    def get_test_data():
        """Get standard test data objects."""
        mock_context_manager = Mock()
        mock_context_manager.get_or_create_context.return_value = None

        return {
            "tenant": {"tenant_id": "tenant_test", "name": "Test Tenant"},
            "principal_id": "principal_test",
            "context_manager": mock_context_manager,
            "identity": ResolvedIdentity(
                principal_id="principal_test",
                tenant_id="tenant_test",
                tenant={"tenant_id": "tenant_test", "name": "Test Tenant"},
                protocol="mcp",
            ),
        }


class TestToolContext:
    """Test the ToolContext class."""

    def test_tool_context_creation(self):
        """Test creating a ToolContext."""
        context = ToolContext(
            context_id="ctx_123",
            tenant_id="tenant_123",
            principal_id="principal_123",
            tool_name="test_tool",
            request_timestamp=datetime.now(UTC),
            conversation_history=[],
            metadata={"test": "data"},
        )

        assert context.context_id == "ctx_123"
        assert context.tenant_id == "tenant_123"
        assert context.principal_id == "principal_123"
        assert context.tool_name == "test_tool"
        assert context.metadata["test"] == "data"

    def test_is_async_operation(self):
        """Test checking if operation is async."""
        # Sync context (no workflow)
        sync_context = ToolContext(
            context_id="ctx_123",
            tenant_id="tenant_123",
            principal_id="principal_123",
            tool_name="test_tool",
            request_timestamp=datetime.now(UTC),
        )
        assert not sync_context.is_async_operation()

        # Async context (with workflow)
        async_context = ToolContext(
            context_id="ctx_123",
            tenant_id="tenant_123",
            principal_id="principal_123",
            tool_name="test_tool",
            request_timestamp=datetime.now(UTC),
            workflow_id="wf_123",
        )
        assert async_context.is_async_operation()

    def test_add_to_history(self):
        """Test adding messages to conversation history."""
        context = ToolContext(
            context_id="ctx_123",
            tenant_id="tenant_123",
            principal_id="principal_123",
            tool_name="test_tool",
            request_timestamp=datetime.now(UTC),
        )

        # Add a message
        context.add_to_history({"type": "request", "content": "test message"})

        assert len(context.conversation_history) == 1
        assert context.conversation_history[0]["type"] == "request"
        assert context.conversation_history[0]["content"] == "test message"
        assert "timestamp" in context.conversation_history[0]


@patch("src.core.transport_helpers.resolve_identity_from_context")
@patch("src.core.config_loader.set_current_tenant")
@patch("src.core.mcp_context_wrapper.get_context_manager")
class TestMCPContextWrapper:
    """Test the MCP context wrapper with consolidated mocking."""

    def setup_method(self):
        """Set up test data and mocks."""
        self.mock_setup = MockSetup()
        self.fastmcp_context = self.mock_setup.create_fastmcp_context()
        self.test_data = self.mock_setup.get_test_data()

    def test_sync_tool_wrapping(
        self,
        mock_get_context_manager,
        mock_set_tenant,
        mock_resolve_identity,
    ):
        """Test wrapping a synchronous tool."""
        mock_resolve_identity.return_value = self.test_data["identity"]
        mock_get_context_manager.return_value = self.test_data["context_manager"]

        wrapper = MCPContextWrapper()

        def test_tool(req: RequestModel, context: ToolContext) -> ResponseModel:
            assert isinstance(context, ToolContext)
            assert context.principal_id == "principal_test"
            assert context.tenant_id == "tenant_test"
            return ResponseModel(result=f"Processed: {req.query}")

        wrapped_tool = wrapper.wrap_tool(test_tool)
        request = RequestModel(query="test query")
        result = wrapped_tool(request, context=self.fastmcp_context)

        assert isinstance(result, ResponseModel)
        assert result.result == "Processed: test query"

    async def test_async_tool_wrapping(
        self,
        mock_get_context_manager,
        mock_set_tenant,
        mock_resolve_identity,
    ):
        """Test wrapping an asynchronous tool."""
        mock_resolve_identity.return_value = self.test_data["identity"]
        mock_get_context_manager.return_value = self.test_data["context_manager"]

        wrapper = MCPContextWrapper()

        async def async_test_tool(req: RequestModel, context: ToolContext) -> ResponseModel:
            assert isinstance(context, ToolContext)
            assert context.principal_id == "principal_test"
            assert context.tenant_id == "tenant_test"
            assert context.context_id == "ctx_test123"
            return ResponseModel(result=f"Async processed: {req.query}")

        wrapped_tool = wrapper.wrap_tool(async_test_tool)
        request = RequestModel(query="async test query")
        result = await wrapped_tool(request, context=self.fastmcp_context)

        assert isinstance(result, ResponseModel)
        assert result.result == "Async processed: async test query"

    def test_context_extraction(
        self,
        mock_get_context_manager,
        mock_set_tenant,
        mock_resolve_identity,
    ):
        """Test extracting context from FastMCP context."""
        wrapper = MCPContextWrapper()

        # Test extraction patterns
        context = wrapper._extract_fastmcp_context((), {"context": self.fastmcp_context})
        assert context == self.fastmcp_context

        context = wrapper._extract_fastmcp_context((self.fastmcp_context,), {})
        assert context == self.fastmcp_context

        context = wrapper._extract_fastmcp_context((), {})
        assert context is None

    def test_tool_context_creation(
        self,
        mock_get_context_manager,
        mock_set_tenant,
        mock_resolve_identity,
    ):
        """Test creating ToolContext from FastMCP context."""
        mock_resolve_identity.return_value = self.test_data["identity"]
        mock_context_manager = self.test_data["context_manager"]
        mock_context_manager.get_or_create_context.return_value = None
        mock_get_context_manager.return_value = mock_context_manager

        wrapper = MCPContextWrapper()
        tool_context = wrapper._create_tool_context(self.fastmcp_context, "test_tool")

        assert isinstance(tool_context, ToolContext)
        assert tool_context.context_id == "ctx_test123"
        assert tool_context.tenant_id == "tenant_test"
        assert tool_context.principal_id == "principal_test"
        assert tool_context.tool_name == "test_tool"
        assert tool_context.metadata["headers"]["x-context-id"] == "ctx_test123"

    def test_response_enhancement(
        self,
        mock_get_context_manager,
        mock_set_tenant,
        mock_resolve_identity,
    ):
        """Test that context_id is stored for protocol layer."""
        mock_resolve_identity.return_value = self.test_data["identity"]
        mock_get_context_manager.return_value = self.test_data["context_manager"]

        wrapper = MCPContextWrapper()

        def test_tool(req: RequestModel, context: ToolContext) -> ResponseModel:
            return ResponseModel(result="test")

        wrapped_tool = wrapper.wrap_tool(test_tool)
        request = RequestModel(query="test")
        result = wrapped_tool(request, context=self.fastmcp_context)

        assert hasattr(result, "_mcp_context_id")
        assert result._mcp_context_id == "ctx_test123"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
