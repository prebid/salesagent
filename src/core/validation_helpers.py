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

from src.core.exceptions import (
    adcp_error_for,
)
from src.core.exceptions import (
    first_validation_error_field as first_validation_error_field,
)

logger = logging.getLogger(__name__)


def _qualified_field(error: ValidationError, field_prefix: str | None) -> str | None:
    """The derived field path, optionally qualified by the outer request field."""
    derived = first_validation_error_field(error)
    if field_prefix is None:
        return derived
    return f"{field_prefix}.{derived}" if derived else field_prefix


@contextmanager
def adcp_validation_boundary(
    field: str | None = None,
    field_prefix: str | None = None,
) -> Iterator[None]:
    """Requalify the FIELD a Pydantic ``ValidationError`` reports, then translate it.

    NOT a translation seam. ``adcp_error_for`` is, and every transport boundary
    already calls it: MCP through ``RegistryTool.run``, A2A through ``_dispatch_skill``,
    REST through ``@app.exception_handler(ValueError)`` (a pydantic
    ``ValidationError`` IS a ``ValueError``). A ``ValidationError`` raised anywhere
    inside a handler therefore reaches the buyer as INVALID_REQUEST with ``field``
    and ``issues`` with no wrapper involved, on all three transports: the boundary
    builds one ``AdcpErrorResponse`` from the one typed exception.

    Which is why this used to wrap 48 sites and now wraps 2. Forty-six of them passed
    NO arguments, and a bare block is exactly ``raise adcp_error_for(e, field=None)``
    — the same call the boundary makes one frame later, off the same exception, with
    the same result. Deleting them changed no envelope on any transport. The RULE is:
    populate the DTO, validate, let it throw.

    The two survivors are the two that are not bare, and both do the one job a
    later frame genuinely cannot do — name the field of the document the BUYER sent.
    A model coerced OUTSIDE its parent request has lost that context by the time the
    error is in hand: pydantic's ``loc`` starts at the coerced model's own root, so
    ``to_push_notification_config`` would report ``authentication.schemes[0]`` and
    the brand coercion would report ``domain`` — neither of which is a path into
    what the buyer sent. Coercing the same value AS A DTO FIELD needs no wrapper (the
    loc carries the field), so the honest end state for these two is to stop coercing
    ahead of construction rather than to keep a wrapper. That move is not done.

    ``field`` pins the reported request field when the failing model is nested
    under a named request field: coercing a ``BrandReference`` reports
    ``field="brand"``, not the nested pydantic location (e.g. ``industries``).
    When ``None`` (default) the field is derived from the validation error.

    ``field_prefix`` QUALIFIES the derived location instead of replacing it, for
    models coerced out of a named request field whose INTERNAL path is the useful
    part: a bad scheme inside ``push_notification_config`` should read
    ``push_notification_config.authentication.schemes[0]``, not the bare
    ``authentication.schemes[0]`` — ``error.field`` is a path into the document
    the BUYER sent, and the buyer sent the outer field. Mutually exclusive with
    ``field``, which discards the inner path entirely.
    """
    if field is not None and field_prefix is not None:
        # A validator whose job is refusing quietly-wrong documents must not itself
        # accept a quietly-wrong call: passing both would silently drop the prefix.
        raise ValueError("adcp_validation_boundary takes field= OR field_prefix=, not both")
    try:
        yield
    except ValidationError as e:
        # ``field_prefix`` is resolved HERE rather than inside ``adcp_error_for``:
        # qualifying a derived path is a property of the wrapped BLOCK (which named
        # the outer request field), not of an exception handed over in isolation.
        # With no prefix the derived path is left to ``adcp_error_for`` so the two
        # entry points cannot compute it differently.
        raise adcp_error_for(e, field=field if field_prefix is None else _qualified_field(e, field_prefix)) from e


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
    # coroutine" .
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


#: The array parameter itself, for a failure that is about the COLLECTION rather
#: than one entry (e.g. a total-budget check across all packages). Naming the array
#: is what the spec asks for when no single element is at fault.
PACKAGES_FIELD = "packages"


def package_field_path(attr: str, index: int) -> str:
    """Indexed JSON pointer for a per-package field in an _impl-layer error.

    ``packages[0].budget``, not ``packages[].budget``. The empty-bracket form this
    replaces could not tell a buyer WHICH package was refused, which is the entire
    purpose of the pointer on a multi-package request -- and it matched neither
    shape the pinned contract uses: notification-config-event-scope.yaml grades
    ``field == 'notification_configs[0].event_types[0]'``, an INDEXED pointer, and
    the collection-level form is the bare array name (see :data:`PACKAGES_FIELD`).

    Use this where one entry is at fault and its position is known; use
    ``PACKAGES_FIELD`` where the collection as a whole failed (salesagent-rfxfu).
    """
    return f"packages[{index}].{attr}"


def format_validation_error(validation_error: ValidationError, label: str = "request") -> str:
    """Format Pydantic ValidationError with helpful context for clients.

    Provides clear, actionable error messages that reference the AdCP spec
    and explain what went wrong with field types.

    Args:
        validation_error: The Pydantic ValidationError to format
        label: What was being validated, for the message (e.g., "request", "creative")

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
        f"Invalid {label}: The following fields do not match the AdCP specification:\n\n"
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
