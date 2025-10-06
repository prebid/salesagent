"""MCP context wrapper for automatic context management.

This module provides middleware-like functionality to automatically handle
context for MCP tools, similar to how A2A manages context for its handlers.
"""

import functools
import inspect
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from fastmcp.server import Context as FastMCPContext
from pydantic import BaseModel
from rich.console import Console

from src.core.auth_utils import get_principal_from_context
from src.core.config_loader import get_current_tenant
from src.core.context_manager import get_context_manager
from src.core.testing_hooks import get_testing_context
from src.core.tool_context import ToolContext

console = Console()


@runtime_checkable
class SyncMCPTool(Protocol):
    """Protocol for synchronous MCP tool functions."""

    def __call__(self, *args, context: "ToolContext", **kwargs) -> Any:
        """Tool function that takes a ToolContext and returns a result."""
        ...


@runtime_checkable
class AsyncMCPTool(Protocol):
    """Protocol for asynchronous MCP tool functions."""

    def __call__(self, *args, context: "ToolContext", **kwargs) -> Awaitable[Any]:
        """Async tool function that takes a ToolContext and returns an awaitable result."""
        ...


class MCPContextWrapper:
    """Wrapper to automatically manage context for MCP tools.

    This class handles:
    - Context extraction from FastMCP
    - Context persistence with database
    - Conversation history management
    - Response enhancement with context_id
    """

    def __init__(self):
        """Initialize the context wrapper."""
        self.context_manager = get_context_manager()

    def wrap_tool(self, tool_func: SyncMCPTool | AsyncMCPTool) -> Callable:
        """Wrap an MCP tool to automatically handle context.

        Args:
            tool_func: The tool function to wrap

        Returns:
            Wrapped function with automatic context management
        """
        # Check if function is async
        is_async = inspect.iscoroutinefunction(tool_func)

        if is_async:
            return self._wrap_async_tool(tool_func)
        else:
            return self._wrap_sync_tool(tool_func)

    def _wrap_async_tool(self, tool_func: AsyncMCPTool) -> Callable:
        """Wrap an async MCP tool."""

        @functools.wraps(tool_func)
        async def wrapper(*args, **kwargs):
            # Extract FastMCP context from arguments
            fastmcp_context = self._extract_fastmcp_context(args, kwargs)
            if not fastmcp_context:
                # No context found, call original function
                return await tool_func(*args, **kwargs)

            # Create ToolContext
            tool_context = self._create_tool_context(fastmcp_context, tool_func.__name__)

            # Replace FastMCP context with ToolContext in arguments
            args, kwargs = self._replace_context_in_args(args, kwargs, tool_context)

            # Track start time
            start_time = time.time()

            try:
                # Call the original function with ToolContext
                result = await tool_func(*args, **kwargs)

                # Update conversation history
                self._update_conversation_history(tool_context, result)

                # Enhance response with context_id at protocol layer
                result = self._enhance_response(result, tool_context)

                return result

            finally:
                # Log activity
                elapsed = time.time() - start_time
                console.print(f"[dim]Tool {tool_func.__name__} completed in {elapsed:.2f}s[/dim]")

        return wrapper

    def _wrap_sync_tool(self, tool_func: SyncMCPTool) -> Callable:
        """Wrap a sync MCP tool."""

        @functools.wraps(tool_func)
        def wrapper(*args, **kwargs):
            # Extract FastMCP context from arguments
            fastmcp_context = self._extract_fastmcp_context(args, kwargs)
            if not fastmcp_context:
                # No context found, call original function
                return tool_func(*args, **kwargs)

            # Create ToolContext
            tool_context = self._create_tool_context(fastmcp_context, tool_func.__name__)

            # Replace FastMCP context with ToolContext in arguments
            args, kwargs = self._replace_context_in_args(args, kwargs, tool_context)

            # Track start time
            start_time = time.time()

            try:
                # Call the original function with ToolContext
                result = tool_func(*args, **kwargs)

                # Update conversation history
                self._update_conversation_history(tool_context, result)

                # Enhance response with context_id at protocol layer
                result = self._enhance_response(result, tool_context)

                return result

            finally:
                # Log activity
                elapsed = time.time() - start_time
                console.print(f"[dim]Tool {tool_func.__name__} completed in {elapsed:.2f}s[/dim]")

        return wrapper

    def _extract_fastmcp_context(self, args: tuple, kwargs: dict) -> FastMCPContext | None:
        """Extract FastMCP Context from function arguments."""
        # Check kwargs first
        if "context" in kwargs and isinstance(kwargs["context"], FastMCPContext):
            return kwargs["context"]

        # Check positional args
        for arg in args:
            if isinstance(arg, FastMCPContext):
                return arg

        return None

    def _create_tool_context(self, fastmcp_context: FastMCPContext, tool_name: str) -> ToolContext:
        """Create a ToolContext from FastMCP context.

        Args:
            fastmcp_context: The FastMCP context object
            tool_name: Name of the tool being called

        Returns:
            A populated ToolContext object
        """
        # Get authentication info
        principal_id = get_principal_from_context(fastmcp_context)

        # Extract headers for debugging
        headers = fastmcp_context.meta.get("headers", {}) if hasattr(fastmcp_context, "meta") else {}
        auth_header = headers.get("x-adcp-auth", "NOT_PRESENT")
        apx_host = headers.get("apx-incoming-host", "NOT_PRESENT")

        if not principal_id:
            # Determine if header is missing or just invalid
            if auth_header == "NOT_PRESENT":
                raise ValueError(f"Missing x-adcp-auth header. " f"Apx-Incoming-Host: {apx_host}")
            else:
                # Header present but invalid (token not found in DB)
                raise ValueError(
                    f"Invalid x-adcp-auth token (not found in database). "
                    f"Token: {auth_header[:20]}..., "
                    f"Apx-Incoming-Host: {apx_host}"
                )

        # Get tenant info
        tenant = get_current_tenant()
        if not tenant:
            raise ValueError(
                f"No tenant context available. " f"Principal: {principal_id}, " f"Apx-Incoming-Host: {apx_host}"
            )

        # Extract or generate context_id
        headers = fastmcp_context.meta.get("headers", {}) if hasattr(fastmcp_context, "meta") else {}
        context_id = headers.get("x-context-id")

        # Determine if this is an async operation
        is_async = headers.get("x-async-operation") == "true"

        # Get or create persistent context if needed
        persistent_context = None
        conversation_history = []
        workflow_id = None

        if context_id or is_async:
            # Need persistent context
            if not context_id:
                context_id = f"ctx_{uuid.uuid4().hex[:12]}"

            persistent_context = self.context_manager.get_or_create_context(
                tenant_id=tenant["tenant_id"], principal_id=principal_id, context_id=context_id, is_async=is_async
            )

            if persistent_context:
                conversation_history = persistent_context.conversation_history or []
                # Check if there's an associated workflow
                # This would need to be implemented based on your workflow tracking
                workflow_id = None  # TODO: Get from workflow mappings if exists
        else:
            # Synchronous operation without context
            context_id = f"ctx_{uuid.uuid4().hex[:12]}"

        # Extract testing context and convert to dict
        testing_context_obj = get_testing_context(fastmcp_context)
        testing_context = testing_context_obj.model_dump() if testing_context_obj else None

        # Build metadata
        metadata = {
            "headers": headers,
            "is_async": is_async,
        }

        return ToolContext(
            context_id=context_id,
            tenant_id=tenant["tenant_id"],
            principal_id=principal_id,
            conversation_history=conversation_history,
            tool_name=tool_name,
            request_timestamp=datetime.now(UTC),
            metadata=metadata,
            testing_context=testing_context,
            workflow_id=workflow_id,
        )

    def _replace_context_in_args(self, args: tuple, kwargs: dict, tool_context: ToolContext) -> tuple[tuple, dict]:
        """Replace FastMCP Context with ToolContext in arguments."""
        # Replace in kwargs
        if "context" in kwargs:
            kwargs = {**kwargs, "context": tool_context}

        # Replace in positional args
        new_args = []
        for arg in args:
            if isinstance(arg, FastMCPContext):
                new_args.append(tool_context)
            else:
                new_args.append(arg)

        return tuple(new_args), kwargs

    def _update_conversation_history(self, tool_context: ToolContext, result: Any) -> None:
        """Update conversation history with the result."""
        if not tool_context.is_async_operation():
            # Don't persist for sync operations
            return

        # Add to conversation history
        message = {
            "tool": tool_context.tool_name,
            "type": "response",
            "content": self._serialize_result(result),
        }
        tool_context.add_to_history(message)

        # Persist if we have a persistent context
        if tool_context.context_id:
            # Update in database
            # This would need to be implemented based on your persistence layer
            pass

    def _serialize_result(self, result: Any) -> dict:
        """Serialize a result for storage in conversation history."""
        if isinstance(result, BaseModel):
            return result.model_dump()
        elif isinstance(result, dict):
            return result
        else:
            return {"value": str(result)}

    def _enhance_response(self, result: Any, tool_context: ToolContext) -> Any:
        """Enhance response with context_id at protocol layer.

        Instead of modifying the response object itself, we ensure
        the context_id is available for the protocol layer to add.
        """
        # For now, just return the result as-is
        # The protocol layer (FastMCP) should handle adding context_id
        # This is where we'd integrate with FastMCP's response handling

        # Store context_id in a way the protocol layer can access it
        if hasattr(result, "__dict__"):
            # Add as a non-serialized attribute that protocol can read
            result._mcp_context_id = tool_context.context_id

        return result


# Global wrapper instance
_wrapper = MCPContextWrapper()


def with_context(tool_func: SyncMCPTool | AsyncMCPTool) -> Callable:
    """Decorator to add automatic context management to an MCP tool.

    Usage:
        @mcp.tool
        @with_context
        async def my_tool(req: MyRequest, context: ToolContext) -> MyResponse:
            # Tool implementation using ToolContext
            pass
    """
    return _wrapper.wrap_tool(tool_func)
