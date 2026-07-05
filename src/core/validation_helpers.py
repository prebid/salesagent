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


# Cross-mode buying_mode validator messages -> actionable buyer suggestions.
# The substring keys mirror the ValueError messages raised by
# GetProductsRequest._validate_buying_mode_invariants. A coupling test
# (test_get_products_buying_mode) pins every validator message to a non-None
# suggestion so a reworded message can't silently drop the wire `suggestion`.
_BUYING_MODE_SUGGESTIONS: tuple[tuple[str, str], ...] = (
    (
        "got None",
        "Provide buying_mode='brief' with a brief, 'wholesale' for raw inventory, or 'refine' with a refine array.",
    ),
    (
        "buying_mode must be one of",
        "Use buying_mode='brief', 'wholesale', or 'refine'.",
    ),
    (
        "brief is required when buying_mode is 'brief'",
        "Provide a brief describing your campaign requirements, or use buying_mode='wholesale' for raw inventory.",
    ),
    (
        "refine must not be provided when buying_mode is 'brief'",
        "Remove refine, or use buying_mode='refine' to iterate on a previous response.",
    ),
    (
        "brief must not be provided when buying_mode is 'wholesale'",
        "Remove brief, or use buying_mode='brief' to discover via a brief.",
    ),
    (
        "refine must not be provided when buying_mode is 'wholesale'",
        "Remove refine, or use buying_mode='refine' to iterate on a previous response.",
    ),
    (
        "brief must not be provided when buying_mode is 'refine'",
        "Remove brief, or use buying_mode='brief' to discover via a brief.",
    ),
    (
        "refine array is required when buying_mode is 'refine'",
        "Provide a refine array with at least one entry, or use a different buying_mode.",
    ),
)


def extract_buying_mode_suggestion(error: ValidationError) -> str | None:
    """Map a cross-mode buying_mode validator violation to an actionable suggestion.

    Pydantic v2 wraps ``raise ValueError("...")`` from a model_validator as
    ``"Value error, ..."`` — substring matching tolerates the prefix.

    Also handles the library-level rejection of an absent/None ``buying_mode`` (type
    "enum"/"literal_error" with input None, or "missing" when omitted) — those fire
    BEFORE the model_validator, so the custom "got None" message is unreachable; detect
    them structurally and return the same suggestion.

    Returns None when no known pattern matches so callers fall back to a generic
    suggestion or omit the field.
    """
    for err in error.errors():
        etype = err.get("type")
        loc_has_buying_mode = any("buying_mode" in str(part) for part in err.get("loc", ()))
        if loc_has_buying_mode and (
            etype == "missing" or (etype in ("enum", "literal_error") and err.get("input") is None)
        ):
            return _BUYING_MODE_SUGGESTIONS[0][1]

        msg = str(err.get("msg", ""))
        for pattern, suggestion in _BUYING_MODE_SUGGESTIONS:
            if pattern in msg:
                return suggestion
    return None


@contextmanager
def adcp_validation_boundary() -> Iterator[None]:
    """Translate a Pydantic ``ValidationError`` into a typed ``AdCPValidationError``.

    A2A skill handlers validate buyer parameters at the transport boundary. A raw
    ``ValidationError`` leaking from ``model_validate`` (or a typed-model constructor)
    would surface as an untyped error — and the outer dispatcher only builds the
    two-layer error envelope for ``AdCPError`` subclasses, so the buyer would lose
    the real code/recovery. Wrapping the construction in this boundary gives every
    handler the same ``Invalid parameters: ...`` message plus a structured ``field``
    path, instead of a hand-copied try/except per handler.
    """
    try:
        yield
    except ValidationError as e:
        raise AdCPValidationError(f"Invalid parameters: {e}", field=first_validation_error_field(e)) from e


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


def package_field_path(attr: str, index: int | None = None) -> str:
    """Bracket-notation field path for a per-package field in an _impl-layer error.

    Mirrors the list notation of :func:`first_validation_error_field`. Without an
    index, the _impl layer validates the package collection as a whole and raises
    ``packages[].budget`` / ``packages[].package_id``; with an index, per-package
    checks name the offending entry (``packages[0].targeting_overlay.property_list``).
    Centralizing the prefix here stops the hand-rolled literals from drifting apart.
    """
    bracket = "" if index is None else index
    return f"packages[{bracket}].{attr}"


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
