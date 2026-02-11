"""Validation and utility helper functions for AdCP request processing.

This module provides validation, JSON parsing, and async/sync context handling utilities
specifically for AdCP protocol request/response processing in main.py.
"""

import asyncio
import concurrent.futures
import json
import logging

from pydantic import ValidationError

logger = logging.getLogger(__name__)


def run_async_in_sync_context(coroutine):
    """
    Helper to run async coroutines from sync code, handling event loop conflicts.

    This is needed when calling async functions from sync code that may be called
    from an async context (like FastMCP tools). It detects if there's already a
    running event loop and uses a thread pool to avoid "asyncio.run() cannot be
    called from a running event loop" errors.

    Args:
        coroutine: The async coroutine to run

    Returns:
        The result of the coroutine
    """
    # Check if coroutine is actually a coroutine object
    if not asyncio.iscoroutine(coroutine):
        raise TypeError(f"Expected coroutine, got {type(coroutine)}")

    try:
        # Check if there's already a running event loop
        asyncio.get_running_loop()

        # We're in an async context, run in thread pool to avoid nested loop error
        # Create a new event loop in the thread to run the coroutine
        def run_in_thread():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(coroutine)
            finally:
                loop.close()

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_in_thread)
            return future.result()
    except RuntimeError:
        # No running loop, safe to create one
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coroutine)
        finally:
            loop.close()


def safe_parse_json_field(field_value, field_name="field", default=None):
    """
    Safely parse a database field that might be JSON string (SQLite) or dict (PostgreSQL JSONB).

    Args:
        field_value: The field value from database (could be str, dict, None, etc.)
        field_name: Name of the field for logging purposes
        default: Default value to return on parse failure (default: None)

    Returns:
        Parsed dict/list or default value
    """
    if not field_value:
        return default if default is not None else {}

    if isinstance(field_value, str):
        try:
            parsed = json.loads(field_value)
            # Validate the parsed result is the expected type
            if default is not None and not isinstance(parsed, type(default)):
                logger.warning(f"Parsed {field_name} has unexpected type: {type(parsed)}, expected {type(default)}")
                return default
            return parsed
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Invalid JSON in {field_name}: {e}")
            return default if default is not None else {}
    elif isinstance(field_value, dict | list):
        return field_value
    else:
        logger.warning(f"Unexpected type for {field_name}: {type(field_value)}")
        return default if default is not None else {}


def format_validation_error(validation_error: ValidationError, context: str = "request") -> str:
    """Format Pydantic ValidationError with helpful context for clients.

    Provides clear, actionable error messages that reference the AdCP spec
    and explain what went wrong with field types.

    Args:
        validation_error: The Pydantic ValidationError to format
        context: Context string for the error message (e.g., "request", "creative")

    Returns:
        Formatted error message string suitable for client consumption

    Example:
        >>> try:
        ...     req = CreateMediaBuyRequest(brand_manifest={"target_audience": {}})
        ... except ValidationError as e:
        ...     raise ToolError(format_validation_error(e))
    """
    error_details = []
    for error in validation_error.errors():
        field_path = ".".join(str(loc) for loc in error["loc"])
        error_type = error["type"]
        msg = error["msg"]
        input_val = error.get("input")

        # Add helpful context for common validation errors
        if "string_type" in error_type and isinstance(input_val, dict):
            error_details.append(
                f"  • {field_path}: Expected string, got object. "
                f"AdCP spec requires this field to be a simple string, not a structured object."
            )
        elif "string_type" in error_type:
            error_details.append(
                f"  • {field_path}: Expected string, got {type(input_val).__name__}. Please provide a string value."
            )
        elif "missing" in error_type:
            error_details.append(f"  • {field_path}: Required field is missing")
        elif "extra_forbidden" in error_type:
            # For extra_forbidden, show the actual value to help debug what was passed
            if input_val is not None:
                # Format the input value more verbosely for debugging
                try:
                    input_repr = json.dumps(input_val, indent=2, default=str)
                except (TypeError, ValueError):
                    input_repr = repr(input_val)
                error_details.append(
                    f"  • {field_path}: Extra field not allowed by AdCP spec.\n" f"    Received value: {input_repr}"
                )
            else:
                error_details.append(f"  • {field_path}: Extra field not allowed by AdCP spec")
        else:
            error_details.append(f"  • {field_path}: {msg}")

    error_msg = (
        f"Invalid {context}: The following fields do not match the AdCP specification:\n\n"
        + "\n".join(error_details)
        + "\n\nPlease check the AdCP spec at https://adcontextprotocol.org/schemas/v1/ for correct field types."
    )

    return error_msg
