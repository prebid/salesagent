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
    AdCPCredentialInArgsError,
    AdCPError,
    AdCPValidationError,
    build_validation_error_details,
    format_buyer_field_path,
)
from src.core.exceptions import (
    first_validation_error_field as first_validation_error_field,
)

logger = logging.getLogger(__name__)

# Best-effort NAME terms that mark a rejected extra field as credential-bearing, so it is
# rejected with the pinned CREDENTIAL_IN_ARGS code (terminal) rather than VALIDATION_ERROR
# (correctable). Detection is name-based over the loc segments (normalized: `_`/`-` stripped);
# a credential smuggled under a buyer-invented, non-credential-shaped name falls through to
# VALIDATION_ERROR — still rejected, still value-redacted by format_validation_error. The value
# is never inspected or echoed. @source dist/docs/3.1.1/building/by-layer/L2/authentication.mdx.
_CREDENTIAL_NAME_TERMS = frozenset(
    {
        "credential",
        "credentials",
        "secret",
        "password",
        "passwd",
        "passphrase",
        "token",
        "bearer",
        "apikey",
        "authorization",
        "accesskey",
        "privatekey",
        "clientsecret",
        "refreshtoken",
        "accesstoken",
        "sessionkey",
        "cookie",
        "signature",
    }
)


def _segment_is_credential(segment: str | int) -> bool:
    """True if a Pydantic loc segment names a credential-bearing field (best-effort)."""
    if not isinstance(segment, str):
        return False
    normalized = segment.lower().replace("_", "").replace("-", "")
    return any(term in normalized for term in _CREDENTIAL_NAME_TERMS)


def _credential_in_args_field(validation_error: ValidationError) -> str | None:
    """Buyer field PATH of the first credential-bearing ``extra_forbidden`` error, else None.

    A buyer-principal credential placed in request args (a credential-named extra field) must be
    rejected with the pinned ``CREDENTIAL_IN_ARGS`` code (terminal), not ``VALIDATION_ERROR``
    (correctable) — auto-retrying re-logs the credential. Returns only the detection PATH (via the
    single bracket renderer), never the value.
    """
    for error in validation_error.errors():
        error_type = str(error.get("type", ""))
        if "extra_forbidden" not in error_type:
            continue
        loc = error.get("loc", ())
        if any(_segment_is_credential(seg) for seg in loc):
            return format_buyer_field_path(loc, error_type=error_type)
    return None


@contextmanager
def adcp_validation_boundary(context: str = "parameters", field: str | None = None) -> Iterator[None]:
    """Translate a Pydantic ``ValidationError`` into a typed ``AdCPValidationError``.

    Transport wrappers and skill handlers validate buyer parameters at the
    boundary. A raw ``ValidationError`` leaking from ``model_validate`` (or a
    typed-model constructor) would surface as an untyped error — and the outer
    dispatcher only builds the two-layer error envelope for ``AdCPError``
    subclasses, so the buyer would lose the real code/recovery. This boundary is
    the SINGLE translation point (#1417): every rejection carries the
    buyer-friendly ``format_validation_error`` message, the structured ``field``
    path, and error.json's top-level ``suggestion`` — no tool hand-rolls its own
    try/except copy.

    ``context`` names what was invalid in the message (e.g. ``"get_products
    request"``); the default renders the ``Invalid parameters`` prefix existing
    wire assertions rely on.

    ``field`` pins the reported request field when the failing model is nested
    under a named request field: coercing a ``BrandReference`` reports
    ``field="brand"``, not the nested pydantic location (e.g. ``industries``).
    When ``None`` (default) the field is derived from the validation error.
    """
    try:
        yield
    except ValidationError as e:
        raise adcp_validation_error_from(e, context=context, field=field) from e


def boundary_context(tool_name: str) -> str:
    """The uniform validation-boundary context for a tool — renders ``Invalid <tool> request: …``.

    ONE accessor so a tool's rejection message reads identically on every transport: the MCP
    TypeAdapter-rejection path (RequestCompatMiddleware) and the MCP-body / A2A / REST request-body
    boundary. Previously each site spelled its own context literal (``"sync_governance request"``,
    ``f"{tool} request"``, ``"request"``), which forked ``create_media_buy`` into ``Invalid
    create_media_buy request:`` on MCP's TypeAdapter stage vs ``Invalid request:`` on A2A/REST
    (#1329). Route every ``adcp_validation_boundary`` / ``adcp_validation_error_from`` context for a
    named tool through here.
    """
    return f"{tool_name} request"


def adcp_validation_error_from(
    validation_error: ValidationError, *, context: str = "parameters", field: str | None = None
) -> AdCPError:
    """Build the typed ``AdCPError`` for a caught Pydantic ``ValidationError``.

    The SINGLE construction shared by the two places a Pydantic ``ValidationError``
    is turned into the buyer-facing validation envelope:

    * ``adcp_validation_boundary`` — the request-body path A2A and REST run (and the
      MCP wrapper body, when the params clear FastMCP's own TypeAdapter first);
    * ``RequestCompatMiddleware`` — the MCP path for a rejection FastMCP's TypeAdapter
      raises BEFORE the wrapper body, which previously produced only the leaf Pydantic
      message and so forked MCP's wire message/suggestion off A2A/REST (#1329).

    Routing both through here means a validation rejection carries the same rich
    ``format_validation_error`` message, the same ``suggest_validation_fix``
    suggestion, the same ``buyer_loc_segments`` field path, and the same structured
    ``details`` on every transport — so a transport-blind scenario can assert the
    same strings everywhere.

    A credential-bearing ``extra_forbidden`` field is rejected with the pinned
    ``CREDENTIAL_IN_ARGS`` code (terminal) instead — the buyer-principal-credential-in-args
    contract (authentication.mdx L2). The message stays generic and never echoes the value; the
    ``field`` is the detection path only, and the terminal recovery + pinned suggestion come from
    the typed class (a single owner for the code, not a call-site literal).
    """
    credential_field = _credential_in_args_field(validation_error)
    if credential_field is not None:
        return AdCPCredentialInArgsError(
            "A credential was detected in the request arguments. Credentials must be sent on the "
            "transport authentication channel (e.g. Authorization: Bearer), never inside the request "
            "payload.",
            field=field if field is not None else credential_field,
        )
    return AdCPValidationError(
        format_validation_error(validation_error, context=context),
        field=field if field is not None else first_validation_error_field(validation_error),
        suggestion=suggest_validation_fix(validation_error),
        details=build_validation_error_details(validation_error.errors()),
    )


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
        error_type = error["type"]
        # Buyer field path via the SINGLE bracket renderer, forwarding the error type so the
        # message reports the exact same path as ``field``, the suggestion, and ``details.loc``
        # (one spelling, brackets: ``accounts[0].governance_agents[0]...``) — #1329.
        field_path = format_buyer_field_path(error["loc"], error_type=str(error_type))
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
            # ALWAYS redact the value of a rejected extra field — never echo it. A
            # deny-list cannot enumerate buyer-invented credential names (creds/pwd/jwt/
            # pat/signature/cookie/session/…), and a scalar or list-of-pairs credential
            # escapes any nested-key scan, so echoing is unsafe in the general case. The
            # field PATH is the actionable part and always survives; withholding the
            # value keeps a secret off the buyer wire and out of the message-persisting
            # log/audit sinks (#1329). ``errors[0].message`` feeds those sinks,
            # so this holds on REST and A2A; MCP surfaces only the leaf Pydantic message.
            if input_val is None:
                error_details.append(f"  • {field_path}: Extra field not allowed by AdCP spec")
            else:
                error_details.append(
                    f"  • {field_path}: Extra field not allowed by AdCP spec.\n    Received value: [redacted]"
                )
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
    error_type = first.get("type", "")
    # Same SINGLE bracket renderer as the field + message (#1329).
    field_path = format_buyer_field_path(first.get("loc", ()), error_type=str(error_type)) or "request"

    if "missing" in error_type:
        return f"Provide the required '{field_path}' field and resend the request."
    if "extra_forbidden" in error_type:
        return f"Remove the unrecognized '{field_path}' field; it is not part of the AdCP request schema."
    if error_type.startswith("string_pattern_mismatch") or "too_short" in error_type or "too_long" in error_type:
        return f"Provide a valid '{field_path}' value that satisfies the AdCP field constraints and resend."
    return f"Correct the '{field_path}' field to match the AdCP specification and resend."
