"""Validation and utility helper functions for AdCP request processing.

This module provides validation, JSON parsing, and async/sync context handling utilities
specifically for AdCP protocol request/response processing in main.py.
"""

import asyncio
import concurrent.futures
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager

from pydantic import ValidationError

from src.core.exceptions import AdCPValidationError

logger = logging.getLogger(__name__)


@contextmanager
def adcp_validation_boundary(context: str = "parameters", field: str | None = None) -> Iterator[None]:
    """Translate a Pydantic ``ValidationError`` into a typed ``AdCPValidationError``.

    Transport wrappers and skill handlers validate buyer parameters at the
    boundary. A raw ``ValidationError`` leaking from ``model_validate`` (or a
    typed-model constructor) would surface as an untyped error — and the outer
    dispatcher only builds the two-layer error envelope for ``AdCPError``
    subclasses, so the buyer would lose the real code/recovery. This boundary is
    the SINGLE translation point (salesagent-ah98): every rejection carries the
    buyer-friendly ``format_validation_error`` message, the structured ``field``
    path, and error.json's top-level ``suggestion`` — no tool hand-rolls its own
    try/except copy.

    ``context`` names what was invalid in the message (e.g. ``"get_products
    request"``); the default renders the ``Invalid parameters`` prefix existing
    wire assertions rely on. ``field`` pins the request-level field path when the
    validated model is nested below it (e.g. ``field="brand"`` while validating a
    ``BrandReference``); by default the path is derived from the error itself.
    """
    try:
        yield
    except ValidationError as e:
        raise AdCPValidationError(
            format_validation_error(e, context=context),
            field=field if field is not None else first_validation_error_field(e),
            suggestion=suggest_validation_fix(e),
        ) from e


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

    # Loop DETECTION only inside this try. The coroutine must execute OUTSIDE
    # it: a RuntimeError raised BY the coroutine (e.g. httpx/anyio "Event loop
    # is closed") re-raised out of future.result() would otherwise be misread
    # as "no running loop" and the already-CONSUMED coroutine re-run on a fresh
    # loop — mangling the real error into "cannot reuse already awaited
    # coroutine" (salesagent-mpo1).
    try:
        asyncio.get_running_loop()
        in_async_context = True
    except RuntimeError:
        in_async_context = False

    if in_async_context:
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

    # No running loop, safe to create one
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coroutine)
    finally:
        loop.close()


def safe_parse_json_field(field_value, field_name="field", default=None):
    """
    Safely parse a database field that might be a JSON string or already-deserialized dict (JSONB).

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


def first_validation_error_field(validation_error: ValidationError) -> str | None:
    """Return the bracket-notation field path of the first Pydantic error, or ``None``.

    Lets a transport boundary attach a structured ``field`` to the
    ``AdCPValidationError`` it raises, so the wire envelope carries the offending
    field path (e.g. ``packages[0].budget``) instead of only the rendered message.
    List indices render as ``[i]`` so the boundary-derived path matches the
    hand-rolled ``field=`` strings raised inside the _impl layer (``packages[].budget``).
    """
    errors = validation_error.errors()
    if not errors:
        return None
    parts: list[str] = []
    for loc in errors[0]["loc"]:
        if isinstance(loc, int):
            parts.append(f"[{loc}]")
        elif parts:
            parts.append(f".{loc}")
        else:
            parts.append(str(loc))
    return "".join(parts)


def package_field_path(attr: str) -> str:
    """Bracket-notation field path for a per-package field in an _impl-layer error.

    Mirrors the list notation of :func:`first_validation_error_field` but without a
    concrete index: the _impl layer validates the package collection as a whole and
    raises ``packages[].budget`` / ``packages[].package_id`` / ``packages[].product_id``,
    while the boundary-derived path carries the offending index (``packages[0].budget``).
    Centralizing the prefix here stops the hand-rolled literals from drifting apart.
    """
    return f"packages[].{attr}"


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
        ...     req = CreateMediaBuyRequest(brand={"domain": "example.com"})
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
                    f"  • {field_path}: Extra field not allowed by AdCP spec.\n    Received value: {input_repr}"
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


def suggest_validation_fix(validation_error: ValidationError) -> str:
    """Derive a single buyer-facing correction hint from a Pydantic ValidationError.

    Produces the actionable ``suggestion`` companion to
    ``format_validation_error``'s diagnostic message, so request-validation
    rejections carry a non-empty wire ``suggestion`` (AdCP POST-F3: the buyer
    must learn how to fix the request). The hint names the offending field(s)
    and the corrective action, keyed off the Pydantic error ``type``:

    * ``missing``        → provide the required field
    * ``string_pattern_mismatch`` / ``string_too_short`` / ``string_too_long`` → fix the value to satisfy the constraint
    * ``extra_forbidden`` → remove the unrecognized field
    * anything else      → correct the field per the AdCP spec
    """
    errors = validation_error.errors()
    if not errors:
        return "Correct the request to match the AdCP specification and resend."

    first = errors[0]
    field_path = ".".join(str(loc) for loc in first.get("loc", ())) or "request"
    error_type = first.get("type", "")

    if "missing" in error_type:
        return f"Provide the required '{field_path}' field and resend the request."
    if "extra_forbidden" in error_type:
        return f"Remove the unrecognized '{field_path}' field; it is not part of the AdCP request schema."
    if error_type.startswith("string_pattern_mismatch") or "too_short" in error_type or "too_long" in error_type:
        return f"Provide a valid '{field_path}' value that satisfies the AdCP field constraints and resend."
    return f"Correct the '{field_path}' field to match the AdCP specification and resend."
