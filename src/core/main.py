import json
import logging
import os
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.server.dependencies import get_http_headers
from rich.console import Console
from sqlalchemy import select

from src.adapters.google_ad_manager import GoogleAdManager
from src.adapters.kevel import Kevel
from src.adapters.mock_ad_server import MockAdServer as MockAdServerAdapter
from src.adapters.mock_creative_engine import MockCreativeEngine
from src.adapters.triton_digital import TritonDigital
from src.core.audit_logger import get_audit_logger
from src.core.testing_hooks import (
    DeliverySimulator,
    TimeSimulator,
    apply_testing_hooks,
    get_testing_context,
)
from src.landing import generate_tenant_landing_page
from src.services.activity_feed import activity_feed

logger = logging.getLogger(__name__)

# Database models
from product_catalog_providers.factory import get_product_catalog_provider

# Other imports
from src.core.config_loader import (
    get_current_tenant,
    get_tenant_by_id,
    get_tenant_by_subdomain,
    get_tenant_by_virtual_host,
    load_config,
    set_current_tenant,
)
from src.core.context_manager import get_context_manager
from src.core.database.database import init_db
from src.core.database.database_session import get_db_session
from src.core.database.models import (
    AdapterConfig,
    AuthorizedProperty,
    MediaBuy,
    PropertyTag,
    Tenant,
    WorkflowStep,
)
from src.core.database.models import Principal as ModelPrincipal
from src.core.database.models import Product as ModelProduct

# Schema models (explicit imports to avoid collisions)
# Schema adapters (wrapping generated schemas)
from src.core.schema_adapters import (
    ActivateSignalResponse,
    CreateMediaBuyResponse,
    GetMediaBuyDeliveryResponse,
    GetProductsResponse,
    GetSignalsResponse,
    ListAuthorizedPropertiesRequest,
    ListAuthorizedPropertiesResponse,
    ListCreativeFormatsRequest,
    ListCreativeFormatsResponse,
    ListCreativesResponse,
    SyncCreativesResponse,
    UpdateMediaBuyResponse,
)
from src.core.schema_helpers import create_get_products_request
from src.core.schemas import (
    CreateHumanTaskResponse,
    CreateMediaBuyRequest,
    Creative,
    CreativeAssignment,
    CreativeGroup,
    CreativeStatus,
    DeliveryTotals,
    Error,
    GetMediaBuyDeliveryRequest,
    GetSignalsRequest,
    HumanTask,
    MediaBuyDeliveryData,
    MediaPackage,
    Package,
    PackageDelivery,
    PackagePerformance,
    Principal,
    Product,
    Property,
    PropertyIdentifier,
    PropertyTagMetadata,
    ReportingPeriod,
    Signal,
    SignalDeployment,
    SignalPricing,
    TaskStatus,
    UpdateMediaBuyRequest,
    UpdatePerformanceIndexRequest,
    UpdatePerformanceIndexResponse,
    VerifyTaskRequest,
    VerifyTaskResponse,
)
from src.core.schemas_generated._schemas_v1_media_buy_get_products_request_json import (
    GetProductsRequest as GetProductsRequestGenerated,
)
from src.services.policy_check_service import PolicyCheckService, PolicyStatus
from src.services.setup_checklist_service import SetupIncompleteError, validate_setup_complete
from src.services.slack_notifier import get_slack_notifier

# Initialize Rich console
console = Console()

# Backward compatibility alias for deprecated Task model
# The workflow system now uses WorkflowStep exclusively
Task = WorkflowStep

# Temporary placeholder classes for missing schemas
# TODO: These should be properly defined in schemas.py
from pydantic import BaseModel, ValidationError


class ApproveAdaptationRequest(BaseModel):
    creative_id: str
    adaptation_id: str
    approve: bool = True
    modifications: dict[str, Any] | None = None


class ApproveAdaptationResponse(BaseModel):
    success: bool
    message: str


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
    import asyncio
    import concurrent.futures

    try:
        # Check if there's already a running event loop
        asyncio.get_running_loop()
        # We're in an async context, run in thread pool to avoid nested loop error
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(lambda: asyncio.run(coroutine))
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
                f"  • {field_path}: Expected string, got {type(input_val).__name__}. " f"Please provide a string value."
            )
        elif "missing" in error_type:
            error_details.append(f"  • {field_path}: Required field is missing")
        elif "extra_forbidden" in error_type:
            error_details.append(f"  • {field_path}: Extra field not allowed by AdCP spec")
        else:
            error_details.append(f"  • {field_path}: {msg}")

    error_msg = (
        f"Invalid {context}: The following fields do not match the AdCP specification:\n\n"
        + "\n".join(error_details)
        + "\n\nPlease check the AdCP spec at https://adcontextprotocol.org/schemas/v1/ for correct field types."
    )

    return error_msg


# --- Authentication ---


def get_principal_from_token(token: str, tenant_id: str | None = None) -> str | None:
    """Looks up a principal_id from the database using a token.

    If tenant_id is provided, only looks in that specific tenant.
    If not provided, searches globally by token and sets the tenant context.
    """
    console.print(
        f"[blue]Looking up principal: tenant_id={tenant_id}, token={'***' + token[-6:] if token else 'None'}[/blue]"
    )

    # Use standardized session management
    with get_db_session() as session:
        # Use explicit transaction for consistency
        with session.begin():
            if tenant_id:
                # If tenant_id specified, ONLY look in that tenant
                console.print(f"[blue]Searching for principal in tenant '{tenant_id}'[/blue]")
                stmt = select(ModelPrincipal).filter_by(access_token=token, tenant_id=tenant_id)
                principal = session.scalars(stmt).first()

                if not principal:
                    console.print(f"[yellow]No principal found in tenant '{tenant_id}', checking admin token[/yellow]")
                    # Also check if it's the admin token for this specific tenant
                    stmt = select(Tenant).filter_by(tenant_id=tenant_id, is_active=True)
                    tenant = session.scalars(stmt).first()
                    if tenant and token == tenant.admin_token:
                        console.print(f"[green]Token matches admin token for tenant '{tenant_id}'[/green]")
                        # Set tenant context for admin token
                        from src.core.utils.tenant_utils import serialize_tenant_to_dict

                        tenant_dict = serialize_tenant_to_dict(tenant)
                        set_current_tenant(tenant_dict)
                        return f"{tenant_id}_admin"
                    console.print(f"[red]Token not found in tenant '{tenant_id}' and doesn't match admin token[/red]")
                    return None
                else:
                    console.print(f"[green]Found principal '{principal.principal_id}' in tenant '{tenant_id}'[/green]")
            else:
                # No tenant specified - search globally by token
                console.print("[blue]No tenant specified - searching globally by token[/blue]")
                stmt = select(ModelPrincipal).filter_by(access_token=token)
                principal = session.scalars(stmt).first()

                if not principal:
                    console.print("[red]No principal found with this token globally[/red]")
                    return None

                console.print(
                    f"[green]Found principal '{principal.principal_id}' in tenant '{principal.tenant_id}'[/green]"
                )

                # CRITICAL: Validate the tenant exists and is active before proceeding
                stmt = select(Tenant).filter_by(tenant_id=principal.tenant_id, is_active=True)
                tenant_check = session.scalars(stmt).first()
                if not tenant_check:
                    console.print(f"[red]Tenant '{principal.tenant_id}' is inactive or deleted[/red]")
                    # Tenant is disabled or deleted - fail securely
                    return None

            # Only set tenant context if we didn't have one specified (global lookup case)
            # If tenant_id was provided, context was already set by the caller
            if not tenant_id:
                # Get the tenant for this principal and set it as current context
                stmt = select(Tenant).filter_by(tenant_id=principal.tenant_id, is_active=True)
                tenant = session.scalars(stmt).first()
                if tenant:
                    from src.core.utils.tenant_utils import serialize_tenant_to_dict

                    tenant_dict = serialize_tenant_to_dict(tenant)
                    set_current_tenant(tenant_dict)
                    console.print(
                        f"[bold green]Set tenant context to '{tenant.tenant_id}' (from principal)[/bold green]"
                    )

                    # Check if this is the admin token for the tenant
                    if token == tenant.admin_token:
                        return f"{tenant.tenant_id}_admin"
            else:
                # Tenant was already set by caller - just check admin token
                stmt = select(Tenant).filter_by(tenant_id=tenant_id, is_active=True)
                tenant = session.scalars(stmt).first()
                if tenant and token == tenant.admin_token:
                    console.print(f"[green]Token is admin token for tenant '{tenant_id}'[/green]")
                    return f"{tenant_id}_admin"

            return principal.principal_id


def _get_header_case_insensitive(headers: dict, header_name: str) -> str | None:
    """Get a header value with case-insensitive lookup.

    HTTP headers are case-insensitive, but Python dicts are case-sensitive.
    This helper function performs case-insensitive header lookup.

    Args:
        headers: Dictionary of headers
        header_name: Header name to look up (will be compared case-insensitively)

    Returns:
        Header value if found, None otherwise
    """
    if not headers:
        return None

    header_name_lower = header_name.lower()
    for key, value in headers.items():
        if key.lower() == header_name_lower:
            return value
    return None


def get_push_notification_config_from_headers(headers: dict[str, str] | None) -> dict[str, Any] | None:
    """
    Extract protocol-level push notification config from MCP HTTP headers.

    MCP clients can provide push notification config via custom headers:
    - X-Push-Notification-Url: Webhook URL
    - X-Push-Notification-Auth-Scheme: Authentication scheme (HMAC-SHA256, Bearer, None)
    - X-Push-Notification-Credentials: Shared secret or Bearer token

    Returns:
        Push notification config dict matching A2A structure, or None if not provided
    """
    if not headers:
        return None

    url = _get_header_case_insensitive(headers, "x-push-notification-url")
    if not url:
        return None

    auth_scheme = _get_header_case_insensitive(headers, "x-push-notification-auth-scheme") or "None"
    credentials = _get_header_case_insensitive(headers, "x-push-notification-credentials")

    return {
        "url": url,
        "authentication": {"schemes": [auth_scheme], "credentials": credentials} if auth_scheme != "None" else None,
    }


def get_principal_from_context(
    context: Context | None, require_valid_token: bool = True
) -> tuple[str | None, dict | None]:
    """Extract principal ID and tenant context from the FastMCP context using x-adcp-auth header.

    Uses the current recommended FastMCP pattern with get_http_headers().
    Falls back to context.meta["headers"] for sync tools where get_http_headers() may return empty dict.
    Requires FastMCP >= 2.11.0.

    Args:
        context: FastMCP context object
        require_valid_token: If True (default), raises error for invalid tokens.
                           If False, treats invalid tokens like missing tokens (for discovery endpoints).

    Returns:
        tuple[principal_id, tenant_context]: Principal ID and tenant dict, or (None, tenant) if no/invalid auth

    Note: Returns tenant context explicitly because ContextVar changes in sync functions
    don't reliably propagate to async callers (Python ContextVar + async/sync boundary issue).
    The caller MUST call set_current_tenant(tenant_context) in their own context.
    """
    # Get headers using the recommended FastMCP approach
    # NOTE: get_http_headers() works via context vars, so it can work even when context=None
    # This allows unauthenticated public discovery endpoints to detect tenant from headers
    # CRITICAL: Use include_all=True to get Host header (excluded by default)
    import logging as log_module
    import sys

    # URGENT DEBUG: Use logger.error() to ensure visibility in production logs
    debug_logger = log_module.getLogger(__name__)
    debug_logger.error(f"🔍 get_principal_from_context called: context={context}, type={type(context)}")
    if context:
        debug_logger.error(f"🔍 context attributes: {dir(context)[:10]}...")  # First 10 attrs
        if hasattr(context, "meta"):
            debug_logger.error(f"🔍 context.meta exists: {context.meta}")
        if hasattr(context, "headers"):
            try:
                headers_len = len(context.headers) if context.headers else 0
                debug_logger.error(f"🔍 context.headers exists: {headers_len} headers")
            except (TypeError, AttributeError):
                debug_logger.error(f"🔍 context.headers exists but not iterable: {type(context.headers)}")

    headers = None
    try:
        headers = get_http_headers(include_all=True)
        debug_logger.error(f"🔍 get_http_headers() returned: {len(headers) if headers else 0} headers")
        if headers:
            debug_logger.error(f"🔍 Header keys: {list(headers.keys())}")
        print(
            f"[PRINCIPAL DEBUG] get_http_headers(include_all=True) returned {len(headers) if headers else 0} headers",
            file=sys.stderr,
            flush=True,
        )
        console.print(
            f"[blue]DEBUG: get_http_headers(include_all=True) returned {len(headers) if headers else 0} headers[/blue]"
        )
        if headers:
            print(f"[PRINCIPAL DEBUG] Header keys: {list(headers.keys())}", file=sys.stderr, flush=True)
            console.print(f"[blue]DEBUG: Header keys: {list(headers.keys())}[/blue]")
    except Exception as e:
        debug_logger.error(f"🔍 get_http_headers() exception: {type(e).__name__}: {e}")
        print(f"[PRINCIPAL DEBUG] get_http_headers() exception: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        console.print(f"[yellow]DEBUG: get_http_headers() exception: {type(e).__name__}: {e}[/yellow]")
        pass  # Will try fallback below

    # If get_http_headers() returned empty dict or None, try context.meta fallback
    # This is necessary for sync tools where get_http_headers() may not work
    # CRITICAL: get_http_headers() returns {} for sync tools, so we need fallback even for empty dict
    if not headers:  # Handles both None and {}
        debug_logger.error(f"🔍 get_http_headers() empty, trying fallback - context is {type(context)}")
        print("[PRINCIPAL DEBUG] get_http_headers() empty, trying fallback methods", file=sys.stderr, flush=True)
        print(f"[PRINCIPAL DEBUG] context={context}, type={type(context)}", file=sys.stderr, flush=True)
        console.print("[yellow]DEBUG: get_http_headers() empty, trying fallback methods[/yellow]")
        # Only try context fallbacks if context is not None
        if context is not None:
            print(f"[PRINCIPAL DEBUG] hasattr(context, 'meta')={hasattr(context, 'meta')}", file=sys.stderr, flush=True)
            if hasattr(context, "meta"):
                print(f"[PRINCIPAL DEBUG] context.meta={context.meta}", file=sys.stderr, flush=True)
                debug_logger.error(f"🔍 context.meta={context.meta}")
            if hasattr(context, "meta") and context.meta and "headers" in context.meta:
                headers = context.meta["headers"]
                debug_logger.error(f"🔍 ✅ Got {len(headers)} headers from context.meta!")
                print(f"[PRINCIPAL DEBUG] Got {len(headers)} headers from context.meta", file=sys.stderr, flush=True)
                console.print(f"[blue]DEBUG: Got {len(headers)} headers from context.meta[/blue]")
            # Try other possible attributes
            elif hasattr(context, "headers"):
                headers = context.headers
                debug_logger.error(f"🔍 ✅ Got {len(headers)} headers from context.headers!")
                print(f"[PRINCIPAL DEBUG] Got {len(headers)} headers from context.headers", file=sys.stderr, flush=True)
                console.print(f"[blue]DEBUG: Got {len(headers)} headers from context.headers[/blue]")
            elif hasattr(context, "_headers"):
                headers = context._headers
                debug_logger.error(f"🔍 ✅ Got {len(headers)} headers from context._headers!")
                print(
                    f"[PRINCIPAL DEBUG] Got {len(headers)} headers from context._headers", file=sys.stderr, flush=True
                )
                console.print(f"[blue]DEBUG: Got {len(headers)} headers from context._headers[/blue]")
            else:
                debug_logger.error("🔍 ❌ No fallback attributes (meta/headers/_headers) available on context")
                print("[PRINCIPAL DEBUG] No fallback attributes available", file=sys.stderr, flush=True)
                console.print(
                    "[yellow]DEBUG: No fallback attributes available (context provided but no headers)[/yellow]"
                )
        else:
            debug_logger.error("🔍 ❌ context=None and get_http_headers() failed")
            print("[PRINCIPAL DEBUG] context=None", file=sys.stderr, flush=True)
            console.print("[yellow]DEBUG: context=None and get_http_headers() failed - no headers available[/yellow]")

    # If still no headers dict available, return None
    if not headers:
        debug_logger.error("🔍 ❌ FINAL: No headers available - cannot detect tenant - returning (None, None)")
        print("[PRINCIPAL DEBUG] ❌ CRITICAL: No headers available - cannot detect tenant", file=sys.stderr, flush=True)
        console.print("[red]❌ CRITICAL: No headers available - cannot detect tenant[/red]")
        return (None, None)

    # Log all relevant headers for debugging
    import logging

    logger = logging.getLogger(__name__)

    host_header = _get_header_case_insensitive(headers, "host")
    apx_host_header = _get_header_case_insensitive(headers, "apx-incoming-host")
    tenant_header = _get_header_case_insensitive(headers, "x-adcp-tenant")

    logger.info("=" * 80)
    logger.info("TENANT DETECTION - Auth Headers Debug:")
    logger.info(f"  Host: {host_header}")
    logger.info(f"  Apx-Incoming-Host: {apx_host_header}")
    logger.info(f"  x-adcp-tenant: {tenant_header}")
    logger.info(f"  Total headers available: {len(headers)}")
    logger.info("=" * 80)

    console.print("[blue]Auth Headers Debug:[/blue]")
    console.print(f"  Host: {host_header}")
    console.print(f"  Apx-Incoming-Host: {apx_host_header}")
    console.print(f"  x-adcp-tenant: {tenant_header}")

    # ALWAYS resolve tenant from headers first (even without auth for public discovery endpoints)
    requested_tenant_id = None
    tenant_context = None
    detection_method = None

    # 1. Check host header - try virtual host FIRST, then fall back to subdomain
    if not requested_tenant_id:
        host = _get_header_case_insensitive(headers, "host") or ""
        console.print(f"[blue]Checking Host header: {host}[/blue]")

        # CRITICAL: Try virtual host lookup FIRST before extracting subdomain
        # This prevents issues where a subdomain happens to match a virtual host
        # (e.g., "test-agent" subdomain vs "test-agent.adcontextprotocol.org" virtual host)
        tenant_context = get_tenant_by_virtual_host(host)
        if tenant_context:
            requested_tenant_id = tenant_context["tenant_id"]
            detection_method = "host header (virtual host)"
            set_current_tenant(tenant_context)
            console.print(
                f"[green]Tenant detected from Host header virtual host: {host} → tenant_id: {requested_tenant_id}[/green]"
            )
        else:
            # Fallback to subdomain extraction if virtual host lookup failed
            subdomain = host.split(".")[0] if "." in host else None
            console.print(f"[blue]No virtual host match, extracting subdomain from Host header: {subdomain}[/blue]")
            if subdomain and subdomain not in ["localhost", "adcp-sales-agent", "www", "admin"]:
                # Look up tenant by subdomain to get actual tenant_id
                console.print(f"[blue]Looking up tenant by subdomain: {subdomain}[/blue]")
                tenant_context = get_tenant_by_subdomain(subdomain)
                if tenant_context:
                    requested_tenant_id = tenant_context["tenant_id"]
                    detection_method = "subdomain"
                    set_current_tenant(tenant_context)
                    console.print(
                        f"[green]Tenant detected from subdomain: {subdomain} → tenant_id: {requested_tenant_id}[/green]"
                    )
                else:
                    console.print(f"[yellow]No tenant found for subdomain: {subdomain}[/yellow]")

    # 2. Check x-adcp-tenant header (set by nginx for path-based routing)
    if not requested_tenant_id:
        tenant_hint = _get_header_case_insensitive(headers, "x-adcp-tenant")
        if tenant_hint:
            console.print(f"[blue]Looking up tenant from x-adcp-tenant header: {tenant_hint}[/blue]")
            # Try to look up by subdomain first (most common case)
            tenant_context = get_tenant_by_subdomain(tenant_hint)
            if tenant_context:
                requested_tenant_id = tenant_context["tenant_id"]
                detection_method = "x-adcp-tenant header (subdomain lookup)"
                set_current_tenant(tenant_context)
                console.print(
                    f"[green]Tenant detected from x-adcp-tenant: {tenant_hint} → tenant_id: {requested_tenant_id}[/green]"
                )
            else:
                # Fallback: assume it's already a tenant_id
                requested_tenant_id = tenant_hint
                detection_method = "x-adcp-tenant header (direct)"
                # Need to look up and set tenant context
                tenant_context = get_tenant_by_id(tenant_hint)
                if tenant_context:
                    set_current_tenant(tenant_context)
                    console.print(f"[green]Tenant context set for tenant_id: {requested_tenant_id}[/green]")
                else:
                    console.print(f"[yellow]Using x-adcp-tenant as tenant_id directly: {requested_tenant_id}[/yellow]")

    # 3. Check Apx-Incoming-Host header (for Approximated.app virtual hosts)
    if not requested_tenant_id:
        apx_host = _get_header_case_insensitive(headers, "apx-incoming-host")
        console.print(f"[blue]Checking Apx-Incoming-Host header: {apx_host}[/blue]")
        if apx_host:
            console.print(f"[blue]Looking up tenant by virtual host (via Apx-Incoming-Host): {apx_host}[/blue]")
            tenant_context = get_tenant_by_virtual_host(apx_host)
            console.print(f"[blue]get_tenant_by_virtual_host() returned: {tenant_context}[/blue]")
            if tenant_context:
                requested_tenant_id = tenant_context["tenant_id"]
                detection_method = "apx-incoming-host"
                # Set tenant context immediately for virtual host routing
                set_current_tenant(tenant_context)
                console.print(f"[green]✅ Tenant detected from Apx-Incoming-Host: {requested_tenant_id}[/green]")
            else:
                console.print(f"[yellow]⚠️ No tenant found for virtual host: {apx_host}[/yellow]")
        else:
            console.print("[yellow]Apx-Incoming-Host header not present[/yellow]")

    if not requested_tenant_id:
        console.print("[yellow]No tenant detected from headers[/yellow]")
    else:
        console.print(f"[bold green]Final tenant_id: {requested_tenant_id} (via {detection_method})[/bold green]")

    # NOW check for auth token (after tenant resolution)
    auth_token = _get_header_case_insensitive(headers, "x-adcp-auth")
    console.print(f"  x-adcp-auth: {'Present' if auth_token else 'Missing'}")

    if not auth_token:
        console.print("[yellow]No x-adcp-auth token found - OK for discovery endpoints[/yellow]")
        # Return tenant context without auth for public discovery endpoints
        return (None, tenant_context)

    # Validate token and get principal
    # If requested_tenant_id is set: validate token belongs to that specific tenant
    # If requested_tenant_id is None: do global lookup and set tenant context from token
    if not requested_tenant_id:
        # No tenant detected from headers - use global token lookup
        # SECURITY NOTE: This is safe because get_principal_from_token() will:
        # 1. Look up the token globally
        # 2. Find which tenant it belongs to
        # 3. Set that tenant's context
        # 4. Return principal_id only if token is valid for that tenant
        console.print("[yellow]Using global token lookup (finds tenant from token)[/yellow]")
        detection_method = "global token lookup"

    principal_id = get_principal_from_token(auth_token, requested_tenant_id)

    # If token was provided but invalid, raise an error (unless require_valid_token=False for discovery)
    # This distinguishes between "no auth" (OK) and "bad auth" (error or warning)
    if principal_id is None:
        if require_valid_token:
            from fastmcp.exceptions import ToolError

            raise ToolError(
                "INVALID_AUTH_TOKEN",
                f"Authentication token is invalid for tenant '{requested_tenant_id or 'any'}'. "
                f"The token may be expired, revoked, or associated with a different tenant.",
            )
        else:
            # For discovery endpoints, treat invalid token like missing token
            console.print(
                f"[yellow]Invalid token for tenant '{requested_tenant_id or 'any'}' - continuing without auth (discovery endpoint)[/yellow]"
            )
            return (None, tenant_context)

    # If tenant_context wasn't set by header detection, get it from current tenant
    # (get_principal_from_token set it as a side effect for global lookup case)
    if not tenant_context:
        tenant_context = get_current_tenant()

    # Return both principal_id and tenant_context explicitly
    # Caller MUST call set_current_tenant(tenant_context) in their async context
    return (principal_id, tenant_context)


def get_principal_adapter_mapping(principal_id: str) -> dict[str, Any]:
    """Get the platform mappings for a principal."""
    tenant = get_current_tenant()
    with get_db_session() as session:
        stmt = select(ModelPrincipal).filter_by(principal_id=principal_id, tenant_id=tenant["tenant_id"])
        principal = session.scalars(stmt).first()
        return principal.platform_mappings if principal else {}


def get_principal_object(principal_id: str) -> Principal | None:
    """Get a Principal object for the given principal_id."""
    tenant = get_current_tenant()
    with get_db_session() as session:
        stmt = select(ModelPrincipal).filter_by(principal_id=principal_id, tenant_id=tenant["tenant_id"])
        principal = session.scalars(stmt).first()

        if principal:
            return Principal(
                principal_id=principal.principal_id,
                name=principal.name,
                platform_mappings=principal.platform_mappings,
            )
    return None


def get_adapter_principal_id(principal_id: str, adapter: str) -> str | None:
    """Get the adapter-specific ID for a principal."""
    mappings = get_principal_adapter_mapping(principal_id)

    # Map adapter names to their specific fields
    adapter_field_map = {
        "gam": "gam_advertiser_id",
        "kevel": "kevel_advertiser_id",
        "triton": "triton_advertiser_id",
        "mock": "mock_advertiser_id",
    }

    field_name = adapter_field_map.get(adapter)
    if field_name:
        return str(mappings.get(field_name, "")) if mappings.get(field_name) else None
    return None


def get_adapter(principal: Principal, dry_run: bool = False, testing_context=None):
    """Get the appropriate adapter instance for the selected adapter type."""
    # Get tenant and adapter config from database
    tenant = get_current_tenant()
    selected_adapter = tenant.get("ad_server", "mock")

    # Get adapter config from adapter_config table
    with get_db_session() as session:
        stmt = select(AdapterConfig).filter_by(tenant_id=tenant["tenant_id"])
        config_row = session.scalars(stmt).first()

        adapter_config = {"enabled": True}
        if config_row:
            adapter_type = config_row.adapter_type
            if adapter_type == "mock":
                adapter_config["dry_run"] = config_row.mock_dry_run
            elif adapter_type == "google_ad_manager":
                adapter_config["network_code"] = config_row.gam_network_code
                adapter_config["refresh_token"] = config_row.gam_refresh_token
                adapter_config["trafficker_id"] = config_row.gam_trafficker_id
                adapter_config["manual_approval_required"] = config_row.gam_manual_approval_required

                # Get advertiser_id from principal's platform_mappings (per-principal, not tenant-level)
                # Support both old format (nested under "google_ad_manager") and new format (root "gam_advertiser_id")
                if principal.platform_mappings:
                    # Try nested format first
                    gam_mappings = principal.platform_mappings.get("google_ad_manager", {})
                    advertiser_id = gam_mappings.get("advertiser_id")

                    # Fall back to root-level format if nested not found
                    if not advertiser_id:
                        advertiser_id = principal.platform_mappings.get("gam_advertiser_id")

                    adapter_config["company_id"] = advertiser_id
                else:
                    adapter_config["company_id"] = None
            elif adapter_type == "kevel":
                adapter_config["network_id"] = config_row.kevel_network_id
                adapter_config["api_key"] = config_row.kevel_api_key
                adapter_config["manual_approval_required"] = config_row.kevel_manual_approval_required
            elif adapter_type == "triton":
                adapter_config["station_id"] = config_row.triton_station_id
                adapter_config["api_key"] = config_row.triton_api_key

    if not selected_adapter:
        # Default to mock if no adapter specified
        selected_adapter = "mock"
        adapter_config = {"enabled": True}

    # Create the appropriate adapter instance with tenant_id and testing context
    tenant_id = tenant["tenant_id"]
    if selected_adapter == "mock":
        return MockAdServerAdapter(
            adapter_config, principal, dry_run, tenant_id=tenant_id, strategy_context=testing_context
        )
    elif selected_adapter == "google_ad_manager":
        return GoogleAdManager(
            adapter_config,
            principal,
            network_code=adapter_config.get("network_code"),
            advertiser_id=adapter_config.get("company_id"),
            trafficker_id=adapter_config.get("trafficker_id"),
            dry_run=dry_run,
            tenant_id=tenant_id,
        )
    elif selected_adapter == "kevel":
        return Kevel(adapter_config, principal, dry_run, tenant_id=tenant_id)
    elif selected_adapter in ["triton", "triton_digital"]:
        return TritonDigital(adapter_config, principal, dry_run, tenant_id=tenant_id)
    else:
        # Default to mock for unsupported adapters
        return MockAdServerAdapter(
            adapter_config, principal, dry_run, tenant_id=tenant_id, strategy_context=testing_context
        )


# --- Initialization ---
# NOTE: Database initialization moved to startup script to avoid import-time failures
# The run_all_services.py script handles database initialization before starting the MCP server

# Try to load config, but use defaults if no tenant context available
try:
    config = load_config()
except (RuntimeError, Exception) as e:
    # Use minimal config for test environments or when DB is unavailable
    # This handles both "No tenant context set" and database connection errors
    if "No tenant context" in str(e) or "connection" in str(e).lower() or "operational" in str(e).lower():
        config = {
            "creative_engine": {},
            "dry_run": False,
            "adapters": {"mock": {"enabled": True}},
            "ad_server": {"adapter": "mock", "enabled": True},
        }
    else:
        raise

mcp = FastMCP(
    name="AdCPSalesAgent",
    # Enable sessions to allow proper HTTP context for header access
    # This is needed for tenant detection via headers in unauthenticated calls
    stateless_http=False,
)

# Initialize creative engine with minimal config (will be tenant-specific later)
creative_engine_config = {}
creative_engine = MockCreativeEngine(creative_engine_config)


def load_media_buys_from_db():
    """Load existing media buys from database into memory on startup."""
    try:
        # We can't load tenant-specific media buys at startup since we don't have tenant context
        # Media buys will be loaded on-demand when needed
        console.print("[dim]Media buys will be loaded on-demand from database[/dim]")
    except Exception as e:
        console.print(f"[yellow]Warning: Could not initialize media buys from database: {e}[/yellow]")


def load_tasks_from_db():
    """[DEPRECATED] This function is no longer needed - tasks are queried directly from database."""
    # This function is kept for backward compatibility but does nothing
    # All task operations now use direct database queries
    pass


# Removed get_task_from_db - replaced by workflow-based system


# --- In-Memory State ---
media_buys: dict[str, tuple[CreateMediaBuyRequest, str]] = {}
creative_assignments: dict[str, dict[str, list[str]]] = {}
creative_statuses: dict[str, CreativeStatus] = {}
product_catalog: list[Product] = []
creative_library: dict[str, Creative] = {}  # creative_id -> Creative
creative_groups: dict[str, CreativeGroup] = {}  # group_id -> CreativeGroup
creative_assignments_v2: dict[str, CreativeAssignment] = {}  # assignment_id -> CreativeAssignment
# REMOVED: human_tasks dictionary - now using direct database queries only

# Note: load_tasks_from_db() is no longer needed - tasks are queried directly from database

# Authentication cache removed - FastMCP v2.11.0+ properly forwards headers

# Import audit logger for later use

# Import context manager for workflow steps
from src.core.context_manager import ContextManager

context_mgr = ContextManager()

# --- Adapter Configuration ---
# Get adapter from config, fallback to mock
SELECTED_ADAPTER = (
    (config.get("ad_server", {}).get("adapter") or "mock") if config else "mock"
).lower()  # noqa: F841 - used below for adapter selection
AVAILABLE_ADAPTERS = ["mock", "gam", "kevel", "triton", "triton_digital"]

# --- In-Memory State (already initialized above, just adding context_map) ---
context_map: dict[str, str] = {}  # Maps context_id to media_buy_id

# --- Dry Run Mode ---
DRY_RUN_MODE = config.get("dry_run", False)
if DRY_RUN_MODE:
    console.print("[bold yellow]🏃 DRY RUN MODE ENABLED - Adapter calls will be logged[/bold yellow]")

# Display selected adapter
if SELECTED_ADAPTER not in AVAILABLE_ADAPTERS:
    console.print(f"[bold red]❌ Invalid adapter '{SELECTED_ADAPTER}'. Using 'mock' instead.[/bold red]")
    SELECTED_ADAPTER = "mock"
console.print(f"[bold cyan]🔌 Using adapter: {SELECTED_ADAPTER.upper()}[/bold cyan]")


# --- Creative Conversion Helper ---
def _extract_format_namespace(format_value: Any) -> tuple[str, str]:
    """Extract agent_url and format ID from format_id field (AdCP v2.4).

    Args:
        format_value: FormatId dict/object with agent_url+id fields

    Returns:
        Tuple of (agent_url, format_id)

    Raises:
        ValueError: If format_value doesn't have required agent_url and id fields
    """
    if isinstance(format_value, dict):
        agent_url = format_value.get("agent_url")
        format_id = format_value.get("id")
        if not agent_url or not format_id:
            raise ValueError(f"format_id must have both 'agent_url' and 'id' fields. Got: {format_value}")
        return agent_url, format_id
    if hasattr(format_value, "agent_url") and hasattr(format_value, "id"):
        return format_value.agent_url, format_value.id
    if isinstance(format_value, str):
        raise ValueError(
            f"format_id must be an object with 'agent_url' and 'id' fields (AdCP v2.4). "
            f"Got string: '{format_value}'. "
            f"String format_id is no longer supported - all formats must be namespaced."
        )
    raise ValueError(f"Invalid format_id format. Expected object with agent_url and id, got: {type(format_value)}")


def _normalize_format_value(format_value: Any) -> str:
    """Normalize format value to string ID (for legacy code compatibility).

    Args:
        format_value: FormatId dict/object with agent_url+id fields

    Returns:
        String format identifier

    Note: This is a legacy compatibility function. New code should use _extract_format_namespace
    to properly handle the agent_url namespace.
    """
    _, format_id = _extract_format_namespace(format_value)
    return format_id


def _validate_creative_assets(assets: Any) -> dict[str, dict[str, Any]] | None:
    """Validate that creative assets are in AdCP v2.1+ dictionary format.

    AdCP v2.1+ requires assets to be a dictionary keyed by asset_id from the format's
    asset_requirements.

    Args:
        assets: Assets in dict format keyed by asset_id, or None

    Returns:
        Dictionary of assets keyed by asset_id, or None if no assets provided

    Raises:
        ValueError: If assets are not in the correct dict format, or if asset structure is invalid

    Example:
        # Correct format (AdCP v2.1+)
        assets = {
            "main_image": {"asset_type": "image", "url": "https://..."},
            "logo": {"asset_type": "image", "url": "https://..."}
        }
    """
    if assets is None:
        return None

    # Must be a dict
    if not isinstance(assets, dict):
        raise ValueError(
            f"Invalid assets format: expected dict keyed by asset_id (AdCP v2.1+), got {type(assets).__name__}. "
            f"Assets must be a dictionary like: {{'main_image': {{'asset_type': 'image', 'url': '...'}}}}"
        )

    # Validate structure of each asset
    for asset_id, asset_data in assets.items():
        # Asset ID must be a non-empty string
        if not isinstance(asset_id, str):
            raise ValueError(
                f"Asset key must be a string (asset_id from format), got {type(asset_id).__name__}: {asset_id!r}"
            )
        if not asset_id.strip():
            raise ValueError("Asset key (asset_id) cannot be empty or whitespace-only")

        # Asset data must be a dict
        if not isinstance(asset_data, dict):
            raise ValueError(
                f"Asset '{asset_id}' data must be a dict, got {type(asset_data).__name__}. "
                f"Expected format: {{'asset_type': '...', 'url': '...', ...}}"
            )

    return assets


def _convert_creative_to_adapter_asset(creative: Creative, package_assignments: list[str]) -> dict[str, Any]:
    """Convert AdCP v1.3+ Creative object to format expected by ad server adapters."""

    # Base asset object with common fields
    asset = {
        "creative_id": creative.creative_id,
        "name": creative.name,
        "format": creative.get_format_string(),  # Handle both string and FormatId object
        "package_assignments": package_assignments,
    }

    # Determine creative type using AdCP v1.3+ logic
    creative_type = creative.get_creative_type()

    if creative_type == "third_party_tag":
        # Third-party tag creative - use AdCP v1.3+ snippet fields
        snippet = creative.get_snippet_content()
        if not snippet:
            raise ValueError(f"No snippet found for third-party creative {creative.creative_id}")

        asset["snippet"] = snippet
        asset["snippet_type"] = creative.snippet_type or _detect_snippet_type(snippet)
        asset["url"] = creative.url  # Keep URL for fallback

    elif creative_type == "native":
        # Native creative - use AdCP v1.3+ template_variables field
        template_vars = creative.get_template_variables_dict()
        if not template_vars:
            raise ValueError(f"No template_variables found for native creative {creative.creative_id}")

        asset["template_variables"] = template_vars
        asset["url"] = creative.url  # Fallback URL

    elif creative_type == "vast":
        # VAST reference
        asset["snippet"] = creative.get_snippet_content() or creative.url
        asset["snippet_type"] = creative.snippet_type or ("vast_xml" if ".xml" in creative.url else "vast_url")

    else:  # hosted_asset
        # Traditional hosted asset (image/video)
        asset["media_url"] = creative.get_primary_content_url()
        asset["url"] = asset["media_url"]  # For backward compatibility

    # Add common optional fields
    if creative.click_url:
        asset["click_url"] = creative.click_url
    if creative.width:
        asset["width"] = creative.width
    if creative.height:
        asset["height"] = creative.height
    if creative.duration:
        asset["duration"] = creative.duration

    # Always preserve delivery_settings (including tracking_urls) for all creative types
    # This ensures impression trackers from buyers flow through to ad servers
    if creative.delivery_settings:
        asset["delivery_settings"] = creative.delivery_settings

    return asset


def _detect_snippet_type(snippet: str) -> str:
    """Auto-detect snippet type from content for legacy support."""
    if snippet.startswith("<?xml") or ".xml" in snippet:
        return "vast_xml"
    elif snippet.startswith("http") and "vast" in snippet.lower():
        return "vast_url"
    elif snippet.startswith("<script"):
        return "javascript"
    else:
        return "html"  # Default


# --- Security Helper ---
def _get_principal_id_from_context(context: Context) -> str:
    """Extracts the token from the header and returns a principal_id.

    Handles both FastMCP Context (with HTTP headers) and ToolContext (with principal_id already set).
    This allows the same implementation function to work from both MCP and A2A paths.
    """
    # Import here to avoid circular dependency
    from src.core.tool_context import ToolContext

    # If this is a ToolContext (from A2A), principal_id is already set
    if isinstance(context, ToolContext):
        console.print(f"[bold green]Authenticated principal '{context.principal_id}' (from ToolContext)[/bold green]")
        return context.principal_id

    # Otherwise, extract from FastMCP Context headers
    principal_id, tenant = get_principal_from_context(context)

    # Set tenant context if returned (sync function so ContextVar should propagate)
    if tenant:
        set_current_tenant(tenant)

    # Extract headers for debugging
    headers = {}
    if hasattr(context, "meta"):
        headers = context.meta.get("headers", {})
    auth_header = headers.get("x-adcp-auth", "NOT_PRESENT")
    apx_host = headers.get("apx-incoming-host", "NOT_PRESENT")

    if not principal_id:
        # Determine if header is missing or just invalid
        if auth_header == "NOT_PRESENT":
            raise ToolError(
                f"Missing x-adcp-auth header. "
                f"Apx-Incoming-Host: {apx_host}, "
                f"Tenant: {tenant.get('tenant_id') if tenant else 'NONE'}"
            )
        else:
            # Header present but invalid (token not found in DB)
            raise ToolError(
                f"Invalid x-adcp-auth token (not found in database). "
                f"Token: {auth_header[:20]}..., "
                f"Apx-Incoming-Host: {apx_host}, "
                f"Tenant: {get_current_tenant().get('tenant_id') if get_current_tenant() else 'NONE'}"
            )

    console.print(f"[bold green]Authenticated principal '{principal_id}' (from FastMCP Context)[/bold green]")
    return principal_id


def _verify_principal(media_buy_id: str, context: Context):
    """Verify that the principal from context owns the media buy.

    Checks database for media buy ownership, not in-memory dictionary.

    Args:
        media_buy_id: Media buy ID to verify
        context: FastMCP context with principal info

    Raises:
        ValueError: Media buy not found
        PermissionError: Principal doesn't own media buy
    """
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy as MediaBuyModel

    principal_id = _get_principal_id_from_context(context)
    tenant = get_current_tenant()

    # Query database for media buy
    with get_db_session() as session:
        stmt = select(MediaBuyModel).where(
            MediaBuyModel.media_buy_id == media_buy_id, MediaBuyModel.tenant_id == tenant["tenant_id"]
        )
        media_buy = session.scalars(stmt).first()

        if not media_buy:
            raise ValueError(f"Media buy '{media_buy_id}' not found.")

        if media_buy.principal_id != principal_id:
            # Log security violation
            from src.core.audit_logger import get_audit_logger

            security_logger = get_audit_logger("AdCP", tenant["tenant_id"])
            security_logger.log_security_violation(
                operation="access_media_buy",
                principal_id=principal_id,
                resource_id=media_buy_id,
                reason=f"Principal does not own media buy (owner: {media_buy.principal_id})",
            )
            raise PermissionError(f"Principal '{principal_id}' does not own media buy '{media_buy_id}'.")


# --- Activity Feed Helper ---


def log_tool_activity(context: Context, tool_name: str, start_time: float = None):
    """Log tool activity to the activity feed."""
    try:
        # Get principal and tenant context
        principal_id, tenant = get_principal_from_context(context)

        # Set tenant context if returned
        if tenant:
            set_current_tenant(tenant)
        else:
            tenant = get_current_tenant()

        if not tenant:
            return
        principal_name = "Unknown"

        if principal_id:
            with get_db_session() as session:
                stmt = select(ModelPrincipal).filter_by(principal_id=principal_id, tenant_id=tenant["tenant_id"])
                principal = session.scalars(stmt).first()
                if principal:
                    principal_name = principal.name

        # Calculate response time if start_time provided
        response_time_ms = None
        if start_time:
            response_time_ms = int((time.time() - start_time) * 1000)

        # Log to activity feed (for WebSocket real-time updates)
        activity_feed.log_api_call(
            tenant_id=tenant["tenant_id"],
            principal_name=principal_name,
            method=tool_name,
            status_code=200,
            response_time_ms=response_time_ms,
        )

        # Also log to audit logs (for persistent dashboard activity feed)
        audit_logger = get_audit_logger("MCP", tenant["tenant_id"])
        details = {"tool": tool_name, "status": "success"}
        if response_time_ms:
            details["response_time_ms"] = response_time_ms

        audit_logger.log_operation(
            operation=tool_name,
            principal_name=principal_name,
            principal_id=principal_id or "anonymous",
            adapter_id="mcp_server",
            success=True,
            details=details,
        )
    except Exception as e:
        # Don't let activity logging break the main flow
        console.print(f"[yellow]Activity logging error: {e}[/yellow]")


# --- MCP Tools (Full Implementation) ---


async def _get_products_impl(req: GetProductsRequestGenerated, context: Context) -> GetProductsResponse:
    """Shared implementation for get_products.

    Contains all business logic for product discovery including policy checks,
    product catalog providers, dynamic pricing, and filtering.

    Args:
        req: GetProductsRequest from generated schemas
        context: FastMCP Context for tenant/principal resolution

    Returns:
        GetProductsResponse containing matching products
    """
    import sys

    print("=" * 80, file=sys.stderr, flush=True)
    print(
        f"🔧 _get_products_impl CALLED: brand_manifest={req.brand_manifest}, brief={req.brief[:50] if req.brief else 'N/A'}",
        file=sys.stderr,
        flush=True,
    )
    print("=" * 80, file=sys.stderr, flush=True)

    from src.core.tool_context import ToolContext

    start_time = time.time()

    # Handle both old Context and new ToolContext
    if isinstance(context, ToolContext):
        # New context management - everything is already extracted
        testing_ctx_raw = context.testing_context
        # Convert dict testing context back to TestingContext object if needed
        if isinstance(testing_ctx_raw, dict):
            from src.core.testing_hooks import TestingContext

            testing_ctx = TestingContext(**testing_ctx_raw)
        else:
            testing_ctx = testing_ctx_raw
        principal_id = context.principal_id
        tenant = {"tenant_id": context.tenant_id}  # Simplified tenant info
    else:
        # Legacy path - extract from FastMCP Context
        testing_ctx = get_testing_context(context)
        # For discovery endpoints, authentication is optional
        # require_valid_token=False means invalid tokens are treated like missing tokens (discovery endpoint behavior)
        logger.info("[GET_PRODUCTS] About to call get_principal_from_context")
        print("🔍 [GET_PRODUCTS DEBUG] About to call get_principal_from_context", flush=True)
        principal_id, tenant = get_principal_from_context(
            context, require_valid_token=False
        )  # Returns (None, tenant) if no/invalid auth
        logger.info(f"[GET_PRODUCTS] principal_id returned: {principal_id}, tenant: {tenant}")
        print(f"🔍 [GET_PRODUCTS DEBUG] principal_id returned: {principal_id}, tenant: {tenant}", flush=True)

        # Set tenant context explicitly in this async context (ContextVar propagation fix)
        if tenant:
            set_current_tenant(tenant)
            logger.info(f"[GET_PRODUCTS] Set tenant context: {tenant['tenant_id']}")
            print(f"🔍 [GET_PRODUCTS DEBUG] Set tenant context: {tenant['tenant_id']}", flush=True)
        elif principal_id:
            # If we have principal but no tenant, something went wrong
            logger.error(f"[GET_PRODUCTS] Principal found but no tenant context: principal_id={principal_id}")
            print("❌ [GET_PRODUCTS DEBUG] Principal found but no tenant context", flush=True)
            raise ToolError(
                f"Authentication succeeded but tenant context missing. " f"This is a bug. principal_id={principal_id}"
            )
        # else: No auth provided, which is OK for discovery endpoints

    # Get the Principal object with ad server mappings
    principal = get_principal_object(principal_id) if principal_id else None
    principal_data = principal.model_dump() if principal else None

    # Extract offering text from brand_manifest
    offering = None
    if req.brand_manifest:
        if isinstance(req.brand_manifest, str):
            # brand_manifest is a URL - use it as-is for now
            # TODO: In future, fetch and parse the URL
            offering = f"Brand at {req.brand_manifest}"
        else:
            # brand_manifest is a BrandManifest object or dict
            # Try to access as object first, then as dict
            if hasattr(req.brand_manifest, "name"):
                offering = req.brand_manifest.name
            elif isinstance(req.brand_manifest, dict):
                offering = req.brand_manifest.get("name", "")

    if not offering:
        raise ToolError("brand_manifest must provide brand information")

    # Skip strict validation in test environments (allow simple test values)
    import os

    is_test_mode = (testing_ctx and testing_ctx.test_session_id is not None) or os.getenv("ADCP_TESTING") == "true"

    # Note: brand_manifest validation is handled by Pydantic schema, no need for runtime validation here

    # Check policy compliance first (if enabled)
    advertising_policy = safe_parse_json_field(
        tenant.get("advertising_policy"), field_name="advertising_policy", default={}
    )

    # Only run policy checks if enabled in tenant settings
    policy_check_enabled = advertising_policy.get("enabled", False)  # Default to False for new tenants
    policy_disabled_reason = None

    if not policy_check_enabled:
        # Skip policy checks if disabled
        policy_result = None
        policy_disabled_reason = "disabled_by_tenant"
        logger.info(f"Policy checks disabled for tenant {tenant['tenant_id']}")
    else:
        # Get tenant's Gemini API key for policy checks
        tenant_gemini_key = tenant.get("gemini_api_key")
        if not tenant_gemini_key:
            # No API key - cannot run policy checks
            policy_result = None
            policy_disabled_reason = "no_gemini_api_key"
            logger.warning(f"Policy checks enabled but no Gemini API key configured for tenant {tenant['tenant_id']}")
        else:
            policy_service = PolicyCheckService(gemini_api_key=tenant_gemini_key)

            # Use advertising_policy settings for tenant-specific rules
            tenant_policies = advertising_policy if advertising_policy else {}

            # Convert brand_manifest to dict if it's a BrandManifest object
            brand_manifest_dict = None
            if req.brand_manifest:
                if hasattr(req.brand_manifest, "model_dump"):
                    brand_manifest_dict = req.brand_manifest.model_dump()
                elif isinstance(req.brand_manifest, dict):
                    brand_manifest_dict = req.brand_manifest
                else:
                    brand_manifest_dict = req.brand_manifest  # URL string

            try:
                policy_result = await policy_service.check_brief_compliance(
                    brief=req.brief,
                    promoted_offering=offering,  # Use extracted offering from brand_manifest
                    brand_manifest=brand_manifest_dict,
                    tenant_policies=tenant_policies if tenant_policies else None,
                )

                # Log successful policy check
                audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
                audit_logger.log_operation(
                    operation="policy_check",
                    principal_name=principal_id or "anonymous",
                    principal_id=principal_id or "anonymous",
                    adapter_id="policy_service",
                    success=policy_result.status != PolicyStatus.BLOCKED,
                    details={
                        "brief": req.brief[:100] + "..." if len(req.brief) > 100 else req.brief,
                        "brand_name": offering[:100] + "..." if offering and len(offering) > 100 else offering,
                        "policy_status": policy_result.status,
                        "reason": policy_result.reason,
                        "restrictions": policy_result.restrictions,
                    },
                )

            except Exception as e:
                # Policy check failed - log error
                logger.error(f"Policy check failed for tenant {tenant['tenant_id']}: {e}")
                audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
                audit_logger.log_operation(
                    operation="policy_check_failure",
                    principal_name=principal_id or "anonymous",
                    principal_id=principal_id or "anonymous",
                    adapter_id="policy_service",
                    success=False,
                    details={
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "brief": req.brief[:100] + "..." if len(req.brief) > 100 else req.brief,
                    },
                )

                # Fail open by default (allow campaigns) with warning in response
                policy_result = None
                policy_disabled_reason = f"service_error: {type(e).__name__}"
                logger.warning(f"Policy check failed, allowing campaign by default: {e}")

    # Handle policy result based on settings
    if policy_result and policy_result.status == PolicyStatus.BLOCKED:
        # Always block if policy says blocked
        logger.warning(f"Brief blocked by policy: {policy_result.reason}")
        # Raise ToolError to properly signal failure to client
        raise ToolError("POLICY_VIOLATION", policy_result.reason)

    # If restricted and manual review is required, create a task
    if (
        policy_result
        and policy_result.status == PolicyStatus.RESTRICTED
        and advertising_policy.get("require_manual_review", False)
    ):
        # Create a manual review task
        from src.core.database.database_session import get_db_session

        with get_db_session() as session:
            task_id = f"policy_review_{tenant['tenant_id']}_{int(datetime.now(UTC).timestamp())}"

            # Log policy violation for audit trail and compliance
            audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
            audit_logger.log_operation(
                operation="get_products_policy_violation",
                principal_name=principal_id,
                principal_id=principal_id,
                adapter_id="policy_engine",
                success=False,
                details={
                    "brief": req.brief,
                    "brand_name": offering,
                    "policy_status": policy_result.status,
                    "restrictions": policy_result.restrictions,
                    "reason": policy_result.reason,
                },
            )

        # Raise error for policy violations - explicit failure, not silent return
        raise ToolError(
            "POLICY_VIOLATION",
            f"Request violates content policy: {policy_result.reason}. Restrictions: {', '.join(policy_result.restrictions)}",
        )

    # Determine product catalog configuration based on tenant's signals discovery settings
    catalog_config = {"provider": "database", "config": {}}  # Default to database provider

    # Check if signals discovery is configured for this tenant
    if hasattr(tenant, "signals_agent_config") and tenant.get("signals_agent_config"):
        signals_config = tenant["signals_agent_config"]

        # Parse signals config if it's a string (SQLite) vs dict (PostgreSQL JSONB)
        if isinstance(signals_config, str):
            import json

            try:
                signals_config = json.loads(signals_config)
            except json.JSONDecodeError:
                logger.error(f"Invalid signals_agent_config JSON for tenant {tenant['tenant_id']}")
                signals_config = {}

        # If signals discovery is enabled, use hybrid provider
        if isinstance(signals_config, dict) and signals_config.get("enabled", False):
            logger.info(f"Using hybrid provider with signals discovery for tenant {tenant['tenant_id']}")
            catalog_config = {
                "provider": "hybrid",
                "config": {
                    "database": {},  # Use database provider defaults
                    "signals_discovery": signals_config,
                    "ranking_strategy": "signals_first",  # Prioritize signals-enhanced products
                    "max_products": 20,
                    "deduplicate": True,
                },
            }

    # Get the product catalog provider for this tenant
    provider = await get_product_catalog_provider(
        tenant["tenant_id"],
        catalog_config,
    )

    # Query products using the brief, including context for signals forwarding
    context_data = {
        "brand_name": offering,
        "tenant_id": tenant["tenant_id"],
        "principal_id": principal_id,
    }

    logger.info(f"[GET_PRODUCTS] Calling provider.get_products for tenant_id={tenant['tenant_id']}")
    print(f"🔍 [GET_PRODUCTS DEBUG] Calling provider.get_products for tenant_id={tenant['tenant_id']}", flush=True)
    products = await provider.get_products(
        brief=req.brief,
        tenant_id=tenant["tenant_id"],
        principal_id=principal_id,
        principal_data=principal_data,
        context=context_data,
    )
    logger.info(f"[GET_PRODUCTS] Got {len(products)} products from provider")
    print(f"🔍 [GET_PRODUCTS DEBUG] Got {len(products)} products from provider", flush=True)

    # Enrich products with dynamic pricing (AdCP PR #79)
    # Calculate floor_cpm, recommended_cpm, estimated_exposures from cached metrics
    try:
        from src.core.database.database_session import get_db_session
        from src.services.dynamic_pricing_service import DynamicPricingService

        # Extract country from request if available (future enhancement: parse from targeting)
        country_code = None  # TODO: Extract from targeting if provided

        with get_db_session() as pricing_session:
            pricing_service = DynamicPricingService(pricing_session)
            products = pricing_service.enrich_products_with_pricing(
                products,
                tenant_id=tenant["tenant_id"],
                country_code=country_code,
                min_exposures=getattr(req, "min_exposures", None),
            )
    except Exception as e:
        logger.warning(f"Failed to enrich products with dynamic pricing: {e}. Using defaults.")

    # Apply AdCP filters if provided
    if req.filters:
        filtered_products = []
        for product in products:
            # Filter by delivery_type
            if req.filters.delivery_type and product.delivery_type != req.filters.delivery_type:
                continue

            # Filter by is_fixed_price (check pricing_options)
            if req.filters.is_fixed_price is not None:
                # Check if product has any pricing option matching the fixed/auction filter
                has_matching_pricing = any(po.is_fixed == req.filters.is_fixed_price for po in product.pricing_options)
                if not has_matching_pricing:
                    continue

            # Filter by format_types
            if req.filters.format_types:
                # Product.formats is list[str] (format IDs), need to look up types from FORMAT_REGISTRY
                from src.core.schemas import get_format_by_id

                product_format_types = set()
                for format_id in product.formats:
                    if isinstance(format_id, str):
                        format_obj = get_format_by_id(format_id)
                        if format_obj:
                            product_format_types.add(format_obj.type)
                    elif hasattr(format_id, "type"):
                        # Already a Format object
                        product_format_types.add(format_id.type)

                if not any(fmt_type in product_format_types for fmt_type in req.filters.format_types):
                    continue

            # Filter by format_ids
            if req.filters.format_ids:
                # Product.formats is list[str] or list[dict] (format IDs)
                product_format_ids = set()
                for format_id in product.formats:
                    if isinstance(format_id, str):
                        product_format_ids.add(format_id)
                    elif isinstance(format_id, dict):
                        # Dict with 'id' key (from database)
                        product_format_ids.add(format_id.get("id"))
                    elif hasattr(format_id, "id"):
                        # FormatId object (has .id attribute, not .format_id)
                        product_format_ids.add(format_id.id)

                # req.filters.format_ids contains FormatId objects, extract .id from them
                request_format_ids = set()
                for fmt_id in req.filters.format_ids:
                    if isinstance(fmt_id, str):
                        request_format_ids.add(fmt_id)
                    elif hasattr(fmt_id, "id"):
                        # FormatId object
                        request_format_ids.add(fmt_id.id)
                    elif isinstance(fmt_id, dict):
                        request_format_ids.add(fmt_id.get("id"))

                if not any(fmt_id in product_format_ids for fmt_id in request_format_ids):
                    continue

            # Filter by standard_formats_only
            if req.filters.standard_formats_only:
                # Check if all formats are IAB standard formats
                # IAB standard formats typically follow patterns like "display_", "video_", "audio_", "native_"
                has_only_standard = True
                for format_id in product.formats:
                    format_id_str = None
                    if isinstance(format_id, str):
                        format_id_str = format_id
                    elif isinstance(format_id, dict):
                        format_id_str = format_id.get("id")
                    elif hasattr(format_id, "id"):
                        # FormatId object (has .id attribute, not .format_id)
                        format_id_str = format_id.id

                    if format_id_str and not format_id_str.startswith(("display_", "video_", "audio_", "native_")):
                        has_only_standard = False
                        break

                if not has_only_standard:
                    continue

            # Product passed all filters
            filtered_products.append(product)

        products = filtered_products
        logger.info(f"Applied filters: {req.filters.model_dump(exclude_none=True)}. {len(products)} products remain.")

    # Filter products based on policy compliance (if policy checks are enabled)
    eligible_products = []
    if policy_result and policy_check_enabled:
        # Policy checks are enabled - filter products based on policy compliance
        for product in products:
            is_eligible, reason = policy_service.check_product_eligibility(policy_result, product.model_dump())

            if is_eligible:
                # Product passed policy checks - add to eligible products
                # Note: policy_compliance field removed in AdCP v2.4
                eligible_products.append(product)
            else:
                logger.info(f"Product {product.product_id} excluded: {reason}")
    else:
        # Policy checks disabled - all products are eligible
        eligible_products = products

    # Apply min_exposures filtering (AdCP PR #79)
    min_exposures = getattr(req, "min_exposures", None)
    if min_exposures is not None:
        filtered_products = []
        for product in eligible_products:
            # For guaranteed products, check estimated_exposures
            if product.delivery_type == "guaranteed":
                if product.estimated_exposures is not None and product.estimated_exposures >= min_exposures:
                    filtered_products.append(product)
                else:
                    logger.info(
                        f"Product {product.product_id} excluded: estimated_exposures "
                        f"({product.estimated_exposures}) < min_exposures ({min_exposures})"
                    )
            else:
                # For non-guaranteed, include if recommended_cpm is set (indicates it can meet min_exposures)
                # or if no recommended_cpm is set (product doesn't provide exposure estimates)
                if product.recommended_cpm is not None:
                    filtered_products.append(product)
                else:
                    # Include non-guaranteed products without recommended_cpm (can't filter by exposure estimates)
                    filtered_products.append(product)
        eligible_products = filtered_products

    # Apply testing hooks to response
    response_data = {"products": [p.model_dump_internal() for p in eligible_products]}
    response_data = apply_testing_hooks(response_data, testing_ctx, "get_products")

    # Reconstruct products from modified data
    modified_products = [Product(**p) for p in response_data["products"]]

    # Annotate pricing options with adapter support (AdCP PR #88)
    if principal and modified_products:
        try:
            from src.adapters import get_adapter

            adapter = get_adapter(principal, dry_run=True)
            supported_models = adapter.get_supported_pricing_models()

            for product in modified_products:
                if product.pricing_options:
                    # Annotate each pricing option with "supported" flag
                    for option in product.pricing_options:
                        pricing_model = (
                            option.pricing_model.value
                            if hasattr(option.pricing_model, "value")
                            else option.pricing_model
                        )
                        # Add supported annotation (will be included in response)
                        option.supported = pricing_model in supported_models
                        if not option.supported:
                            option.unsupported_reason = (
                                f"Current adapter does not support {pricing_model.upper()} pricing"
                            )
        except Exception as e:
            logger.warning(f"Failed to annotate pricing options with adapter support: {e}")

    # Filter pricing data for anonymous users
    if principal_id is None:  # Anonymous user
        # Remove pricing data from products for anonymous users
        # Set to empty list to hide pricing (will be excluded during serialization)
        for product in modified_products:
            product.pricing_options = []

    # Log activity
    log_tool_activity(context, "get_products", start_time)

    # Set status based on operation result
    status = TaskStatus.from_operation_state(
        operation_type="discovery", has_errors=False, requires_approval=False, requires_auth=principal_id is None
    )

    # Response __str__() will generate appropriate message based on content
    return GetProductsResponse(products=modified_products, status=status)


@mcp.tool()
async def get_products(
    brand_manifest: Any | None = None,  # BrandManifest | str | None - validated by Pydantic
    brief: str = "",
    filters: dict | None = None,
    context: Context = None,
) -> GetProductsResponse:
    """Get available products matching the brief.

    MCP tool wrapper that delegates to the shared implementation.

    Args:
        brand_manifest: Brand information manifest (inline object or URL string)
        brief: Brief description of the advertising campaign or requirements (optional)
        filters: Structured filters for product discovery (optional)
        context: FastMCP context (automatically provided)

    Returns:
        GetProductsResponse containing matching products

    Note:
        promoted_offering is deprecated - use brand_manifest instead.
        If you need backward compatibility, use the A2A interface which still supports it.
    """
    import sys

    print("=" * 80, file=sys.stderr, flush=True)
    print(
        f"🚀 MCP get_products CALLED: brand_manifest={brand_manifest}, brief={brief[:50] if brief else 'N/A'}",
        file=sys.stderr,
        flush=True,
    )
    print("=" * 80, file=sys.stderr, flush=True)

    # Build request object for shared implementation using helper
    try:
        req = create_get_products_request(
            promoted_offering=None,  # Not exposed in MCP tool (use brand_manifest)
            brief=brief,
            brand_manifest=brand_manifest,
            filters=filters,
        )
    except ValidationError as e:
        raise ToolError(format_validation_error(e, context="get_products request")) from e
    except ValueError as e:
        # Convert ValueError from helper to ToolError with clear message
        raise ToolError(f"Invalid get_products request: {e}") from e

    # Call shared implementation
    # Note: GetProductsRequest is now a flat class (not RootModel), so pass req directly
    return await _get_products_impl(req, context)


def _list_creative_formats_impl(
    req: ListCreativeFormatsRequest | None, context: Context
) -> ListCreativeFormatsResponse:
    """List all available creative formats (AdCP spec endpoint).

    Returns formats from all registered creative agents (default + tenant-specific).
    Uses CreativeAgentRegistry for dynamic format discovery with caching.
    Supports optional filtering by type, standard_only, category, and format_ids.
    """
    start_time = time.time()

    # Use default request if none provided
    if req is None:
        req = ListCreativeFormatsRequest()

    # For discovery endpoints, authentication is optional
    # require_valid_token=False means invalid tokens are treated like missing tokens (discovery endpoint behavior)
    principal_id, tenant = get_principal_from_context(
        context, require_valid_token=False
    )  # Returns (None, tenant) if no/invalid auth

    # Set tenant context if returned
    if tenant:
        set_current_tenant(tenant)
    else:
        tenant = get_current_tenant()
    if not tenant:
        raise ToolError("No tenant context available")

    # Get formats from all registered creative agents via registry
    import asyncio

    from src.core.creative_agent_registry import get_creative_agent_registry

    registry = get_creative_agent_registry()

    # Run async operation - check if we're already in an async context
    try:
        # Check if there's already a running event loop
        loop = asyncio.get_running_loop()
        # We're in an async context, run in thread pool to avoid nested loop error
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(lambda: asyncio.run(registry.list_all_formats(tenant_id=tenant["tenant_id"])))
            formats = future.result()
    except RuntimeError:
        # No running loop, safe to create one
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            formats = loop.run_until_complete(registry.list_all_formats(tenant_id=tenant["tenant_id"]))
        finally:
            loop.close()

    # Apply filters from request
    if req.type:
        formats = [f for f in formats if f.type == req.type]

    if req.standard_only:
        formats = [f for f in formats if f.is_standard]

    if req.category:
        # Category maps to is_standard: "standard" -> True, "custom" -> False
        if req.category == "standard":
            formats = [f for f in formats if f.is_standard]
        elif req.category == "custom":
            formats = [f for f in formats if not f.is_standard]

    if req.format_ids:
        # Filter to only the specified format IDs
        format_ids_set = set(req.format_ids)
        formats = [f for f in formats if f.format_id in format_ids_set]

    # Sort formats by type and name for consistent ordering
    formats.sort(key=lambda f: (f.type, f.name))

    # Log the operation
    audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
    audit_logger.log_operation(
        operation="list_creative_formats",
        principal_name=principal_id or "anonymous",
        principal_id=principal_id or "anonymous",
        adapter_id="N/A",
        success=True,
        details={
            "format_count": len(formats),
            "standard_formats": len([f for f in formats if f.is_standard]),
            "custom_formats": len([f for f in formats if not f.is_standard]),
            "format_types": list({f.type for f in formats}),
        },
    )

    # Log activity
    log_tool_activity(context, "list_creative_formats", start_time)

    # Set status based on operation result
    status = TaskStatus.from_operation_state(
        operation_type="discovery", has_errors=False, requires_approval=False, requires_auth=principal_id is None
    )

    # Create response (no message/specification_version - not in adapter schema)
    response = ListCreativeFormatsResponse(formats=formats, status=status)

    # Add schema validation metadata for client validation
    from src.core.schema_validation import INCLUDE_SCHEMAS_IN_RESPONSES, enhance_mcp_response_with_schema

    if INCLUDE_SCHEMAS_IN_RESPONSES:
        # Convert to dict, enhance with schema, return enhanced dict
        response_dict = response.model_dump()
        enhanced_response = enhance_mcp_response_with_schema(
            response_data=response_dict,
            model_class=ListCreativeFormatsResponse,
            include_full_schema=False,  # Set to True for development debugging
        )
        # Return the enhanced response (FastMCP handles dict returns)
        return enhanced_response

    return response


@mcp.tool()
def list_creative_formats(
    type: str | None = None,
    standard_only: bool | None = None,
    category: str | None = None,
    format_ids: list[str] | None = None,
    webhook_url: str | None = None,
    context: Context = None,
) -> ListCreativeFormatsResponse:
    """List all available creative formats (AdCP spec endpoint).

    MCP tool wrapper that delegates to the shared implementation.

    Args:
        type: Filter by format type (audio, video, display)
        standard_only: Only return IAB standard formats
        category: Filter by format category (standard, custom)
        format_ids: Filter by specific format IDs
        webhook_url: URL for async task completion notifications (AdCP spec, optional)
        context: FastMCP context (automatically provided)

    Returns:
        ListCreativeFormatsResponse with all available formats
    """
    try:
        req = ListCreativeFormatsRequest(
            type=type,
            standard_only=standard_only,
            category=category,
            format_ids=format_ids,
        )
    except ValidationError as e:
        raise ToolError(format_validation_error(e, context="list_creative_formats request")) from e

    return _list_creative_formats_impl(req, context)


def _sync_creatives_impl(
    creatives: list[dict],
    patch: bool = False,
    assignments: dict = None,
    delete_missing: bool = False,
    dry_run: bool = False,
    validation_mode: str = "strict",
    push_notification_config: dict | None = None,
    context: Context = None,
) -> SyncCreativesResponse:
    """Sync creative assets to centralized library (AdCP v2.4 spec compliant endpoint).

    Primary creative management endpoint that handles:
    - Bulk creative upload/update with upsert semantics
    - Creative assignment to media buy packages via assignments dict
    - Support for both hosted assets (media_url) and third-party tags (snippet)
    - Patch updates, dry-run mode, and validation options

    Args:
        creatives: Array of creative assets to sync
        patch: When true, only update provided fields (partial update). When false, full upsert.
        assignments: Bulk assignment map of creative_id to package_ids (spec-compliant)
        delete_missing: Delete creatives not in sync payload (use with caution)
        dry_run: Preview changes without applying them
        validation_mode: Validation strictness (strict or lenient)
        push_notification_config: Push notification config for status updates (AdCP spec, optional)
        context: FastMCP context (automatically provided)

    Returns:
        SyncCreativesResponse with synced creatives and assignments
    """
    from pydantic import ValidationError

    from src.core.schemas import Creative

    # Process raw creative dictionaries without schema validation initially
    # Schema objects will be created later with populated internal fields
    raw_creatives = [creative if isinstance(creative, dict) else creative.model_dump() for creative in creatives]

    start_time = time.time()

    # Authentication
    principal_id = _get_principal_id_from_context(context)

    # Get tenant information
    # If context is ToolContext (A2A), tenant is already set, but verify it matches
    from src.core.tool_context import ToolContext

    if isinstance(context, ToolContext):
        # Tenant context should already be set by A2A handler, but verify
        tenant = get_current_tenant()
        if not tenant or tenant.get("tenant_id") != context.tenant_id:
            # Tenant context wasn't set properly - this shouldn't happen but handle it
            console.print(
                f"[yellow]Warning: Tenant context mismatch, setting from ToolContext: {context.tenant_id}[/yellow]"
            )
            # We need to load the tenant properly - for now use the ID from context
            tenant = {"tenant_id": context.tenant_id}
    else:
        # FastMCP path - tenant should be set by get_principal_from_context
        tenant = get_current_tenant()

    if not tenant:
        raise ToolError("No tenant context available")

    # Track actions per creative for AdCP-compliant response
    from src.core.schemas import SyncCreativeResult

    results: list[SyncCreativeResult] = []
    created_count = 0
    updated_count = 0
    unchanged_count = 0
    failed_count = 0

    # Legacy tracking (still used internally)
    synced_creatives = []
    failed_creatives = []

    # Track creatives requiring approval for workflow creation
    creatives_needing_approval = []

    # Extract webhook URL from push_notification_config for AI review callbacks
    webhook_url = None
    if push_notification_config:
        webhook_url = push_notification_config.get("url")
        logger.info(f"[sync_creatives] Push notification webhook URL: {webhook_url}")

    # Get tenant creative approval settings
    # approval_mode: "auto-approve", "require-human", "ai-powered"
    logger.info(f"[sync_creatives] Tenant dict keys: {list(tenant.keys())}")
    logger.info(f"[sync_creatives] Tenant approval_mode field: {tenant.get('approval_mode', 'NOT FOUND')}")
    approval_mode = tenant.get("approval_mode", "require-human")
    logger.info(f"[sync_creatives] Final approval mode: {approval_mode} (from tenant: {tenant.get('tenant_id')})")

    with get_db_session() as session:
        # Process each creative with proper transaction isolation
        for creative in raw_creatives:
            try:
                # First, validate the creative against the schema before database operations
                try:
                    # Create temporary schema object for validation
                    # Map input fields to schema field names
                    schema_data = {
                        "creative_id": creative.get("creative_id") or str(uuid.uuid4()),
                        "name": creative.get("name", ""),  # Ensure name is never None
                        "format_id": creative.get("format_id") or creative.get("format"),  # Support both field names
                        "click_through_url": creative.get("click_url") or creative.get("click_through_url"),
                        "width": creative.get("width"),
                        "height": creative.get("height"),
                        "duration": creative.get("duration"),
                        "principal_id": principal_id,
                        "created_at": datetime.now(UTC),
                        "updated_at": datetime.now(UTC),
                        "status": "pending",
                    }

                    # Handle assets vs media content
                    if creative.get("assets"):
                        # Asset-based creative (new AdCP format)
                        schema_data["content_uri"] = creative.get("url") or f"asset://{creative.get('creative_id')}"
                    else:
                        # Media-based creative (legacy)
                        schema_data["content_uri"] = (
                            creative.get("url") or "https://placeholder.example.com/missing.jpg"
                        )

                    if creative.get("template_variables"):
                        schema_data["template_variables"] = creative.get("template_variables")

                    # Validate by creating a Creative schema object
                    # This will fail if required fields are missing or invalid (like empty name)
                    # Also auto-upgrades string format_ids to FormatId objects via validator
                    validated_creative = Creative(**schema_data)

                    # Additional business logic validation
                    if not creative.get("name") or str(creative.get("name")).strip() == "":
                        raise ValueError("Creative name cannot be empty")

                    if not creative.get("format_id") and not creative.get("format"):
                        raise ValueError("Creative format is required")

                    # Use validated format (auto-upgraded from string if needed)
                    format_value = validated_creative.format

                except (ValidationError, ValueError) as validation_error:
                    # Creative failed validation - add to failed list
                    creative_id = creative.get("creative_id", "unknown")
                    # Format ValidationError nicely for clients, pass through ValueError as-is
                    if isinstance(validation_error, ValidationError):
                        error_msg = format_validation_error(validation_error, context=f"creative {creative_id}")
                    else:
                        error_msg = str(validation_error)
                    failed_creatives.append({"creative_id": creative_id, "error": error_msg})
                    failed_count += 1
                    results.append(
                        SyncCreativeResult(
                            creative_id=creative_id,
                            action="failed",
                            errors=[error_msg],
                        )
                    )
                    continue  # Skip to next creative

                # Use savepoint for individual creative transaction isolation
                with session.begin_nested():
                    # Check if creative already exists (always check for upsert/patch behavior)
                    # SECURITY: Must filter by principal_id to prevent cross-principal modification
                    existing_creative = None
                    if creative.get("creative_id"):
                        from src.core.database.models import Creative as DBCreative

                        # Query for existing creative with security filter
                        stmt = select(DBCreative).filter_by(
                            tenant_id=tenant["tenant_id"],
                            principal_id=principal_id,  # SECURITY: Prevent cross-principal modification
                            creative_id=creative.get("creative_id"),
                        )
                        existing_creative = session.scalars(stmt).first()

                    if existing_creative:
                        # Update existing creative (respects patch vs full upsert)
                        existing_creative.updated_at = datetime.now(UTC)

                        # Track changes for result
                        changes = []

                        # Update fields based on patch mode
                        if patch:
                            # Patch mode: only update provided fields
                            if creative.get("name") is not None and creative.get("name") != existing_creative.name:
                                existing_creative.name = creative.get("name")
                                changes.append("name")
                            if creative.get("format_id") or creative.get("format"):
                                # Use validated format_value (already auto-upgraded from string)
                                new_agent_url, new_format = _extract_format_namespace(format_value)
                                if (
                                    new_agent_url != existing_creative.agent_url
                                    or new_format != existing_creative.format
                                ):
                                    existing_creative.agent_url = new_agent_url
                                    existing_creative.format = new_format
                                    changes.append("format")
                        else:
                            # Full upsert mode: replace all fields
                            if creative.get("name") != existing_creative.name:
                                existing_creative.name = creative.get("name")
                                changes.append("name")
                            # Use validated format_value (already auto-upgraded from string)
                            new_agent_url, new_format = _extract_format_namespace(format_value)
                            if new_agent_url != existing_creative.agent_url or new_format != existing_creative.format:
                                existing_creative.agent_url = new_agent_url
                                existing_creative.format = new_format
                                changes.append("format")

                        # Determine creative status based on approval mode
                        creative_format = creative.get("format_id") or creative.get("format")
                        if creative_format:  # Only update approval status if format is provided
                            if approval_mode == "auto-approve":
                                existing_creative.status = "approved"
                                needs_approval = False
                            elif approval_mode == "ai-powered":
                                # Submit to background AI review (async)

                                from src.admin.blueprints.creatives import (
                                    _ai_review_executor,
                                    _ai_review_lock,
                                    _ai_review_tasks,
                                )

                                # Set status to pending immediately
                                existing_creative.status = "pending"
                                needs_approval = True

                                # Submit background task
                                task_id = f"ai_review_{existing_creative.creative_id}_{uuid.uuid4().hex[:8]}"

                                # Need to flush to ensure creative_id is available
                                session.flush()

                                # Import the async function
                                from src.admin.blueprints.creatives import _ai_review_creative_async

                                future = _ai_review_executor.submit(
                                    _ai_review_creative_async,
                                    creative_id=existing_creative.creative_id,
                                    tenant_id=tenant["tenant_id"],
                                    webhook_url=webhook_url,
                                    slack_webhook_url=tenant.get("slack_webhook_url"),
                                    principal_name=principal_id,
                                )

                                # Track the task
                                with _ai_review_lock:
                                    _ai_review_tasks[task_id] = {
                                        "future": future,
                                        "creative_id": existing_creative.creative_id,
                                        "created_at": time.time(),
                                    }

                                logger.info(
                                    f"[sync_creatives] Submitted AI review for {existing_creative.creative_id} (task: {task_id})"
                                )
                            else:  # require-human
                                existing_creative.status = "pending"
                                needs_approval = True
                        else:
                            needs_approval = False

                        # Store creative properties in data field
                        if patch:
                            # Patch mode: merge with existing data
                            data = existing_creative.data or {}
                            if creative.get("url") is not None and data.get("url") != creative.get("url"):
                                data["url"] = creative.get("url")
                                changes.append("url")
                            if creative.get("click_url") is not None and data.get("click_url") != creative.get(
                                "click_url"
                            ):
                                data["click_url"] = creative.get("click_url")
                                changes.append("click_url")
                            if creative.get("width") is not None and data.get("width") != creative.get("width"):
                                data["width"] = creative.get("width")
                                changes.append("width")
                            if creative.get("height") is not None and data.get("height") != creative.get("height"):
                                data["height"] = creative.get("height")
                                changes.append("height")
                            if creative.get("duration") is not None and data.get("duration") != creative.get(
                                "duration"
                            ):
                                data["duration"] = creative.get("duration")
                                changes.append("duration")
                            if creative.get("assets") is not None:
                                data["assets"] = creative.get("assets")
                                changes.append("assets")
                            if creative.get("template_variables") is not None:
                                data["template_variables"] = creative.get("template_variables")
                                changes.append("template_variables")
                        else:
                            # Full upsert mode: replace all data
                            data = {
                                "url": creative.get("url"),
                                "click_url": creative.get("click_url"),
                                "width": creative.get("width"),
                                "height": creative.get("height"),
                                "duration": creative.get("duration"),
                            }
                            if creative.get("assets"):
                                data["assets"] = creative.get("assets")
                            if creative.get("template_variables"):
                                data["template_variables"] = creative.get("template_variables")

                            # ALWAYS validate updates with creative agent
                            if creative_format:
                                try:
                                    # Get format to find creative agent URL

                                    from src.core.creative_agent_registry import get_creative_agent_registry

                                    registry = get_creative_agent_registry()

                                    # List all formats to find the matching one
                                    all_formats = run_async_in_sync_context(
                                        registry.list_all_formats(tenant_id=tenant["tenant_id"])
                                    )

                                    # Find matching format
                                    format_obj = None
                                    for fmt in all_formats:
                                        if fmt.format_id == creative_format:
                                            format_obj = fmt
                                            break

                                    if format_obj and format_obj.agent_url:
                                        # Check if format is generative (has output_format_ids)
                                        is_generative = bool(getattr(format_obj, "output_format_ids", None))

                                        if is_generative:
                                            # Generative creative update - rebuild using AI
                                            logger.info(
                                                f"[sync_creatives] Detected generative format update: {creative_format}, "
                                                f"checking for Gemini API key"
                                            )

                                            # Get Gemini API key from config
                                            from src.core.config import get_config

                                            config = get_config()
                                            gemini_api_key = config.gemini_api_key

                                            if not gemini_api_key:
                                                error_msg = (
                                                    f"Cannot update generative creative {creative_format}: "
                                                    f"GEMINI_API_KEY not configured"
                                                )
                                                logger.error(f"[sync_creatives] {error_msg}")
                                                raise ValueError(error_msg)

                                            # Extract message/brief from assets or inputs
                                            message = None
                                            if creative.get("assets"):
                                                assets = creative.get("assets", {})
                                                for role, asset in assets.items():
                                                    if role in ["message", "brief", "prompt"] and isinstance(
                                                        asset, dict
                                                    ):
                                                        message = asset.get("content") or asset.get("text")
                                                        break

                                            if not message and creative.get("inputs"):
                                                inputs = creative.get("inputs", [])
                                                if inputs and isinstance(inputs[0], dict):
                                                    message = inputs[0].get("context_description")

                                            # Extract promoted_offerings from assets if available
                                            promoted_offerings = None
                                            if creative.get("assets"):
                                                assets = creative.get("assets", {})
                                                for role, asset in assets.items():
                                                    if role == "promoted_offerings" and isinstance(asset, dict):
                                                        promoted_offerings = asset
                                                        break

                                            # Get existing context_id for refinement
                                            existing_context_id = None
                                            if existing_creative.data:
                                                existing_context_id = existing_creative.data.get(
                                                    "generative_context_id"
                                                )

                                            # Use provided context_id or existing one
                                            context_id = creative.get("context_id") or existing_context_id

                                            # Only call build_creative if we have a message (refinement)
                                            if message:
                                                logger.info(
                                                    f"[sync_creatives] Calling build_creative for update: "
                                                    f"{existing_creative.creative_id} format {creative_format} "
                                                    f"from agent {format_obj.agent_url}, "
                                                    f"message_length={len(message) if message else 0}, "
                                                    f"context_id={context_id}"
                                                )

                                                build_result = run_async_in_sync_context(
                                                    registry.build_creative(
                                                        agent_url=format_obj.agent_url,
                                                        format_id=creative_format,
                                                        message=message,
                                                        gemini_api_key=gemini_api_key,
                                                        promoted_offerings=promoted_offerings,
                                                        context_id=context_id,
                                                        finalize=creative.get("approved", False),
                                                    )
                                                )

                                                # Store build result in data
                                                if build_result:
                                                    data["generative_build_result"] = build_result
                                                    data["generative_status"] = build_result.get("status", "draft")
                                                    data["generative_context_id"] = build_result.get("context_id")
                                                    changes.append("generative_build_result")

                                                    # Extract creative output if available
                                                    if build_result.get("creative_output"):
                                                        creative_output = build_result["creative_output"]

                                                        if creative_output.get("assets"):
                                                            data["assets"] = creative_output["assets"]
                                                            changes.append("assets")

                                                        if creative_output.get("output_format"):
                                                            output_format = creative_output["output_format"]
                                                            data["output_format"] = output_format
                                                            changes.append("output_format")

                                                            if isinstance(output_format, dict) and output_format.get(
                                                                "url"
                                                            ):
                                                                data["url"] = output_format["url"]
                                                                changes.append("url")
                                                                logger.info(
                                                                    f"[sync_creatives] Got URL from generative output (update): "
                                                                    f"{data['url']}"
                                                                )

                                                    logger.info(
                                                        f"[sync_creatives] Generative creative updated: "
                                                        f"status={data.get('generative_status')}, "
                                                        f"context_id={data.get('generative_context_id')}"
                                                    )
                                            else:
                                                logger.info(
                                                    "[sync_creatives] No message for generative update, "
                                                    "keeping existing creative data"
                                                )

                                            # Skip preview_creative call since we already have the output
                                            preview_result = None
                                        else:
                                            # Static creative - use preview_creative
                                            # Build creative manifest from available data
                                            # Extract string ID from FormatId object if needed
                                            format_id_str = (
                                                creative_format.id
                                                if hasattr(creative_format, "id")
                                                else str(creative_format)
                                            )
                                            creative_manifest = {
                                                "creative_id": existing_creative.creative_id,
                                                "name": creative.get("name") or existing_creative.name,
                                                "format_id": format_id_str,
                                            }

                                            # Add any provided asset data for validation
                                            # Validate assets are in dict format (AdCP v2.4+)
                                            if creative.get("assets"):
                                                validated_assets = _validate_creative_assets(creative.get("assets"))
                                                if validated_assets:
                                                    creative_manifest["assets"] = validated_assets
                                            if data.get("url"):
                                                creative_manifest["url"] = data.get("url")

                                            # Call creative agent's preview_creative for validation + preview
                                            # Extract string ID from FormatId object if needed
                                            format_id_str = (
                                                creative_format.id
                                                if hasattr(creative_format, "id")
                                                else str(creative_format)
                                            )
                                            logger.info(
                                                f"[sync_creatives] Calling preview_creative for validation (update): "
                                                f"{existing_creative.creative_id} format {format_id_str} "
                                                f"from agent {format_obj.agent_url}, has_assets={bool(creative.get('assets'))}, "
                                                f"has_url={bool(data.get('url'))}"
                                            )

                                            preview_result = run_async_in_sync_context(
                                                registry.preview_creative(
                                                    agent_url=format_obj.agent_url,
                                                    format_id=format_id_str,
                                                    creative_manifest=creative_manifest,
                                                )
                                            )

                                        # Extract preview data and store in data field
                                        if preview_result and preview_result.get("previews"):
                                            # Store full preview response for UI (per AdCP PR #119)
                                            # This preserves all variants and renders for UI display
                                            data["preview_response"] = preview_result
                                            changes.append("preview_response")

                                            # Also extract primary preview URL for backward compatibility
                                            first_preview = preview_result["previews"][0]
                                            renders = first_preview.get("renders", [])
                                            if renders:
                                                first_render = renders[0]

                                                # Store preview URL from render
                                                if first_render.get("preview_url"):
                                                    data["url"] = first_render["preview_url"]
                                                    changes.append("url")
                                                    logger.info(
                                                        f"[sync_creatives] Got preview URL from creative agent: {data['url']}"
                                                    )

                                                # Extract dimensions from dimensions object
                                                dimensions = first_render.get("dimensions", {})
                                                if dimensions.get("width"):
                                                    data["width"] = dimensions["width"]
                                                    changes.append("width")
                                                if dimensions.get("height"):
                                                    data["height"] = dimensions["height"]
                                                    changes.append("height")
                                                if dimensions.get("duration"):
                                                    data["duration"] = dimensions["duration"]
                                                    changes.append("duration")

                                        logger.info(
                                            f"[sync_creatives] Preview data populated for update: "
                                            f"url={bool(data.get('url'))}, "
                                            f"width={data.get('width')}, "
                                            f"height={data.get('height')}, "
                                            f"variants={len(preview_result.get('previews', []))}"
                                        )
                                    else:
                                        # Preview generation failed for update - creative is invalid
                                        error_msg = f"Creative validation failed: preview_creative returned no previews for update of {existing_creative.creative_id}"
                                        logger.error(f"[sync_creatives] {error_msg}")
                                        failed_creatives.append(
                                            {
                                                "creative_id": existing_creative.creative_id,
                                                "error": error_msg,
                                                "format": creative_format,
                                            }
                                        )
                                        failed_count += 1
                                        results.append(
                                            SyncCreativeResult(
                                                creative_id=existing_creative.creative_id,
                                                action="failed",
                                                errors=[error_msg],
                                            )
                                        )
                                        continue  # Skip this creative update

                                except Exception as validation_error:
                                    # Creative agent validation failed for update (network error, agent down, etc.)
                                    # Do NOT update the creative - it needs validation before acceptance
                                    error_msg = (
                                        f"Creative agent unreachable or validation error: {str(validation_error)}. "
                                        f"Retry recommended - creative agent may be temporarily unavailable."
                                    )
                                    logger.error(
                                        f"[sync_creatives] {error_msg} for update of {existing_creative.creative_id}",
                                        exc_info=True,
                                    )
                                    failed_creatives.append(
                                        {
                                            "creative_id": existing_creative.creative_id,
                                            "error": error_msg,
                                            "format": creative_format,
                                        }
                                    )
                                    failed_count += 1
                                    results.append(
                                        SyncCreativeResult(
                                            creative_id=existing_creative.creative_id,
                                            action="failed",
                                            errors=[error_msg],
                                        )
                                    )
                                    continue  # Skip this creative update

                            # In full upsert, consider all fields as changed
                            changes.extend(["url", "click_url", "width", "height", "duration"])

                        existing_creative.data = data

                        # Mark JSONB field as modified for SQLAlchemy
                        from sqlalchemy.orm import attributes

                        attributes.flag_modified(existing_creative, "data")

                        # Track creatives needing approval for workflow creation
                        if needs_approval:
                            creative_info = {
                                "creative_id": existing_creative.creative_id,
                                "format": creative_format,
                                "name": creative.get("name"),
                                "status": existing_creative.status,
                            }
                            # Include AI review reason if available
                            if (
                                approval_mode == "ai-powered"
                                and existing_creative.data
                                and existing_creative.data.get("ai_review")
                            ):
                                creative_info["ai_review_reason"] = existing_creative.data["ai_review"].get("reason")
                            creatives_needing_approval.append(creative_info)

                        # Record result for updated creative
                        action = "updated" if changes else "unchanged"
                        if action == "updated":
                            updated_count += 1
                        else:
                            unchanged_count += 1

                        results.append(
                            SyncCreativeResult(
                                creative_id=existing_creative.creative_id,
                                action=action,
                                status=existing_creative.status,
                                changes=changes,
                            )
                        )

                    else:
                        # Create new creative
                        from src.core.database.models import Creative as DBCreative

                        # Extract creative_id for error reporting (must be defined before any validation)
                        creative_id = creative.get("creative_id", "unknown")

                        # Prepare data field with all creative properties
                        data = {
                            "url": creative.get("url"),
                            "click_url": creative.get("click_url"),
                            "width": creative.get("width"),
                            "height": creative.get("height"),
                            "duration": creative.get("duration"),
                        }

                        # Add AdCP v1.3+ fields to data
                        if creative.get("snippet"):
                            data["snippet"] = creative.get("snippet")
                            data["snippet_type"] = creative.get("snippet_type")

                        if creative.get("template_variables"):
                            data["template_variables"] = creative.get("template_variables")

                        # ALWAYS validate creatives with the creative agent (validation + preview generation)
                        creative_format = creative.get("format_id") or creative.get("format")
                        if creative_format:
                            try:
                                # Get format to find creative agent URL

                                from src.core.creative_agent_registry import get_creative_agent_registry

                                registry = get_creative_agent_registry()

                                # List all formats to find the matching one
                                all_formats = run_async_in_sync_context(
                                    registry.list_all_formats(tenant_id=tenant["tenant_id"])
                                )

                                # Find matching format
                                format_obj = None
                                for fmt in all_formats:
                                    if fmt.format_id == creative_format:
                                        format_obj = fmt
                                        break

                                if format_obj and format_obj.agent_url:
                                    # Check if format is generative (has output_format_ids)
                                    is_generative = bool(getattr(format_obj, "output_format_ids", None))

                                    if is_generative:
                                        # Generative creative - call build_creative
                                        logger.info(
                                            f"[sync_creatives] Detected generative format: {creative_format}, "
                                            f"checking for Gemini API key"
                                        )

                                        # Get Gemini API key from config
                                        from src.core.config import get_config

                                        config = get_config()
                                        gemini_api_key = config.gemini_api_key

                                        if not gemini_api_key:
                                            error_msg = (
                                                f"Cannot build generative creative {creative_format}: "
                                                f"GEMINI_API_KEY not configured"
                                            )
                                            logger.error(f"[sync_creatives] {error_msg}")
                                            raise ValueError(error_msg)

                                        # Extract message/brief from assets or inputs
                                        message = None
                                        if creative.get("assets"):
                                            assets = creative.get("assets", {})
                                            for role, asset in assets.items():
                                                if role in ["message", "brief", "prompt"] and isinstance(asset, dict):
                                                    message = asset.get("content") or asset.get("text")
                                                    break

                                        if not message and creative.get("inputs"):
                                            inputs = creative.get("inputs", [])
                                            if inputs and isinstance(inputs[0], dict):
                                                message = inputs[0].get("context_description")

                                        if not message:
                                            message = f"Create a creative for: {creative.get('name')}"
                                            logger.warning(
                                                "[sync_creatives] No message found in assets/inputs, "
                                                "using creative name as fallback"
                                            )

                                        # Extract promoted_offerings from assets if available
                                        promoted_offerings = None
                                        if creative.get("assets"):
                                            assets = creative.get("assets", {})
                                            for role, asset in assets.items():
                                                if role == "promoted_offerings" and isinstance(asset, dict):
                                                    promoted_offerings = asset
                                                    break

                                        # Call build_creative
                                        # Extract string ID from FormatId object if needed
                                        format_id_str = (
                                            creative_format.id
                                            if hasattr(creative_format, "id")
                                            else str(creative_format)
                                        )
                                        logger.info(
                                            f"[sync_creatives] Calling build_creative for generative format: "
                                            f"{format_id_str} from agent {format_obj.agent_url}, "
                                            f"message_length={len(message) if message else 0}"
                                        )

                                        build_result = run_async_in_sync_context(
                                            registry.build_creative(
                                                agent_url=format_obj.agent_url,
                                                format_id=format_id_str,
                                                message=message,
                                                gemini_api_key=gemini_api_key,
                                                promoted_offerings=promoted_offerings,
                                                context_id=creative.get("context_id"),
                                                finalize=creative.get("approved", False),
                                            )
                                        )

                                        # Store build result
                                        if build_result:
                                            data["generative_build_result"] = build_result
                                            data["generative_status"] = build_result.get("status", "draft")
                                            data["generative_context_id"] = build_result.get("context_id")

                                            # Extract creative output
                                            if build_result.get("creative_output"):
                                                creative_output = build_result["creative_output"]

                                                if creative_output.get("assets"):
                                                    data["assets"] = creative_output["assets"]

                                                if creative_output.get("output_format"):
                                                    output_format = creative_output["output_format"]
                                                    data["output_format"] = output_format

                                                    if isinstance(output_format, dict) and output_format.get("url"):
                                                        data["url"] = output_format["url"]
                                                        logger.info(
                                                            f"[sync_creatives] Got URL from generative output: "
                                                            f"{data['url']}"
                                                        )

                                            logger.info(
                                                f"[sync_creatives] Generative creative built: "
                                                f"status={data.get('generative_status')}, "
                                                f"context_id={data.get('generative_context_id')}"
                                            )

                                        # Skip preview_creative call since we already have the output
                                        preview_result = None
                                    else:
                                        # Static creative - use preview_creative
                                        # Build creative manifest from available data
                                        # Extract string ID from FormatId object if needed
                                        format_id_str = (
                                            creative_format.id
                                            if hasattr(creative_format, "id")
                                            else str(creative_format)
                                        )
                                        creative_manifest = {
                                            "creative_id": creative.get("creative_id") or str(uuid.uuid4()),
                                            "name": creative.get("name"),
                                            "format_id": format_id_str,
                                        }

                                        # Add any provided asset data for validation
                                        # Validate assets are in dict format (AdCP v2.4+)
                                        if creative.get("assets"):
                                            validated_assets = _validate_creative_assets(creative.get("assets"))
                                            if validated_assets:
                                                creative_manifest["assets"] = validated_assets
                                        if data.get("url"):
                                            creative_manifest["url"] = data.get("url")

                                        # Call creative agent's preview_creative for validation + preview
                                        # Extract string ID from FormatId object if needed
                                        format_id_str = (
                                            creative_format.id
                                            if hasattr(creative_format, "id")
                                            else str(creative_format)
                                        )
                                        logger.info(
                                            f"[sync_creatives] Calling preview_creative for validation: {format_id_str} "
                                            f"from agent {format_obj.agent_url}, has_assets={bool(creative.get('assets'))}, "
                                            f"has_url={bool(data.get('url'))}"
                                        )

                                        preview_result = run_async_in_sync_context(
                                            registry.preview_creative(
                                                agent_url=format_obj.agent_url,
                                                format_id=format_id_str,
                                                creative_manifest=creative_manifest,
                                            )
                                        )

                                    # Extract preview data and store in data field
                                    if preview_result and preview_result.get("previews"):
                                        # Store full preview response for UI (per AdCP PR #119)
                                        # This preserves all variants and renders for UI display
                                        data["preview_response"] = preview_result

                                        # Also extract primary preview URL for backward compatibility
                                        first_preview = preview_result["previews"][0]
                                        renders = first_preview.get("renders", [])
                                        if renders:
                                            first_render = renders[0]

                                            # Store preview URL from render
                                            if first_render.get("preview_url"):
                                                data["url"] = first_render["preview_url"]
                                                logger.info(
                                                    f"[sync_creatives] Got preview URL from creative agent: {data['url']}"
                                                )

                                            # Extract dimensions from dimensions object
                                            dimensions = first_render.get("dimensions", {})
                                            if dimensions.get("width"):
                                                data["width"] = dimensions["width"]
                                            if dimensions.get("height"):
                                                data["height"] = dimensions["height"]
                                            if dimensions.get("duration"):
                                                data["duration"] = dimensions["duration"]

                                        logger.info(
                                            f"[sync_creatives] Preview data populated: "
                                            f"url={bool(data.get('url'))}, "
                                            f"width={data.get('width')}, "
                                            f"height={data.get('height')}, "
                                            f"variants={len(preview_result.get('previews', []))}"
                                        )
                                    else:
                                        # Preview generation failed - creative is invalid
                                        error_msg = f"Creative validation failed: preview_creative returned no previews for {creative_id}"
                                        logger.error(f"[sync_creatives] {error_msg}")
                                        failed_creatives.append(
                                            {
                                                "creative_id": creative_id,
                                                "error": error_msg,
                                                "format": creative_format,
                                            }
                                        )
                                        failed_count += 1
                                        results.append(
                                            SyncCreativeResult(
                                                creative_id=creative_id,
                                                action="failed",
                                                errors=[error_msg],
                                            )
                                        )
                                        continue  # Skip this creative

                            except Exception as validation_error:
                                # Creative agent validation failed (network error, agent down, etc.)
                                # Do NOT store the creative - it needs validation before acceptance
                                error_msg = (
                                    f"Creative agent unreachable or validation error: {str(validation_error)}. "
                                    f"Retry recommended - creative agent may be temporarily unavailable."
                                )
                                logger.error(
                                    f"[sync_creatives] {error_msg} - rejecting creative {creative_id}",
                                    exc_info=True,
                                )
                                failed_creatives.append(
                                    {
                                        "creative_id": creative_id,
                                        "error": error_msg,
                                        "format": creative_format,
                                    }
                                )
                                failed_count += 1
                                results.append(
                                    SyncCreativeResult(
                                        creative_id=creative_id,
                                        action="failed",
                                        errors=[error_msg],
                                    )
                                )
                                continue  # Skip storing this creative

                        # Determine creative status based on approval mode

                        # Create initial creative with pending status for AI review
                        creative_status = "pending"
                        needs_approval = False

                        # Extract agent_url and format ID from format_id field
                        # Use validated format_value (already auto-upgraded from string)
                        agent_url, format_id = _extract_format_namespace(format_value)

                        db_creative = DBCreative(
                            tenant_id=tenant["tenant_id"],
                            creative_id=creative.get("creative_id") or str(uuid.uuid4()),
                            name=creative.get("name"),
                            agent_url=agent_url,
                            format=format_id,
                            principal_id=principal_id,
                            status=creative_status,
                            created_at=datetime.now(UTC),
                            data=data,
                        )

                        session.add(db_creative)
                        session.flush()  # Get the ID

                        # Update creative_id if it was generated
                        if not creative.get("creative_id"):
                            creative["creative_id"] = db_creative.creative_id

                        # Now apply approval mode logic
                        if approval_mode == "auto-approve":
                            db_creative.status = "approved"
                            needs_approval = False
                        elif approval_mode == "ai-powered":
                            # Submit to background AI review (async)

                            from src.admin.blueprints.creatives import (
                                _ai_review_executor,
                                _ai_review_lock,
                                _ai_review_tasks,
                            )

                            # Set status to pending immediately
                            db_creative.status = "pending"
                            needs_approval = True

                            # Submit background task
                            task_id = f"ai_review_{db_creative.creative_id}_{uuid.uuid4().hex[:8]}"

                            # Import the async function
                            from src.admin.blueprints.creatives import _ai_review_creative_async

                            future = _ai_review_executor.submit(
                                _ai_review_creative_async,
                                creative_id=db_creative.creative_id,
                                tenant_id=tenant["tenant_id"],
                                webhook_url=webhook_url,
                                slack_webhook_url=tenant.get("slack_webhook_url"),
                                principal_name=principal_id,
                            )

                            # Track the task
                            with _ai_review_lock:
                                _ai_review_tasks[task_id] = {
                                    "future": future,
                                    "creative_id": db_creative.creative_id,
                                    "created_at": time.time(),
                                }

                            logger.info(
                                f"[sync_creatives] Submitted AI review for new creative {db_creative.creative_id} (task: {task_id})"
                            )
                        else:  # require-human
                            db_creative.status = "pending"
                            needs_approval = True

                        # Track creatives needing approval for workflow creation
                        if needs_approval:
                            creative_info = {
                                "creative_id": db_creative.creative_id,
                                "format": creative_format,
                                "name": creative.get("name"),
                                "status": db_creative.status,  # Include status for Slack notification
                            }
                            # AI review reason will be added asynchronously when review completes
                            # No ai_result available yet in async mode
                            creatives_needing_approval.append(creative_info)

                        # Record result for created creative
                        created_count += 1
                        results.append(
                            SyncCreativeResult(
                                creative_id=db_creative.creative_id,
                                action="created",
                                status=db_creative.status,
                            )
                        )

                    # If we reach here, creative processing succeeded
                    synced_creatives.append(creative)

            except Exception as e:
                # Savepoint automatically rolls back this creative only
                creative_id = creative.get("creative_id", "unknown")
                error_msg = str(e)
                failed_creatives.append({"creative_id": creative_id, "name": creative.get("name"), "error": error_msg})
                failed_count += 1
                results.append(
                    SyncCreativeResult(
                        creative_id=creative_id,
                        action="failed",
                        errors=[error_msg],
                    )
                )

        # Commit all successful creative operations
        session.commit()

    # Process assignments (spec-compliant: creative_id → package_ids mapping)
    assignment_list = []
    # Track assignments per creative for response population
    assignments_by_creative: dict[str, list[str]] = {}  # creative_id -> [package_ids]
    assignment_errors_by_creative: dict[str, dict[str, str]] = {}  # creative_id -> {package_id: error}

    # Note: assignments should be a dict, but handle both dict and None
    if assignments and isinstance(assignments, dict):
        with get_db_session() as session:
            from src.core.database.models import CreativeAssignment as DBAssignment
            from src.core.database.models import MediaBuy
            from src.core.schemas import CreativeAssignment

            for creative_id, package_ids in assignments.items():
                # Initialize tracking for this creative
                if creative_id not in assignments_by_creative:
                    assignments_by_creative[creative_id] = []
                if creative_id not in assignment_errors_by_creative:
                    assignment_errors_by_creative[creative_id] = {}

                for package_id in package_ids:
                    # Find which media buy this package belongs to
                    # Packages are stored in media_buy.raw_request["packages"]
                    # Note: package_id can be either the server-generated package_id OR buyer_ref
                    stmt = select(MediaBuy).filter_by(tenant_id=tenant["tenant_id"])
                    media_buys = session.scalars(stmt).all()

                    media_buy_id = None
                    actual_package_id = None
                    for mb in media_buys:
                        packages = mb.raw_request.get("packages", [])
                        # Check both package_id (server-generated) and buyer_ref (client-provided)
                        for pkg in packages:
                            if pkg.get("package_id") == package_id or pkg.get("buyer_ref") == package_id:
                                media_buy_id = mb.media_buy_id
                                # Use the server-generated package_id for storage
                                actual_package_id = pkg.get("package_id", package_id)
                                break
                        if media_buy_id:
                            break

                    if not media_buy_id:
                        # Package not found - record error
                        error_msg = f"Package not found: {package_id}"
                        assignment_errors_by_creative[creative_id][package_id] = error_msg

                        # Skip if in lenient mode, error if strict
                        if validation_mode == "strict":
                            raise ToolError(error_msg)
                        else:
                            logger.warning(f"Package not found during assignment: {package_id}, skipping")
                            continue

                    # Create assignment in creative_assignments table
                    assignment = DBAssignment(
                        tenant_id=tenant["tenant_id"],
                        assignment_id=str(uuid.uuid4()),
                        media_buy_id=media_buy_id,
                        package_id=actual_package_id,  # Use resolved package_id
                        creative_id=creative_id,
                        weight=100,
                        created_at=datetime.now(UTC),
                    )

                    session.add(assignment)
                    assignment_list.append(
                        CreativeAssignment(
                            assignment_id=assignment.assignment_id,
                            media_buy_id=assignment.media_buy_id,
                            package_id=assignment.package_id,
                            creative_id=assignment.creative_id,
                            weight=assignment.weight,
                        )
                    )

                    # Track successful assignment
                    assignments_by_creative[creative_id].append(actual_package_id)

            session.commit()

    # Update creative results with assignment information (per AdCP spec)
    for result in results:
        if result.creative_id in assignments_by_creative:
            assigned_packages = assignments_by_creative[result.creative_id]
            if assigned_packages:
                result.assigned_to = assigned_packages

        if result.creative_id in assignment_errors_by_creative:
            errors = assignment_errors_by_creative[result.creative_id]
            if errors:
                result.assignment_errors = errors

    # Create workflow steps for creatives requiring approval
    if creatives_needing_approval:
        from src.core.context_manager import get_context_manager
        from src.core.database.models import ObjectWorkflowMapping

        ctx_manager = get_context_manager()

        # Get or create persistent context for this operation
        # is_async=True because we're creating workflow steps that need tracking
        persistent_ctx = ctx_manager.get_or_create_context(
            principal_id=principal_id, tenant_id=tenant["tenant_id"], is_async=True
        )

        with get_db_session() as session:
            for creative_info in creatives_needing_approval:
                # Build appropriate comment based on status
                status = creative_info.get("status", "pending")
                if status == "rejected":
                    comment = f"Creative '{creative_info['name']}' (format: {creative_info['format']}) was rejected by AI review"
                elif status == "pending":
                    if approval_mode == "ai-powered":
                        comment = f"Creative '{creative_info['name']}' (format: {creative_info['format']}) requires human review per AI recommendation"
                    else:
                        comment = f"Creative '{creative_info['name']}' (format: {creative_info['format']}) requires manual approval"
                else:
                    comment = f"Creative '{creative_info['name']}' (format: {creative_info['format']}) requires review"

                # Create workflow step for creative approval
                request_data_for_workflow = {
                    "creative_id": creative_info["creative_id"],
                    "format": creative_info["format"],
                    "name": creative_info["name"],
                    "status": status,
                    "approval_mode": approval_mode,
                }
                # Store push_notification_config if provided for async notification
                if push_notification_config:
                    request_data_for_workflow["push_notification_config"] = push_notification_config

                step = ctx_manager.create_workflow_step(
                    context_id=persistent_ctx.context_id,
                    step_type="creative_approval",
                    owner="publisher",
                    status="requires_approval",
                    tool_name="sync_creatives",
                    request_data=request_data_for_workflow,
                    initial_comment=comment,
                )

                # Create ObjectWorkflowMapping to link creative to workflow step
                # This is CRITICAL for webhook delivery when creative is approved
                mapping = ObjectWorkflowMapping(
                    step_id=step.step_id,
                    object_type="creative",
                    object_id=creative_info["creative_id"],
                    action="approval_required",
                )
                session.add(mapping)

            session.commit()
            console.print(
                f"[blue]📋 Created {len(creatives_needing_approval)} workflow steps for creative approval[/blue]"
            )

        # Send Slack notification for pending/rejected creative reviews
        # Note: For ai-powered mode, notifications are sent AFTER AI review completes (with AI reasoning)
        # Only send immediate notifications for require-human mode or existing creatives with AI review results
        logger.info(
            f"Checking Slack notification: creatives={len(creatives_needing_approval)}, webhook={tenant.get('slack_webhook_url')}, approval_mode={approval_mode}"
        )
        if creatives_needing_approval and tenant.get("slack_webhook_url") and approval_mode == "require-human":
            from src.services.slack_notifier import get_slack_notifier

            logger.info(
                f"Sending Slack notifications for {len(creatives_needing_approval)} creatives (require-human mode)"
            )
            tenant_config = {"features": {"slack_webhook_url": tenant["slack_webhook_url"]}}
            notifier = get_slack_notifier(tenant_config)

            for creative_info in creatives_needing_approval:
                status = creative_info.get("status", "pending")
                ai_review_reason = creative_info.get("ai_review_reason")

                if status == "rejected":
                    # For rejected creatives, send a different notification
                    # TODO: Add notify_creative_rejected method to SlackNotifier
                    notifier.notify_creative_pending(
                        creative_id=creative_info["creative_id"],
                        principal_name=principal_id,
                        format_type=creative_info["format"],
                        media_buy_id=None,
                        tenant_id=tenant["tenant_id"],
                        ai_review_reason=ai_review_reason,
                    )
                else:
                    # For pending creatives (human review required)
                    notifier.notify_creative_pending(
                        creative_id=creative_info["creative_id"],
                        principal_name=principal_id,
                        format_type=creative_info["format"],
                        media_buy_id=None,
                        tenant_id=tenant["tenant_id"],
                        ai_review_reason=ai_review_reason,
                    )

    # Audit logging
    audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])

    # Build error message from failed creatives
    error_message = None
    if failed_creatives:
        error_lines = []
        for fc in failed_creatives[:5]:  # Limit to first 5 errors to avoid huge messages
            creative_id = fc.get("creative_id", "unknown")
            error_text = fc.get("error", "Unknown error")
            error_lines.append(f"{creative_id}: {error_text}")
        error_message = "; ".join(error_lines)
        if len(failed_creatives) > 5:
            error_message += f" (and {len(failed_creatives) - 5} more)"

    audit_logger.log_operation(
        operation="sync_creatives",
        principal_name=principal_id,
        principal_id=principal_id,
        adapter_id="N/A",
        success=len(failed_creatives) == 0,
        error=error_message,
        details={
            "synced_count": len(synced_creatives),
            "failed_count": len(failed_creatives),
            "assignment_count": len(assignment_list),
            "patch_mode": patch,
            "dry_run": dry_run,
        },
    )

    # Log activity
    log_tool_activity(context, "sync_creatives", start_time)

    # Build message
    message = f"Synced {created_count + updated_count} creatives"
    if created_count:
        message += f" ({created_count} created"
        if updated_count:
            message += f", {updated_count} updated"
        message += ")"
    elif updated_count:
        message += f" ({updated_count} updated)"
    if unchanged_count:
        message += f", {unchanged_count} unchanged"
    if failed_count:
        message += f", {failed_count} failed"
    if assignment_list:
        message += f", {len(assignment_list)} assignments created"
    if creatives_needing_approval:
        message += f", {len(creatives_needing_approval)} require approval"

    # Log audit trail for sync_creatives operation
    try:
        with get_db_session() as audit_session:
            from src.core.database.models import Principal as DBPrincipal

            # Get principal info for audit log
            stmt = select(DBPrincipal).filter_by(tenant_id=tenant["tenant_id"], principal_id=principal_id)
            principal = audit_session.scalars(stmt).first()

            if principal:
                # Create audit logger and log the operation
                audit_logger = get_audit_logger("sync_creatives", tenant["tenant_id"])
                audit_logger.log_operation(
                    operation="sync_creatives",
                    principal_name=principal.name,
                    principal_id=principal_id,
                    adapter_id=principal_id,  # Use principal_id as adapter_id for consistency
                    success=(failed_count == 0),
                    details={
                        "created_count": created_count,
                        "updated_count": updated_count,
                        "unchanged_count": unchanged_count,
                        "failed_count": failed_count,
                        "assignment_count": len(assignment_list) if assignment_list else 0,
                        "approval_required_count": len(creatives_needing_approval),
                        "dry_run": dry_run,
                        "patch_mode": patch,
                    },
                    tenant_id=tenant["tenant_id"],
                )
    except Exception as e:
        # Don't fail the operation if audit logging fails
        logger.warning(f"Failed to write audit log for sync_creatives: {e}")

    # Build AdCP-compliant response (per official spec)
    return SyncCreativesResponse(
        creatives=results,
        dry_run=dry_run,
    )


@mcp.tool()
def sync_creatives(
    creatives: list[dict],
    patch: bool = False,
    assignments: dict = None,
    delete_missing: bool = False,
    dry_run: bool = False,
    validation_mode: str = "strict",
    push_notification_config: dict | None = None,
    context: Context = None,
) -> SyncCreativesResponse:
    """Sync creative assets to centralized library (AdCP v2.4 spec compliant endpoint).

    MCP tool wrapper that delegates to the shared implementation.

    Args:
        creatives: List of creative objects to sync
        patch: When true, only update provided fields (partial update). When false, full upsert.
        assignments: Bulk assignment map of creative_id to package_ids (spec-compliant)
        delete_missing: Delete creatives not in sync payload (use with caution)
        dry_run: Preview changes without applying them
        validation_mode: Validation strictness (strict or lenient)
        push_notification_config: Push notification config for async notifications (AdCP spec, optional)
        context: FastMCP context (automatically provided)

    Returns:
        SyncCreativesResponse with sync results
    """
    return _sync_creatives_impl(
        creatives=creatives,
        patch=patch,
        assignments=assignments,
        delete_missing=delete_missing,
        dry_run=dry_run,
        validation_mode=validation_mode,
        push_notification_config=push_notification_config,
        context=context,
    )


def _list_creatives_impl(
    media_buy_id: str = None,
    buyer_ref: str = None,
    status: str = None,
    format: str = None,
    tags: list[str] = None,
    created_after: str = None,
    created_before: str = None,
    search: str = None,
    filters: dict = None,
    sort: dict = None,
    pagination: dict = None,
    fields: list[str] = None,
    include_performance: bool = False,
    include_assignments: bool = False,
    include_sub_assets: bool = False,
    page: int = 1,
    limit: int = 50,
    sort_by: str = "created_date",
    sort_order: str = "desc",
    context: Context = None,
) -> ListCreativesResponse:
    """List and search creative library (AdCP spec endpoint).

    Advanced filtering and search endpoint for the centralized creative library.
    Supports pagination, sorting, and multiple filter criteria.

    Args:
        media_buy_id: Filter by media buy ID (optional)
        buyer_ref: Filter by buyer reference (optional)
        status: Filter by creative status (pending, approved, rejected) (optional)
        format: Filter by creative format (optional)
        tags: Filter by tags (optional)
        created_after: Filter by creation date (ISO string) (optional)
        created_before: Filter by creation date (ISO string) (optional)
        search: Search in creative names and descriptions (optional)
        filters: Advanced filtering options (nested object, optional)
        sort: Sort configuration (nested object, optional)
        pagination: Pagination parameters (nested object, optional)
        fields: Specific fields to return (optional)
        include_performance: Include performance metrics (optional)
        include_assignments: Include package assignments (optional)
        include_sub_assets: Include sub-assets (optional)
        page: Page number for pagination (default: 1)
        limit: Number of results per page (default: 50, max: 1000)
        sort_by: Sort field (created_date, name, status) (default: created_date)
        sort_order: Sort order (asc, desc) (default: desc)
        context: FastMCP context (automatically provided)

    Returns:
        ListCreativesResponse with filtered creative assets and pagination info
    """
    from src.core.schemas import Creative, ListCreativesRequest

    # Parse datetime strings if provided
    created_after_dt = None
    created_before_dt = None
    if created_after:
        try:
            created_after_dt = datetime.fromisoformat(created_after.replace("Z", "+00:00"))
        except ValueError:
            raise ToolError(f"Invalid created_after date format: {created_after}")
    if created_before:
        try:
            created_before_dt = datetime.fromisoformat(created_before.replace("Z", "+00:00"))
        except ValueError:
            raise ToolError(f"Invalid created_before date format: {created_before}")

    # Create request object from individual parameters (MCP-compliant)
    try:
        req = ListCreativesRequest(
            media_buy_id=media_buy_id,
            buyer_ref=buyer_ref,
            status=status,
            format=format,
            tags=tags or [],
            created_after=created_after_dt,
            created_before=created_before_dt,
            search=search,
            filters=filters,
            sort=sort,
            pagination=pagination,
            fields=fields,
            include_performance=include_performance,
            include_assignments=include_assignments,
            include_sub_assets=include_sub_assets,
            page=page,
            limit=min(limit, 1000),  # Enforce max limit
            sort_by=sort_by,
            sort_order=sort_order,
        )
    except ValidationError as e:
        raise ToolError(format_validation_error(e, context="list_creatives request")) from e

    start_time = time.time()

    # Authentication - REQUIRED (creatives contain sensitive data)
    # Unlike discovery endpoints (list_creative_formats), this returns actual creative assets
    # which are principal-specific and must be access-controlled
    principal_id = _get_principal_id_from_context(context)

    # Get tenant information
    tenant = get_current_tenant()
    if not tenant:
        raise ToolError("No tenant context available")

    creatives = []
    total_count = 0

    with get_db_session() as session:
        from src.core.database.models import Creative as DBCreative
        from src.core.database.models import CreativeAssignment as DBAssignment
        from src.core.database.models import MediaBuy

        # Build query - filter by tenant AND principal for security
        stmt = select(DBCreative).filter_by(tenant_id=tenant["tenant_id"], principal_id=principal_id)

        # Apply filters
        if req.media_buy_id:
            # Filter by media buy assignments
            stmt = stmt.join(DBAssignment, DBCreative.creative_id == DBAssignment.creative_id).where(
                DBAssignment.media_buy_id == req.media_buy_id
            )

        if req.buyer_ref:
            # Filter by buyer_ref through media buy
            stmt = (
                stmt.join(DBAssignment, DBCreative.creative_id == DBAssignment.creative_id)
                .join(MediaBuy, DBAssignment.media_buy_id == MediaBuy.media_buy_id)
                .where(MediaBuy.buyer_ref == req.buyer_ref)
            )

        if req.status:
            stmt = stmt.where(DBCreative.status == req.status)

        if req.format:
            stmt = stmt.where(DBCreative.format == req.format)

        if req.tags:
            # Simple tag filtering - in production, might use JSON operators
            for tag in req.tags:
                stmt = stmt.where(DBCreative.name.contains(tag))  # Simplified

        if req.created_after:
            stmt = stmt.where(DBCreative.created_at >= req.created_after)

        if req.created_before:
            stmt = stmt.where(DBCreative.created_at <= req.created_before)

        if req.search:
            # Search in name and description
            search_term = f"%{req.search}%"
            stmt = stmt.where(DBCreative.name.ilike(search_term))

        # Get total count before pagination
        from sqlalchemy import func

        total_count = session.scalar(select(func.count()).select_from(stmt.subquery()))

        # Apply sorting
        if req.sort_by == "name":
            sort_column = DBCreative.name
        elif req.sort_by == "status":
            sort_column = DBCreative.status
        else:  # Default to created_date
            sort_column = DBCreative.created_at

        if req.sort_order == "asc":
            stmt = stmt.order_by(sort_column.asc())
        else:
            stmt = stmt.order_by(sort_column.desc())

        # Apply pagination
        offset = (req.page - 1) * req.limit
        db_creatives = session.scalars(stmt.offset(offset).limit(req.limit)).all()

        # Convert to schema objects
        for db_creative in db_creatives:
            # Create schema object with correct field names and data field access
            schema_data = {
                "creative_id": db_creative.creative_id,
                "name": db_creative.name,
                "format_id": {  # Structured format_id per AdCP v2.4 spec
                    "agent_url": db_creative.agent_url,
                    "id": db_creative.format,
                },
                "click_through_url": db_creative.data.get("click_url") if db_creative.data else None,  # From data field
                "width": db_creative.data.get("width") if db_creative.data else None,
                "height": db_creative.data.get("height") if db_creative.data else None,
                "duration": db_creative.data.get("duration") if db_creative.data else None,
                "status": db_creative.status,
                "template_variables": db_creative.data.get("template_variables", {}) if db_creative.data else {},
                "principal_id": db_creative.principal_id,
                "created_at": db_creative.created_at or datetime.now(UTC),
                "updated_at": db_creative.updated_at or datetime.now(UTC),
            }

            # Handle content_uri - required field even for snippet creatives
            # For snippet creatives, provide an HTML-looking URL to pass validation
            snippet = db_creative.data.get("snippet") if db_creative.data else None
            if snippet:
                schema_data.update(
                    {
                        "snippet": snippet,
                        "snippet_type": db_creative.data.get("snippet_type") if db_creative.data else None,
                        # Use HTML snippet-looking URL to pass _is_html_snippet() validation
                        "content_uri": (
                            db_creative.data.get("url") or "<script>/* Snippet-based creative */</script>"
                            if db_creative.data
                            else "<script>/* Snippet-based creative */</script>"
                        ),
                    }
                )
            else:
                schema_data["content_uri"] = (
                    db_creative.data.get("url") or "https://placeholder.example.com/missing.jpg"
                    if db_creative.data
                    else "https://placeholder.example.com/missing.jpg"
                )

            creative = Creative(**schema_data)
            creatives.append(creative)

    # Calculate pagination info
    has_more = (req.page * req.limit) < total_count

    # Audit logging
    audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
    audit_logger.log_operation(
        operation="list_creatives",
        principal_name=principal_id,
        principal_id=principal_id,
        adapter_id="N/A",
        success=True,
        details={
            "result_count": len(creatives),
            "total_count": total_count,
            "page": req.page,
            "filters_applied": {
                "media_buy_id": req.media_buy_id,
                "status": req.status,
                "format": req.format,
                "search": req.search,
            },
        },
    )

    # Log activity
    log_tool_activity(context, "list_creatives", start_time)

    message = f"Found {len(creatives)} creatives"
    if total_count > len(creatives):
        message += f" (page {req.page} of {total_count} total)"

    # Build filters_applied list
    filters_applied = []
    if req.media_buy_id:
        filters_applied.append(f"media_buy_id={req.media_buy_id}")
    if req.buyer_ref:
        filters_applied.append(f"buyer_ref={req.buyer_ref}")
    if req.status:
        filters_applied.append(f"status={req.status}")
    if req.format:
        filters_applied.append(f"format={req.format}")
    if req.tags:
        filters_applied.append(f"tags={','.join(req.tags)}")
    if req.created_after:
        filters_applied.append(f"created_after={req.created_after.isoformat()}")
    if req.created_before:
        filters_applied.append(f"created_before={req.created_before.isoformat()}")
    if req.search:
        filters_applied.append(f"search={req.search}")

    # Build sort_applied dict
    sort_applied = {"field": req.sort_by, "direction": req.sort_order} if req.sort_by else None

    # Calculate offset and total_pages
    offset = (req.page - 1) * req.limit
    total_pages = (total_count + req.limit - 1) // req.limit if req.limit > 0 else 0

    # Import required schema classes
    from src.core.schemas import Pagination, QuerySummary

    return ListCreativesResponse(
        query_summary=QuerySummary(
            total_matching=total_count,
            returned=len(creatives),
            filters_applied=filters_applied,
            sort_applied=sort_applied,
        ),
        pagination=Pagination(
            limit=req.limit, offset=offset, has_more=has_more, total_pages=total_pages, current_page=req.page
        ),
        creatives=creatives,
    )


@mcp.tool()
def list_creatives(
    media_buy_id: str = None,
    buyer_ref: str = None,
    status: str = None,
    format: str = None,
    tags: list[str] = None,
    created_after: str = None,
    created_before: str = None,
    search: str = None,
    filters: dict = None,
    sort: dict = None,
    pagination: dict = None,
    fields: list[str] = None,
    include_performance: bool = False,
    include_assignments: bool = False,
    include_sub_assets: bool = False,
    page: int = 1,
    limit: int = 50,
    sort_by: str = "created_date",
    sort_order: str = "desc",
    webhook_url: str | None = None,
    context: Context = None,
) -> ListCreativesResponse:
    """List and filter creative assets from the centralized library.

    MCP tool wrapper that delegates to the shared implementation.
    Supports both flat parameters (status, format, etc.) and nested objects (filters, sort, pagination)
    for maximum flexibility.
    """
    return _list_creatives_impl(
        media_buy_id,
        buyer_ref,
        status,
        format,
        tags,
        created_after,
        created_before,
        search,
        filters,
        sort,
        pagination,
        fields,
        include_performance,
        include_assignments,
        include_sub_assets,
        page,
        limit,
        sort_by,
        sort_order,
        context,
    )


@mcp.tool()
async def get_signals(req: GetSignalsRequest, context: Context = None) -> GetSignalsResponse:
    """Optional endpoint for discovering available signals (audiences, contextual, etc.)

    Args:
        req: Request containing query parameters for signal discovery
        context: FastMCP context (automatically provided)

    Returns:
        GetSignalsResponse containing matching signals
    """

    _get_principal_id_from_context(context)

    # Get tenant information
    tenant = get_current_tenant()
    if not tenant:
        raise ToolError("No tenant context available")

    # Mock implementation - in production, this would query from a signal provider
    # or the ad server's available audience segments
    signals = []

    # Sample signals for demonstration using AdCP-compliant structure
    sample_signals = [
        Signal(
            signal_agent_segment_id="auto_intenders_q1_2025",
            name="Auto Intenders Q1 2025",
            description="Users actively researching new vehicles in Q1 2025",
            signal_type="marketplace",
            data_provider="Acme Data Solutions",
            coverage_percentage=85.0,
            deployments=[
                SignalDeployment(
                    platform="google_ad_manager",
                    account="123456",
                    is_live=True,
                    scope="account-specific",
                    decisioning_platform_segment_id="gam_auto_intenders",
                    estimated_activation_duration_minutes=0,
                )
            ],
            pricing=SignalPricing(cpm=3.0, currency="USD"),
        ),
        Signal(
            signal_agent_segment_id="luxury_travel_enthusiasts",
            name="Luxury Travel Enthusiasts",
            description="High-income individuals interested in premium travel experiences",
            signal_type="marketplace",
            data_provider="Premium Audience Co",
            coverage_percentage=75.0,
            deployments=[
                SignalDeployment(
                    platform="google_ad_manager",
                    is_live=True,
                    scope="platform-wide",
                    estimated_activation_duration_minutes=15,
                )
            ],
            pricing=SignalPricing(cpm=5.0, currency="USD"),
        ),
        Signal(
            signal_agent_segment_id="sports_content",
            name="Sports Content Pages",
            description="Target ads on sports-related content",
            signal_type="owned",
            data_provider="Publisher Sports Network",
            coverage_percentage=95.0,
            deployments=[
                SignalDeployment(
                    platform="google_ad_manager",
                    is_live=True,
                    scope="account-specific",
                    decisioning_platform_segment_id="sports_contextual",
                )
            ],
            pricing=SignalPricing(cpm=1.5, currency="USD"),
        ),
        Signal(
            signal_agent_segment_id="finance_content",
            name="Finance & Business Content",
            description="Target ads on finance and business content",
            signal_type="owned",
            data_provider="Financial News Corp",
            coverage_percentage=88.0,
            deployments=[SignalDeployment(platform="google_ad_manager", is_live=True, scope="platform-wide")],
            pricing=SignalPricing(cpm=2.0, currency="USD"),
        ),
        Signal(
            signal_agent_segment_id="urban_millennials",
            name="Urban Millennials",
            description="Millennials living in major metropolitan areas",
            signal_type="marketplace",
            data_provider="Demographics Plus",
            coverage_percentage=78.0,
            deployments=[
                SignalDeployment(
                    platform="google_ad_manager",
                    is_live=True,
                    scope="account-specific",
                    estimated_activation_duration_minutes=30,
                )
            ],
            pricing=SignalPricing(cpm=1.8, currency="USD"),
        ),
        Signal(
            signal_agent_segment_id="pet_owners",
            name="Pet Owners",
            description="Households with dogs or cats",
            signal_type="marketplace",
            data_provider="Lifestyle Data Inc",
            coverage_percentage=92.0,
            deployments=[SignalDeployment(platform="google_ad_manager", is_live=True, scope="platform-wide")],
            pricing=SignalPricing(cpm=1.2, currency="USD"),
        ),
    ]

    # Filter based on request parameters using new AdCP-compliant fields
    for signal in sample_signals:
        # Apply signal_spec filter (natural language description matching)
        if req.signal_spec:
            spec_lower = req.signal_spec.lower()
            if (
                spec_lower not in signal.name.lower()
                and spec_lower not in signal.description.lower()
                and spec_lower not in signal.signal_type.lower()
            ):
                continue

        # Apply filters if provided
        if req.filters:
            # Filter by catalog_types (equivalent to old 'type' field)
            if req.filters.catalog_types and signal.signal_type not in req.filters.catalog_types:
                continue

            # Filter by data_providers
            if req.filters.data_providers and signal.data_provider not in req.filters.data_providers:
                continue

            # Filter by max_cpm (using signal's pricing.cpm)
            if req.filters.max_cpm is not None and signal.pricing and signal.pricing.cpm > req.filters.max_cpm:
                continue

            # Filter by min_coverage_percentage
            if (
                req.filters.min_coverage_percentage is not None
                and signal.coverage_percentage < req.filters.min_coverage_percentage
            ):
                continue

        signals.append(signal)

    # Apply max_results limit (AdCP-compliant field name)
    if req.max_results:
        signals = signals[: req.max_results]

    # Generate message (required field in adapter schema)
    count = len(signals)
    if count == 0:
        message = "No signals matched your query."
    elif count == 1:
        message = "Found 1 signal matching your query."
    else:
        message = f"Found {count} signals matching your query."

    # Generate context_id (required field)
    context_id = f"signals_{uuid.uuid4().hex[:12]}"

    return GetSignalsResponse(message=message, context_id=context_id, signals=signals)


@mcp.tool()
async def activate_signal(
    signal_id: str,
    campaign_id: str = None,
    media_buy_id: str = None,
    context: Context = None,
) -> ActivateSignalResponse:
    """Activate a signal for use in campaigns.

    Args:
        signal_id: Signal ID to activate
        campaign_id: Optional campaign ID to activate signal for
        media_buy_id: Optional media buy ID to activate signal for
        context: FastMCP context (automatically provided)

    Returns:
        ActivateSignalResponse with activation status
    """
    start_time = time.time()

    # Authentication required for signal activation
    principal_id = _get_principal_id_from_context(context)

    # Get tenant information
    tenant = get_current_tenant()
    if not tenant:
        raise ToolError("No tenant context available")

    # Get the Principal object with ad server mappings
    principal = get_principal_object(principal_id)

    # Apply testing hooks
    testing_ctx = get_testing_context(context)
    campaign_info = {"endpoint": "activate_signal", "signal_id": signal_id}
    apply_testing_hooks(testing_ctx, campaign_info)

    try:
        # In a real implementation, this would:
        # 1. Validate the signal exists and is available
        # 2. Check if the principal has permission to activate the signal
        # 3. Communicate with the signal provider's API to activate the signal
        # 4. Update the campaign or media buy configuration to include the signal

        # Mock implementation for demonstration
        activation_success = True
        requires_approval = signal_id.startswith("premium_")  # Mock rule: premium signals need approval

        task_id = f"task_{uuid.uuid4().hex[:12]}"

        if requires_approval:
            # Create a human task for approval
            status = "pending"
            errors = [
                {
                    "code": "APPROVAL_REQUIRED",
                    "message": f"Signal {signal_id} requires manual approval before activation",
                }
            ]
        elif activation_success:
            status = "processing"  # Activation in progress
            estimated_activation_duration_minutes = 15.0
            decisioning_platform_segment_id = f"seg_{signal_id}_{uuid.uuid4().hex[:8]}"
        else:
            status = "failed"
            errors = [{"code": "ACTIVATION_FAILED", "message": "Signal provider unavailable"}]

        # Log activity
        log_tool_activity(context, "activate_signal", start_time)

        # Build response with adapter schema fields
        if requires_approval or not activation_success:
            return ActivateSignalResponse(
                task_id=task_id,
                status=status,
                errors=errors,
            )
        else:
            return ActivateSignalResponse(
                task_id=task_id,
                status=status,
                decisioning_platform_segment_id=decisioning_platform_segment_id if activation_success else None,
                estimated_activation_duration_minutes=(
                    estimated_activation_duration_minutes if activation_success else None
                ),
            )

    except Exception as e:
        logger.error(f"Error activating signal {signal_id}: {e}")
        return ActivateSignalResponse(
            task_id=f"task_{uuid.uuid4().hex[:12]}",
            status="failed",
            errors=[{"code": "ACTIVATION_ERROR", "message": str(e)}],
        )


def _list_authorized_properties_impl(
    req: ListAuthorizedPropertiesRequest | None = None, context: Context | None = None
) -> ListAuthorizedPropertiesResponse:
    """List all properties this agent is authorized to represent (AdCP spec endpoint).

    Discovers advertising properties (websites, apps, podcasts, etc.) that this
    sales agent is authorized to sell advertising on behalf of publishers.

    Args:
        req: Request parameters including optional tag filters
        context: FastMCP context for authentication

    Returns:
        ListAuthorizedPropertiesResponse with properties and tag metadata
    """
    start_time = time.time()

    # Handle missing request object (allows empty calls)
    if req is None:
        req = ListAuthorizedPropertiesRequest()

    # Get tenant and principal from context
    # Authentication is OPTIONAL for discovery endpoints (returns public inventory)
    # require_valid_token=False means invalid tokens are treated like missing tokens (discovery endpoint behavior)
    principal_id, tenant = get_principal_from_context(
        context, require_valid_token=False
    )  # May return (None, tenant) for public discovery

    # Set tenant context if returned
    if tenant:
        set_current_tenant(tenant)
    else:
        tenant = get_current_tenant()

    if not tenant:
        raise ToolError(
            "TENANT_ERROR",
            "Could not resolve tenant from request context (no subdomain, virtual host, or x-adcp-tenant header found)",
        )

    tenant_id = tenant["tenant_id"]

    # Apply testing hooks
    from src.core.testing_hooks import TestingContext
    from src.core.tool_context import ToolContext

    if isinstance(context, ToolContext):
        # ToolContext has testing_context field directly
        testing_context = TestingContext(**context.testing_context) if context.testing_context else TestingContext()
        headers = {}
    else:
        # FastMCP Context has meta.headers
        headers = context.meta.get("headers", {}) if context and hasattr(context, "meta") and context.meta else {}
        testing_context = get_testing_context(headers)

    # Note: apply_testing_hooks signature is (data, testing_ctx, operation, campaign_info)
    # For list_authorized_properties, we don't modify data, so we can skip this call
    # The testing_context is used later if needed

    log_tool_activity(context, "list_authorized_properties", start_time)

    try:
        with get_db_session() as session:
            # Query authorized properties for this tenant
            stmt = select(AuthorizedProperty).where(AuthorizedProperty.tenant_id == tenant_id)

            # Apply tag filtering if requested
            if req.tags:
                # Filter properties that have any of the requested tags
                tag_filters = []
                for tag in req.tags:
                    tag_filters.append(AuthorizedProperty.tags.contains([tag]))
                stmt = stmt.where(sa.or_(*tag_filters))

            # Get all properties for this tenant (no verification status filter)
            # Publishers control what properties they add - verification is informational only
            authorized_properties = session.scalars(stmt).all()

            # Convert database models to Pydantic models
            properties = []
            all_tags = set()

            for prop in authorized_properties:
                # Extract identifiers from JSON
                identifiers = [
                    PropertyIdentifier(type=ident["type"], value=ident["value"]) for ident in (prop.identifiers or [])
                ]

                # Extract tags
                prop_tags = prop.tags or []
                all_tags.update(prop_tags)

                property_obj = Property(
                    property_type=prop.property_type,
                    name=prop.name,
                    identifiers=identifiers,
                    tags=prop_tags,
                    publisher_domain=prop.publisher_domain,
                )
                properties.append(property_obj)

            # Get tag metadata for all referenced tags
            tag_metadata = {}
            if all_tags:
                stmt = select(PropertyTag).where(PropertyTag.tenant_id == tenant_id, PropertyTag.tag_id.in_(all_tags))
                property_tags = session.scalars(stmt).all()

                for property_tag in property_tags:
                    tag_metadata[property_tag.tag_id] = PropertyTagMetadata(
                        name=property_tag.name, description=property_tag.description
                    )

            # Generate advertising policies text from tenant configuration
            advertising_policies_text = None
            advertising_policy = safe_parse_json_field(
                tenant.get("advertising_policy"), field_name="advertising_policy", default={}
            )

            if advertising_policy and advertising_policy.get("enabled"):
                # Build human-readable policy text
                policy_parts = []

                # Add baseline categories
                default_categories = advertising_policy.get("default_prohibited_categories", [])
                if default_categories:
                    policy_parts.append(f"**Baseline Protected Categories:** {', '.join(default_categories)}")

                # Add baseline tactics
                default_tactics = advertising_policy.get("default_prohibited_tactics", [])
                if default_tactics:
                    policy_parts.append(f"**Baseline Prohibited Tactics:** {', '.join(default_tactics)}")

                # Add additional categories
                additional_categories = advertising_policy.get("prohibited_categories", [])
                if additional_categories:
                    policy_parts.append(f"**Additional Prohibited Categories:** {', '.join(additional_categories)}")

                # Add additional tactics
                additional_tactics = advertising_policy.get("prohibited_tactics", [])
                if additional_tactics:
                    policy_parts.append(f"**Additional Prohibited Tactics:** {', '.join(additional_tactics)}")

                # Add blocked advertisers
                blocked_advertisers = advertising_policy.get("prohibited_advertisers", [])
                if blocked_advertisers:
                    policy_parts.append(f"**Blocked Advertisers/Domains:** {', '.join(blocked_advertisers)}")

                if policy_parts:
                    advertising_policies_text = "\n\n".join(policy_parts)
                    # Add footer
                    advertising_policies_text += (
                        "\n\n**Policy Enforcement:** Campaigns are analyzed using AI against these policies. "
                        "Violations will result in campaign rejection or require manual review."
                    )

            # Extract unique publisher domains from properties
            publisher_domains = sorted({prop.publisher_domain for prop in properties if prop.publisher_domain})

            # If no properties configured, return error - NO FALLBACK BEHAVIOR
            if not publisher_domains:
                raise ToolError(
                    "NO_PROPERTIES_CONFIGURED",
                    f"No authorized properties configured for tenant '{tenant_id}'. "
                    f"Please add properties via the Admin UI at /admin/tenant/{tenant_id}/authorized-properties",
                )

            # Create response with AdCP spec-compliant fields
            response = ListAuthorizedPropertiesResponse(
                publisher_domains=publisher_domains,  # Required per AdCP v2.4 spec
                advertising_policies=advertising_policies_text,
                errors=[],
            )

            # Log audit
            audit_logger = get_audit_logger("AdCP", tenant_id)
            audit_logger.log_operation(
                operation="list_authorized_properties",
                principal_name=principal_id or "anonymous",
                principal_id=principal_id or "anonymous",
                adapter_id="mcp_server",
                success=True,
                details={
                    "properties_count": len(properties),
                    "requested_tags": req.tags,
                    "response_tags_count": len(tag_metadata),
                },
            )

            return response

    except Exception as e:
        logger.error(f"Error listing authorized properties: {str(e)}")

        # Log audit for failure
        audit_logger = get_audit_logger("AdCP", tenant_id)
        audit_logger.log_operation(
            operation="list_authorized_properties",
            principal_name=principal_id,
            principal_id=principal_id,
            adapter_id="mcp_server",
            success=False,
            error=str(e),
        )

        raise ToolError("PROPERTIES_ERROR", f"Failed to list authorized properties: {str(e)}")


@mcp.tool()
def list_authorized_properties(
    req: ListAuthorizedPropertiesRequest | None = None, webhook_url: str | None = None, context: Context | None = None
) -> ListAuthorizedPropertiesResponse:
    """List all properties this agent is authorized to represent (AdCP spec endpoint).

    MCP tool wrapper that delegates to the shared implementation.

    Args:
        req: Request parameters including optional tag filters
        webhook_url: URL for async task completion notifications (AdCP spec, optional)
        context: FastMCP context for authentication

    Returns:
        ListAuthorizedPropertiesResponse with properties and tag metadata
    """
    # FIX: Create MinimalContext with headers from FastMCP request (like A2A does)
    # This ensures tenant detection works the same way for both MCP and A2A
    import logging
    import sys

    logger = logging.getLogger(__name__)
    tool_context = None

    if context:
        try:
            print(f"[MCP DEBUG] context type={type(context)}", file=sys.stderr, flush=True)
            print(f"[MCP DEBUG] has request_context={hasattr(context, 'request_context')}", file=sys.stderr, flush=True)
            logger.info(f"MCP list_authorized_properties: context type={type(context)}")
            logger.info(f"MCP list_authorized_properties: has request_context={hasattr(context, 'request_context')}")

            # Access raw Starlette request headers via context.request_context.request
            request = context.request_context.request
            print(
                f"[MCP DEBUG] request={request}, type={type(request) if request else None}", file=sys.stderr, flush=True
            )
            logger.info(f"MCP list_authorized_properties: request={request}, type={type(request) if request else None}")

            if request and hasattr(request, "headers"):
                headers = dict(request.headers)
                print(f"[MCP DEBUG] Extracted {len(headers)} headers from request", file=sys.stderr, flush=True)
                print(f"[MCP DEBUG] Host header={headers.get('host')}", file=sys.stderr, flush=True)
                print(
                    f"[MCP DEBUG] Apx-Incoming-Host header={headers.get('apx-incoming-host')}",
                    file=sys.stderr,
                    flush=True,
                )
                logger.info(f"MCP list_authorized_properties: Extracted {len(headers)} headers from request")
                logger.info(f"MCP list_authorized_properties: Host header={headers.get('host')}")

                # Create MinimalContext matching A2A pattern
                class MinimalContext:
                    def __init__(self, headers):
                        self.meta = {"headers": headers}
                        self.headers = headers

                tool_context = MinimalContext(headers)
                print("[MCP DEBUG] Created MinimalContext successfully", file=sys.stderr, flush=True)
                logger.info("MCP list_authorized_properties: Created MinimalContext successfully")
            else:
                print("[MCP DEBUG] request has no headers attribute", file=sys.stderr, flush=True)
                logger.warning("MCP list_authorized_properties: request has no headers attribute")
                tool_context = context
        except Exception as e:
            # Fallback to passing context as-is
            print(f"[MCP DEBUG] Exception extracting headers: {e}", file=sys.stderr, flush=True)
            logger.error(
                f"MCP list_authorized_properties: Could not extract headers from FastMCP context: {e}", exc_info=True
            )
            tool_context = context
    else:
        print("[MCP DEBUG] No context provided", file=sys.stderr, flush=True)
        logger.info("MCP list_authorized_properties: No context provided")
        tool_context = context

    return _list_authorized_properties_impl(req, tool_context)


def _validate_pricing_model_selection(
    package: Package,
    product: Any,  # ProductModel from database
    campaign_currency: str | None,
) -> dict[str, Any]:
    """Validate pricing model selection for a package against product's pricing options.

    Args:
        package: Package with optional pricing_model and bid_price
        product: Product database model with pricing_options relationship
        campaign_currency: Optional campaign-level currency

    Returns:
        Dict with validated pricing information:
        {
            "pricing_model": str,
            "rate": float | None,
            "currency": str,
            "is_fixed": bool,
            "bid_price": float | None,
        }

    Raises:
        ToolError: If pricing_model validation fails
    """
    from decimal import Decimal

    # All products must have pricing_options
    if not product.pricing_options or len(product.pricing_options) == 0:
        raise ToolError(
            "PRICING_ERROR",
            f"Product {product.product_id} has no pricing_options configured. This is a data integrity error.",
        )

    # If package doesn't specify pricing_model, use first pricing option from product
    if not package.pricing_model:
        first_option = product.pricing_options[0]
        return {
            "pricing_model": first_option.pricing_model,
            "rate": float(first_option.rate) if first_option.rate else None,
            "currency": first_option.currency or campaign_currency or "USD",
            "is_fixed": first_option.is_fixed,
            "bid_price": None,
        }

    # Find matching pricing option
    selected_option = None
    for option in product.pricing_options:
        if option.pricing_model == package.pricing_model.value:
            # If campaign currency specified, must match
            if campaign_currency and option.currency != campaign_currency:
                continue
            selected_option = option
            break

    if not selected_option:
        available_options = [f"{opt.pricing_model} ({opt.currency})" for opt in product.pricing_options]
        error_msg = f"Product {product.product_id} does not offer pricing model '{package.pricing_model}'"
        if campaign_currency:
            error_msg += f" in currency {campaign_currency}"
        error_msg += f". Available options: {', '.join(available_options)}"
        raise ToolError("PRICING_ERROR", error_msg)

    # Validate auction pricing
    if not selected_option.is_fixed:
        if not package.bid_price:
            raise ToolError(
                "PRICING_ERROR",
                f"Package requires bid_price for auction-based {package.pricing_model} pricing. "
                f"Floor price: {selected_option.price_guidance.get('floor') if selected_option.price_guidance else 'N/A'}",
            )

        floor_price = (
            Decimal(str(selected_option.price_guidance.get("floor", 0)))
            if selected_option.price_guidance
            else Decimal("0")
        )
        bid_decimal = Decimal(str(package.bid_price))

        if bid_decimal < floor_price:
            raise ToolError(
                "PRICING_ERROR",
                f"Bid price {package.bid_price} is below floor price {floor_price} "
                f"for {package.pricing_model} pricing",
            )

    # Validate fixed pricing has rate
    if selected_option.is_fixed and not selected_option.rate:
        raise ToolError(
            "PRICING_ERROR",
            f"Product {product.product_id} pricing option has is_fixed=true but no rate specified",
        )

    # Validate minimum spend per package
    if selected_option.min_spend_per_package:
        package_budget = None
        if isinstance(package.budget, dict):
            package_budget = Decimal(str(package.budget.get("total", 0)))
        elif isinstance(package.budget, int | float):
            package_budget = Decimal(str(package.budget))

        if package_budget and package_budget < Decimal(str(selected_option.min_spend_per_package)):
            raise ToolError(
                "PRICING_ERROR",
                f"Package budget {package_budget} {selected_option.currency} is below minimum spend "
                f"{selected_option.min_spend_per_package} {selected_option.currency} for {package.pricing_model}",
            )

    # Return validated pricing information
    return {
        "pricing_model": selected_option.pricing_model,
        "rate": float(selected_option.rate) if selected_option.rate else None,
        "currency": selected_option.currency,
        "is_fixed": selected_option.is_fixed,
        "bid_price": float(package.bid_price) if package.bid_price else None,
    }


async def _validate_and_convert_format_ids(
    format_ids: list[Any], tenant_id: str, package_idx: int
) -> list[dict[str, str]]:
    """Validate and convert format_ids to FormatId objects with strict enforcement.

    Per AdCP spec, format_ids must be FormatId objects with {agent_url, id}.
    This function enforces:
    1. Only FormatId objects are accepted (no plain strings)
    2. agent_url must be a registered creative agent (default or tenant-specific)
    3. format_id must exist on the specified agent
    4. Format must pass validation (dimensions, asset requirements, etc.)

    Args:
        format_ids: List of format ID objects from request
        tenant_id: Tenant ID for looking up registered agents
        package_idx: Package index for error messages (0-based)

    Returns:
        List of validated FormatId dicts with {agent_url, id}

    Raises:
        ToolError: If any format_id is invalid, unregistered, or doesn't exist
    """
    from src.core.creative_agent_registry import CreativeAgentRegistry

    if not format_ids:
        return []

    registry = CreativeAgentRegistry()
    validated_format_ids = []

    # Get registered agents for this tenant
    registered_agents = registry._get_tenant_agents(tenant_id)
    # Normalize agent URLs for consistent comparison (strips /mcp, /a2a, /.well-known/*, trailing slashes)
    # This ensures all URL variations match: "https://example.com/mcp/" -> "https://example.com"
    from src.core.validation import normalize_agent_url

    registered_agent_urls = {normalize_agent_url(agent.agent_url) for agent in registered_agents}

    for idx, fmt_id in enumerate(format_ids):
        # STRICT ENFORCEMENT: Reject plain strings
        if isinstance(fmt_id, str):
            raise ToolError(
                "FORMAT_VALIDATION_ERROR",
                f"Package {package_idx + 1}, format_ids[{idx}]: Plain string format IDs are not supported. "
                f"Per AdCP spec, format_ids must be FormatId objects with {{agent_url, id}}. "
                f'Example: {{"agent_url": "https://creative.adcontextprotocol.org", "id": "{fmt_id}"}}. '
                f"Use list_creative_formats to discover available formats.",
            )

        # Extract agent_url and id from dict/object
        if isinstance(fmt_id, dict):
            agent_url = fmt_id.get("agent_url")
            format_id = fmt_id.get("id")
        elif hasattr(fmt_id, "agent_url") and hasattr(fmt_id, "id"):
            agent_url = fmt_id.agent_url
            format_id = fmt_id.id
        else:
            raise ToolError(
                "FORMAT_VALIDATION_ERROR",
                f"Package {package_idx + 1}, format_ids[{idx}]: Invalid format_id structure. "
                f"Expected FormatId object with {{agent_url, id}}, got: {type(fmt_id).__name__}",
            )

        if not agent_url or not format_id:
            raise ToolError(
                "FORMAT_VALIDATION_ERROR",
                f"Package {package_idx + 1}, format_ids[{idx}]: FormatId object missing required fields. "
                f"Both agent_url and id are required. Got: agent_url={agent_url!r}, id={format_id!r}",
            )

        # VALIDATION: Check agent is registered
        # Normalize incoming agent_url for comparison (strips /mcp, /a2a, /.well-known/*, trailing slashes)
        normalized_agent_url = normalize_agent_url(agent_url)
        if normalized_agent_url not in registered_agent_urls:
            raise ToolError(
                "FORMAT_VALIDATION_ERROR",
                f"Package {package_idx + 1}, format_ids[{idx}]: Creative agent not registered: {agent_url}. "
                f"Registered agents: {', '.join(sorted(registered_agent_urls))}. "
                f"Contact your administrator to register this creative agent.",
            )

        # VALIDATION: Verify format exists on agent
        try:
            format_obj = await registry.get_format(agent_url, format_id)
            if not format_obj:
                raise ToolError(
                    "FORMAT_VALIDATION_ERROR",
                    f"Package {package_idx + 1}, format_ids[{idx}]: Format not found on agent. "
                    f"agent_url={agent_url}, format_id={format_id!r}. "
                    f"Use list_creative_formats to discover available formats.",
                )
        except Exception as e:
            if isinstance(e, ToolError):
                raise
            logger.exception(f"Error fetching format {format_id} from {agent_url}: {e}")
            raise ToolError(
                "FORMAT_VALIDATION_ERROR",
                f"Package {package_idx + 1}, format_ids[{idx}]: Failed to verify format on agent. "
                f"agent_url={agent_url}, format_id={format_id!r}. Error: {e}",
            )

        # Format validated - add to results
        validated_format_ids.append({"agent_url": agent_url, "id": format_id})

    return validated_format_ids


async def _create_media_buy_impl(
    buyer_ref: str,
    brand_manifest: Any,  # BrandManifest | str - REQUIRED per AdCP v2.2.0 spec
    packages: list[Any],  # REQUIRED per AdCP spec
    start_time: Any,  # datetime | Literal["asap"] | str - REQUIRED per AdCP spec
    end_time: Any,  # datetime | str - REQUIRED per AdCP spec
    budget: Any,  # Budget | float | dict - REQUIRED per AdCP spec
    po_number: str | None = None,
    product_ids: list[str] | None = None,  # Legacy format conversion
    start_date: Any | None = None,  # Legacy format conversion
    end_date: Any | None = None,  # Legacy format conversion
    total_budget: float | None = None,  # Legacy format conversion
    targeting_overlay: dict[str, Any] | None = None,
    pacing: str = "even",
    daily_budget: float | None = None,
    creatives: list[Any] | None = None,
    reporting_webhook: dict[str, Any] | None = None,
    required_axe_signals: list[str] | None = None,
    enable_creative_macro: bool = False,
    strategy_id: str | None = None,
    push_notification_config: dict[str, Any] | None = None,
    context: Context | None = None,
) -> CreateMediaBuyResponse:
    """Create a media buy with the specified parameters.

    Args:
        buyer_ref: Buyer reference for tracking (REQUIRED per AdCP spec)
        brand_manifest: Brand information manifest - inline object or URL string (REQUIRED per AdCP v2.2.0 spec)
        packages: Array of packages with products and budgets (REQUIRED)
        start_time: Campaign start time ISO 8601 or 'asap' (REQUIRED)
        end_time: Campaign end time ISO 8601 (REQUIRED)
        budget: Overall campaign budget (REQUIRED)
        po_number: Purchase order number (optional)
        product_ids: Legacy: Product IDs (converted to packages)
        start_date: Legacy: Start date (converted to start_time)
        end_date: Legacy: End date (converted to end_time)
        total_budget: Legacy: Total budget (converted to Budget object)
        targeting_overlay: Targeting overlay configuration
        pacing: Pacing strategy (even, asap, daily_budget)
        daily_budget: Daily budget limit
        creatives: Creative assets for the campaign
        reporting_webhook: Webhook configuration for automated reporting delivery
        required_axe_signals: Required targeting signals
        enable_creative_macro: Enable AXE to provide creative_macro signal
        strategy_id: Optional strategy ID for linking operations
        push_notification_config: Push notification config for status updates (MCP/A2A)
        context: FastMCP context (automatically provided)

    Returns:
        CreateMediaBuyResponse with media buy details
    """
    request_start_time = time.time()

    # DEBUG: Log incoming push_notification_config
    logger.info(f"🐛 create_media_buy called with push_notification_config={push_notification_config}")
    logger.info(f"🐛 push_notification_config type: {type(push_notification_config)}")
    if push_notification_config:
        logger.info(f"🐛 push_notification_config contents: {push_notification_config}")

    # Create request object from individual parameters (MCP-compliant)
    # Validate early with helpful error messages
    try:
        req = CreateMediaBuyRequest(
            buyer_ref=buyer_ref,
            brand_manifest=brand_manifest,
            campaign_name=None,  # Optional display name
            po_number=po_number,
            packages=packages,
            start_time=start_time,
            end_time=end_time,
            budget=budget,
            currency=None,  # Derived from product pricing_options
            product_ids=product_ids,
            start_date=start_date,
            end_date=end_date,
            total_budget=total_budget,
            targeting_overlay=targeting_overlay,
            pacing=pacing,
            daily_budget=daily_budget,
            creatives=creatives,
            reporting_webhook=reporting_webhook,
            required_axe_signals=required_axe_signals,
            enable_creative_macro=enable_creative_macro,
            strategy_id=strategy_id,
            webhook_url=None,  # Internal field, not in AdCP spec
            webhook_auth_token=None,  # Internal field, not in AdCP spec
            push_notification_config=push_notification_config,
        )
    except ValidationError as e:
        # Format validation errors with helpful context using shared helper
        raise ToolError(format_validation_error(e, context="request")) from e

    # Extract testing context first
    testing_ctx = get_testing_context(context)

    # Authentication and tenant setup
    principal_id = _get_principal_id_from_context(context)
    tenant = get_current_tenant()

    # Validate setup completion (only in production, skip for testing)
    if not testing_ctx.dry_run and not testing_ctx.test_session_id:
        try:
            validate_setup_complete(tenant["tenant_id"])
        except SetupIncompleteError as e:
            # Return helpful error with missing tasks
            task_list = "\n".join(f"  - {task['name']}: {task['description']}" for task in e.missing_tasks)
            error_msg = (
                f"Setup incomplete. Please complete the following required tasks:\n\n{task_list}\n\n"
                f"Visit the setup checklist at /tenant/{tenant['tenant_id']}/setup-checklist for details."
            )
            raise ToolError(error_msg)

    # Context management and workflow step creation - create workflow step FIRST
    ctx_manager = get_context_manager()
    ctx_id = context.headers.get("x-context-id") if hasattr(context, "headers") else None
    persistent_ctx = None
    step = None

    # Create workflow step immediately for tracking all operations
    if not persistent_ctx:
        # Check if we have an existing context ID
        if ctx_id:
            persistent_ctx = ctx_manager.get_context(ctx_id)

        # Create new context if needed
        if not persistent_ctx:
            persistent_ctx = ctx_manager.create_context(tenant_id=tenant["tenant_id"], principal_id=principal_id)

    # Create workflow step for tracking this operation
    step = ctx_manager.create_workflow_step(
        context_id=persistent_ctx.context_id,
        step_type="media_buy_creation",
        owner="system",
        status="in_progress",
        tool_name="create_media_buy",
        request_data=req.model_dump(mode="json"),
    )

    # Register push notification config if provided (MCP/A2A protocol support)
    if push_notification_config:
        from src.core.database.database_session import get_db_session
        from src.core.database.models import PushNotificationConfig as DBPushNotificationConfig

        logger.info(f"[MCP/A2A] Registering push notification config from request: {push_notification_config}")

        # Extract config details
        url = push_notification_config.get("url")
        authentication = push_notification_config.get("authentication", {})

        if url:
            # Extract authentication details (A2A format: schemes + credentials)
            schemes = authentication.get("schemes", []) if authentication else []
            auth_type = schemes[0] if schemes else None
            credentials = authentication.get("credentials") if authentication else None

            # Generate config ID
            config_id = push_notification_config.get("id") or f"pnc_{uuid.uuid4().hex[:16]}"

            # Save to database
            with get_db_session() as db:
                # Check if config already exists
                from sqlalchemy import select

                stmt = select(DBPushNotificationConfig).filter_by(
                    id=config_id, tenant_id=tenant["tenant_id"], principal_id=principal_id
                )
                existing_config = db.scalars(stmt).first()

                if existing_config:
                    # Update existing
                    existing_config.url = url
                    existing_config.authentication_type = auth_type
                    existing_config.authentication_token = credentials
                    existing_config.updated_at = datetime.now(UTC)
                    existing_config.is_active = True
                else:
                    # Create new
                    new_config = DBPushNotificationConfig(
                        id=config_id,
                        tenant_id=tenant["tenant_id"],
                        principal_id=principal_id,
                        url=url,
                        authentication_type=auth_type,
                        authentication_token=credentials,
                        is_active=True,
                    )
                    db.add(new_config)

                db.commit()
                logger.info(
                    f"[MCP/A2A] Push notification config {'updated' if existing_config else 'created'}: {config_id}"
                )

    try:
        # Validate input parameters
        # 1. Budget validation
        total_budget = req.get_total_budget()
        if total_budget <= 0:
            error_msg = f"Invalid budget: {total_budget}. Budget must be positive."
            raise ValueError(error_msg)

        # 2. DateTime validation
        now = datetime.now(UTC)

        # Validate start_time
        if req.start_time is None:
            error_msg = "start_time is required"
            raise ValueError(error_msg)

        # Handle 'asap' start_time (AdCP v1.7.0)
        if req.start_time == "asap":
            start_time = now
        else:
            # Ensure start_time is timezone-aware for comparison
            # At this point, req.start_time is guaranteed to be datetime (not str)
            assert isinstance(req.start_time, datetime), "start_time must be datetime when not 'asap'"
            start_time = req.start_time  # type: datetime
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=UTC)

            if start_time < now:
                error_msg = f"Invalid start time: {req.start_time}. Start time cannot be in the past."
                raise ValueError(error_msg)

        # Validate end_time
        if req.end_time is None:
            error_msg = "end_time is required"
            raise ValueError(error_msg)

        # Ensure end_time is timezone-aware for comparison
        end_time = req.end_time
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=UTC)

        if end_time <= start_time:
            error_msg = f"Invalid time range: end time ({req.end_time}) must be after start time ({req.start_time})."
            raise ValueError(error_msg)

        # 3. Package/Product validation
        product_ids = req.get_product_ids()
        logger.info(f"DEBUG: Extracted product_ids: {product_ids}")
        logger.info(
            f"DEBUG: Request packages: {[{'package_id': p.package_id, 'product_id': p.product_id, 'buyer_ref': p.buyer_ref} for p in (req.packages or [])]}"
        )
        if not product_ids:
            error_msg = "At least one product is required."
            raise ValueError(error_msg)

        if req.packages:
            for package in req.packages:
                # Check product_id field per AdCP spec
                if not package.product_id:
                    error_msg = f"Package {package.buyer_ref} must specify product_id."
                    raise ValueError(error_msg)

            # Check for duplicate product_ids across packages
            product_id_counts: dict[str, int] = {}
            for package in req.packages:
                if package.product_id:
                    product_id_counts[package.product_id] = product_id_counts.get(package.product_id, 0) + 1

            duplicate_products = [pid for pid, count in product_id_counts.items() if count > 1]
            if duplicate_products:
                error_msg = f"Duplicate product_id(s) found in packages: {', '.join(duplicate_products)}. Each product can only be used once per media buy."
                raise ValueError(error_msg)

        # 4. Currency-specific budget validation
        from decimal import Decimal

        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import CurrencyLimit
        from src.core.database.models import Product as ProductModel

        # Get products first to determine currency from pricing options
        with get_db_session() as session:
            # Get products from database
            from sqlalchemy.orm import selectinload

            stmt = (
                select(ProductModel)
                .where(ProductModel.tenant_id == tenant["tenant_id"], ProductModel.product_id.in_(product_ids))
                .options(selectinload(ProductModel.pricing_options))
            )
            products = session.scalars(stmt).all()

            # Build product lookup map
            product_map = {p.product_id: p for p in products}

            # Get currency from product pricing options (per AdCP spec)
            request_currency = None

            # First, try to get currency from first package's pricing option
            if req.packages and len(req.packages) > 0:
                first_package = req.packages[0]
                package_product_ids = [first_package.product_id] if first_package.product_id else []

                if package_product_ids and package_product_ids[0] in product_map:
                    product = product_map[package_product_ids[0]]
                    pricing_options = product.pricing_options or []

                    # Find the pricing option matching the package's pricing_model
                    if first_package.pricing_model and pricing_options:
                        matching_option = next(
                            (po for po in pricing_options if po.pricing_model == first_package.pricing_model), None
                        )
                        if matching_option:
                            request_currency = matching_option.currency

                    # If no pricing_model specified, use first pricing option's currency
                    if not request_currency and pricing_options:
                        request_currency = pricing_options[0].currency

            # Fallback to deprecated/legacy sources
            if not request_currency and req.currency:
                # Deprecated field, but still supported for backward compatibility
                request_currency = req.currency
            elif not request_currency and req.budget and hasattr(req.budget, "currency"):
                # Legacy: Extract currency from Budget object
                request_currency = req.budget.currency
            elif (
                not request_currency
                and req.packages
                and req.packages[0].budget
                and hasattr(req.packages[0].budget, "currency")
            ):
                # Legacy: Extract currency from package budget object
                request_currency = req.packages[0].budget.currency

            # Final fallback
            if not request_currency:
                request_currency = "USD"

            # Get currency limits for this tenant and currency
            stmt = select(CurrencyLimit).where(
                CurrencyLimit.tenant_id == tenant["tenant_id"], CurrencyLimit.currency_code == request_currency
            )
            currency_limit = session.scalars(stmt).first()

            # Check if tenant supports this currency
            if not currency_limit:
                error_msg = (
                    f"Currency {request_currency} is not supported by this publisher. "
                    f"Contact the publisher to add support for this currency."
                )
                raise ValueError(error_msg)

            # NEW: Validate pricing_model selections (AdCP PR #88)
            # Store validated pricing info for later use in adapter
            package_pricing_info = {}
            if req.packages:
                for package in req.packages:
                    # Get product ID for this package (AdCP spec: single product per package)
                    package_product_ids = [package.product_id] if package.product_id else []

                    # Validate pricing for the product
                    if package_product_ids:
                        product_id = package_product_ids[0]
                        if product_id in product_map:
                            try:
                                pricing_info = _validate_pricing_model_selection(
                                    package=package,
                                    product=product_map[product_id],
                                    campaign_currency=request_currency,
                                )
                                # Store for adapter use
                                if package.package_id:
                                    package_pricing_info[package.package_id] = pricing_info
                            except ToolError as e:
                                # Re-raise pricing validation errors
                                raise ValueError(str(e))

            # Validate minimum product spend (legacy + new pricing_options)
            if currency_limit.min_package_budget:
                # Build map of product_id -> minimum spend
                product_min_spends = {}
                for product in products:
                    # Use product pricing_options min_spend if set, otherwise use currency limit minimum
                    min_spend = currency_limit.min_package_budget
                    if product.pricing_options and len(product.pricing_options) > 0:
                        first_option = product.pricing_options[0]
                        if first_option.min_spend_per_package is not None:
                            min_spend = first_option.min_spend_per_package
                    if min_spend is not None:
                        product_min_spends[product.product_id] = Decimal(str(min_spend))

                # Validate budget against minimum spend requirements
                if product_min_spends:
                    # Check if we're in legacy mode (packages without budgets)
                    is_legacy_mode = req.packages and all(not pkg.budget for pkg in req.packages)

                    # For packages with budgets, validate each package's budget
                    if req.packages and not is_legacy_mode:
                        for package in req.packages:
                            # Skip packages without budgets (shouldn't happen in v2.4 format)
                            if not package.budget:
                                continue

                            # Get the minimum spend requirement for product in this package (AdCP spec: single product)
                            package_product_ids = [package.product_id] if package.product_id else []

                            applicable_min_spends = [
                                product_min_spends[pid] for pid in package_product_ids if pid in product_min_spends
                            ]

                            if applicable_min_spends:
                                # Use the highest minimum spend among all products in package
                                required_min_spend = max(applicable_min_spends)
                                # Extract budget amount (v1.8.0 compatible)
                                from src.core.schemas import extract_budget_amount

                                package_budget_amount, _ = extract_budget_amount(package.budget, request_currency)
                                package_budget = Decimal(str(package_budget_amount))

                                if package_budget < required_min_spend:
                                    error_msg = (
                                        f"Package budget ({package_budget} {request_currency}) does not meet minimum spend requirement "
                                        f"({required_min_spend} {request_currency}) for products in this package"
                                    )
                                    raise ValueError(error_msg)
                    else:
                        # Legacy mode: single total_budget for all products
                        applicable_min_spends = list(product_min_spends.values())
                        if applicable_min_spends:
                            required_min_spend = max(applicable_min_spends)
                            budget_decimal = Decimal(str(total_budget))

                            if budget_decimal < required_min_spend:
                                error_msg = (
                                    f"Total budget ({total_budget} {request_currency}) does not meet minimum spend requirement "
                                    f"({required_min_spend} {request_currency}) for the selected products"
                                )
                                raise ValueError(error_msg)

            # Validate maximum daily spend per package (if set)
            # This is per-package to prevent buyers from splitting large budgets across many packages
            if currency_limit.max_daily_package_spend:
                flight_days = (end_time - start_time).days
                if flight_days <= 0:
                    flight_days = 1

                # Check if we're in legacy mode (packages without budgets)
                is_legacy_mode = req.packages and all(not pkg.budget for pkg in req.packages)

                # For packages with budgets, validate each package's daily budget
                if req.packages and not is_legacy_mode:
                    for package in req.packages:
                        if not package.budget:
                            continue
                        # Extract budget amount (v1.8.0 compatible)
                        from src.core.schemas import extract_budget_amount

                        package_budget_amount, _ = extract_budget_amount(package.budget, request_currency)
                        package_budget = Decimal(str(package_budget_amount))
                        package_daily_budget = package_budget / Decimal(str(flight_days))

                        if package_daily_budget > currency_limit.max_daily_package_spend:
                            error_msg = (
                                f"Package daily budget ({package_daily_budget} {request_currency}) exceeds "
                                f"maximum daily spend per package ({currency_limit.max_daily_package_spend} {request_currency}). "
                                f"This protects against accidental large budgets and prevents GAM line item proliferation."
                            )
                            raise ValueError(error_msg)
                else:
                    # Legacy mode: validate total budget
                    daily_budget = Decimal(str(total_budget)) / Decimal(str(flight_days))

                    if daily_budget > currency_limit.max_daily_package_spend:
                        error_msg = (
                            f"Daily budget ({daily_budget} {request_currency}) exceeds maximum daily spend "
                            f"({currency_limit.max_daily_package_spend} {request_currency}). "
                            f"This protects against accidental large budgets."
                        )
                        raise ValueError(error_msg)

        # Validate targeting doesn't use managed-only dimensions
        if req.targeting_overlay:
            from src.services.targeting_capabilities import validate_overlay_targeting

            violations = validate_overlay_targeting(req.targeting_overlay.model_dump(exclude_none=True))
            if violations:
                error_msg = f"Targeting validation failed: {'; '.join(violations)}"
                raise ValueError(error_msg)

    except (ValueError, PermissionError) as e:
        # Update workflow step as failed
        ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=str(e))

        # Return error response (protocol layer will add status="failed")
        return CreateMediaBuyResponse(
            buyer_ref=buyer_ref or "unknown",
            errors=[Error(code="validation_error", message=str(e), details=None)],
        )

    # Get the Principal object (needed for adapter)
    principal = get_principal_object(principal_id)
    if not principal:
        error_msg = f"Principal {principal_id} not found"
        ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
        return CreateMediaBuyResponse(
            buyer_ref=buyer_ref or "unknown",
            errors=[Error(code="authentication_error", message=error_msg, details=None)],
        )

    try:
        # Get the appropriate adapter with testing context
        adapter = get_adapter(principal, dry_run=DRY_RUN_MODE or testing_ctx.dry_run, testing_context=testing_ctx)

        # Check if manual approval is required
        manual_approval_required = (
            adapter.manual_approval_required if hasattr(adapter, "manual_approval_required") else False
        )
        manual_approval_operations = (
            adapter.manual_approval_operations if hasattr(adapter, "manual_approval_operations") else []
        )

        # Check if auto-creation is disabled in tenant config
        auto_create_enabled = tenant.get("auto_create_media_buys", True)
        product_auto_create = True  # Will be set correctly when we get products later

        if manual_approval_required and "create_media_buy" in manual_approval_operations:
            # Update existing workflow step to require approval
            ctx_manager.update_workflow_step(
                step.step_id, status="requires_approval", step_type="approval", owner="publisher"
            )

            # Workflow step already created above - no need for separate task
            pending_media_buy_id = f"pending_{uuid.uuid4().hex[:8]}"

            response_msg = (
                f"Manual approval required. Workflow Step ID: {step.step_id}. Context ID: {persistent_ctx.context_id}"
            )
            ctx_manager.add_message(persistent_ctx.context_id, "assistant", response_msg)

            # Send Slack notification for manual approval requirement
            try:
                # Get principal name for notification
                principal_name = principal.name if principal else principal_id

                # Build notifier config from tenant fields
                notifier_config = {
                    "features": {
                        "slack_webhook_url": tenant.get("slack_webhook_url"),
                        "slack_audit_webhook_url": tenant.get("slack_audit_webhook_url"),
                    }
                }
                slack_notifier = get_slack_notifier(notifier_config)

                # Create notification details
                notification_details = {
                    "total_budget": total_budget,
                    "po_number": req.po_number,
                    "start_time": start_time.isoformat(),  # Resolved from 'asap' if needed
                    "end_time": end_time.isoformat(),
                    "product_ids": req.get_product_ids(),
                    "workflow_step_id": step.step_id,
                    "context_id": persistent_ctx.context_id,
                }

                slack_notifier.notify_media_buy_event(
                    event_type="approval_required",
                    media_buy_id=pending_media_buy_id,
                    principal_name=principal_name,
                    details=notification_details,
                    tenant_name=tenant.get("name", "Unknown"),
                    tenant_id=tenant.get("tenant_id"),
                    success=True,
                )
                console.print("[green]📧 Sent manual approval notification to Slack[/green]")
            except Exception as e:
                console.print(f"[yellow]⚠️ Failed to send manual approval Slack notification: {e}[/yellow]")

            return CreateMediaBuyResponse(
                buyer_ref=req.buyer_ref,
                media_buy_id=pending_media_buy_id,
                creative_deadline=None,
                errors=[{"code": "APPROVAL_REQUIRED", "message": response_msg}],
            )

        # Get products for the media buy to check product-level auto-creation settings
        catalog = get_product_catalog()
        product_ids = req.get_product_ids()
        products_in_buy = [p for p in catalog if p.product_id in product_ids]

        # Validate and auto-generate GAM implementation_config for each product if needed
        if adapter.__class__.__name__ == "GoogleAdManager":
            from src.services.gam_product_config_service import GAMProductConfigService

            gam_validator = GAMProductConfigService()
            config_errors = []

            for product in products_in_buy:
                # Auto-generate default config if missing
                if not product.implementation_config:
                    logger.info(
                        f"Product '{product.name}' ({product.product_id}) is missing GAM configuration. "
                        f"Auto-generating defaults based on product type."
                    )
                    # Generate defaults based on product delivery type and formats
                    delivery_type = product.delivery_type if hasattr(product, "delivery_type") else "non_guaranteed"
                    formats = product.formats if hasattr(product, "formats") else None
                    product.implementation_config = gam_validator.generate_default_config(
                        delivery_type=delivery_type, formats=formats
                    )

                    # Persist the auto-generated config to database
                    with get_db_session() as db_session:
                        stmt = select(ModelProduct).filter_by(product_id=product.product_id)
                        db_product = db_session.scalars(stmt).first()
                        if db_product:
                            db_product.implementation_config = product.implementation_config
                            db_session.commit()
                            logger.info(f"Saved auto-generated GAM config for product {product.product_id}")

                # Validate the config (whether existing or auto-generated)
                is_valid, error_msg = gam_validator.validate_config(product.implementation_config)
                if not is_valid:
                    config_errors.append(
                        f"Product '{product.name}' ({product.product_id}) has invalid GAM configuration: {error_msg}"
                    )

            if config_errors:
                error_detail = "GAM configuration validation failed:\n" + "\n".join(
                    f"  • {err}" for err in config_errors
                )
                ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_detail)
                return CreateMediaBuyResponse(
                    buyer_ref=req.buyer_ref,
                    errors=[{"code": "invalid_configuration", "message": err} for err in config_errors],
                )

        product_auto_create = all(
            p.implementation_config.get("auto_create_enabled", True) if p.implementation_config else True
            for p in products_in_buy
        )

        # Check if either tenant or product disables auto-creation
        if not auto_create_enabled or not product_auto_create:
            reason = "Tenant configuration" if not auto_create_enabled else "Product configuration"

            # Update existing workflow step to require approval
            ctx_manager.update_workflow_step(
                step.step_id, status="requires_approval", step_type="approval", owner="publisher"
            )

            # Workflow step already created above - no need for separate task
            pending_media_buy_id = f"pending_{uuid.uuid4().hex[:8]}"

            response_msg = f"Media buy requires approval due to {reason.lower()}. Workflow Step ID: {step.step_id}. Context ID: {persistent_ctx.context_id}"
            ctx_manager.add_message(persistent_ctx.context_id, "assistant", response_msg)

            # Send Slack notification for configuration-based approval requirement
            try:
                # Get principal name for notification
                principal_name = principal.name if principal else principal_id

                # Build notifier config from tenant fields
                notifier_config = {
                    "features": {
                        "slack_webhook_url": tenant.get("slack_webhook_url"),
                        "slack_audit_webhook_url": tenant.get("slack_audit_webhook_url"),
                    }
                }
                slack_notifier = get_slack_notifier(notifier_config)

                # Create notification details including configuration reason
                notification_details = {
                    "total_budget": total_budget,
                    "po_number": req.po_number,
                    "start_time": start_time.isoformat(),  # Resolved from 'asap' if needed
                    "end_time": end_time.isoformat(),
                    "product_ids": req.get_product_ids(),
                    "approval_reason": reason,
                    "workflow_step_id": step.step_id,
                    "context_id": persistent_ctx.context_id,
                    "auto_create_enabled": auto_create_enabled,
                    "product_auto_create": product_auto_create,
                }

                slack_notifier.notify_media_buy_event(
                    event_type="config_approval_required",
                    media_buy_id=pending_media_buy_id,
                    principal_name=principal_name,
                    details=notification_details,
                    tenant_name=tenant.get("name", "Unknown"),
                    tenant_id=tenant.get("tenant_id"),
                    success=True,
                )
                console.print(f"[green]📧 Sent {reason.lower()} approval notification to Slack[/green]")
            except Exception as e:
                console.print(f"[yellow]⚠️ Failed to send configuration approval Slack notification: {e}[/yellow]")

            return CreateMediaBuyResponse(
                buyer_ref=req.buyer_ref,
                workflow_step_id=step.step_id,
            )

        # Continue with synchronized media buy creation

        # Note: products_in_buy was already calculated above for product_auto_create check
        # No need to recalculate

        # Note: Key-value pairs are NOT aggregated here anymore.
        # Each product maintains its own custom_targeting_keys in implementation_config
        # which will be applied separately to its corresponding line item in GAM.
        # The adapter (google_ad_manager.py) handles this per-product targeting at line 491-494

        # Convert products to MediaPackages
        # If req.packages provided, use format_ids from request; otherwise use product.formats
        packages = []
        for product in products_in_buy:
            # Determine format_ids to use
            format_ids_to_use = []

            # Check if this product has a corresponding package in the request with format_ids
            if req.packages:
                # Find the package for this product
                matching_package = None
                for pkg in req.packages:
                    if pkg.product_id == product.product_id:
                        matching_package = pkg
                        break

                # If found and has format_ids, validate and use those
                if matching_package and hasattr(matching_package, "format_ids") and matching_package.format_ids:
                    # Validate that requested formats are supported by product
                    # Format is composite key: (agent_url, format_id) per AdCP spec
                    # Note: AdCP JSON uses "id" field, but Pydantic object uses "format_id" attribute
                    # Build set of (agent_url, format_id) tuples for comparison
                    product_format_keys = set()
                    if product.formats:
                        for fmt in product.formats:
                            agent_url = None
                            format_id = None

                            if isinstance(fmt, dict):
                                # Database JSONB: uses "id" per AdCP spec
                                agent_url = fmt.get("agent_url")
                                format_id = fmt.get("id") or fmt.get(
                                    "format_id"
                                )  # "id" is AdCP spec, "format_id" is legacy
                            elif hasattr(fmt, "agent_url") and (hasattr(fmt, "format_id") or hasattr(fmt, "id")):
                                # Pydantic object: uses "format_id" attribute (serializes to "id" in JSON)
                                agent_url = fmt.agent_url
                                format_id = getattr(fmt, "format_id", None) or getattr(fmt, "id", None)
                            elif isinstance(fmt, str):
                                # Legacy: plain string format ID (no agent_url)
                                format_id = fmt

                            if format_id:
                                # Normalize agent_url by removing trailing slash for consistent comparison
                                normalized_url = agent_url.rstrip("/") if agent_url else None
                                product_format_keys.add((normalized_url, format_id))

                    # Build set of requested format keys for comparison
                    requested_format_keys = set()
                    for fmt in matching_package.format_ids:
                        agent_url = None
                        format_id = None

                        if isinstance(fmt, dict):
                            # JSON from request: uses "id" per AdCP spec
                            agent_url = fmt.get("agent_url")
                            format_id = fmt.get("id") or fmt.get(
                                "format_id"
                            )  # "id" is AdCP spec, "format_id" is legacy
                        elif hasattr(fmt, "agent_url") and (hasattr(fmt, "format_id") or hasattr(fmt, "id")):
                            # Pydantic object: uses "format_id" attribute
                            agent_url = fmt.agent_url
                            format_id = getattr(fmt, "format_id", None) or getattr(fmt, "id", None)
                        elif isinstance(fmt, str):
                            # Legacy: plain string
                            format_id = fmt

                        if format_id:
                            # Normalize agent_url by removing trailing slash for consistent comparison
                            normalized_url = agent_url.rstrip("/") if agent_url else None
                            requested_format_keys.add((normalized_url, format_id))

                    def format_display(url: str | None, fid: str) -> str:
                        """Format a (url, id) pair for display, handling trailing slashes."""
                        if not url:
                            return fid
                        # Remove trailing slash from URL to avoid double slashes
                        clean_url = url.rstrip("/")
                        return f"{clean_url}/{fid}"

                    unsupported_formats = [
                        format_display(url, fid)
                        for url, fid in requested_format_keys
                        if (url, fid) not in product_format_keys
                    ]

                    if unsupported_formats:
                        supported_formats_str = ", ".join(
                            [format_display(url, fid) for url, fid in product_format_keys]
                        )
                        error_msg = (
                            f"Product '{product.name}' ({product.product_id}) does not support requested format(s): "
                            f"{', '.join(unsupported_formats)}. Supported formats: {supported_formats_str}"
                        )
                        raise ValueError(error_msg)

                    # Preserve original format objects for format_ids_to_use
                    format_ids_to_use = list(matching_package.format_ids)

            # Fallback to product's formats if no request format_ids
            if not format_ids_to_use:
                format_ids_to_use = list(product.formats) if product.formats else []

            # Get CPM from pricing_options
            cpm = 10.0  # Default
            if product.pricing_options and len(product.pricing_options) > 0:
                first_option = product.pricing_options[0]
                if first_option.rate:
                    cpm = float(first_option.rate)

            packages.append(
                MediaPackage(
                    package_id=product.product_id,
                    name=product.name,
                    delivery_type=product.delivery_type,
                    cpm=cpm,
                    impressions=int(total_budget / cpm * 1000),
                    format_ids=format_ids_to_use,
                )
            )

        # Create the media buy using the adapter (SYNCHRONOUS operation)
        # Defensive null check: ensure start_time and end_time are set
        if not req.start_time or not req.end_time:
            error_msg = "start_time and end_time are required but were not properly set"
            ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
            return CreateMediaBuyResponse(
                buyer_ref=req.buyer_ref,
                errors=[Error(code="invalid_datetime", message=error_msg, details=None)],
            )

        # Call adapter with detailed error logging
        # Note: start_time variable already resolved from 'asap' to actual datetime if needed
        # Pass package_pricing_info for pricing model support (AdCP PR #88)
        try:
            response = adapter.create_media_buy(req, packages, start_time, end_time, package_pricing_info)
        except Exception as adapter_error:
            import traceback

            error_traceback = traceback.format_exc()
            logger.error(f"Adapter create_media_buy failed with traceback:\n{error_traceback}")
            raise

        # Store the media buy in memory (for backward compatibility)
        media_buys[response.media_buy_id] = (req, principal_id)

        # Determine initial status based on flight dates
        now = datetime.now(UTC)
        if now < start_time:
            media_buy_status = "ready"  # Scheduled to go live at flight start date
        elif now > end_time:
            media_buy_status = "completed"
        else:
            media_buy_status = "active"

        # Store the media buy in database (context_id is NULL for synchronous operations)
        tenant = get_current_tenant()
        with get_db_session() as session:
            new_media_buy = MediaBuy(
                media_buy_id=response.media_buy_id,
                tenant_id=tenant["tenant_id"],
                principal_id=principal_id,
                buyer_ref=req.buyer_ref,  # AdCP v2.4 buyer reference
                order_name=req.po_number or f"Order-{response.media_buy_id}",
                advertiser_name=principal.name,
                campaign_objective=getattr(req, "campaign_objective", ""),  # Optional field
                kpi_goal=getattr(req, "kpi_goal", ""),  # Optional field
                budget=total_budget,  # Extract total budget
                currency=request_currency,  # AdCP v2.4 currency field (resolved above)
                start_date=start_time.date(),  # Legacy field for compatibility
                end_date=end_time.date(),  # Legacy field for compatibility
                start_time=start_time,  # AdCP v2.4 datetime scheduling (resolved from 'asap' if needed)
                end_time=end_time,  # AdCP v2.4 datetime scheduling
                status=media_buy_status,
                raw_request=req.model_dump(mode="json"),
            )
            session.add(new_media_buy)
            session.commit()

        # Handle creative_ids in packages if provided (immediate association)
        if req.packages:
            with get_db_session() as session:
                from src.core.database.models import Creative as DBCreative
                from src.core.database.models import CreativeAssignment as DBAssignment

                # Batch load all creatives upfront to avoid N+1 queries
                all_creative_ids = []
                for package in req.packages:
                    if package.creative_ids:
                        all_creative_ids.extend(package.creative_ids)

                creatives_map: dict[str, Any] = {}
                if all_creative_ids:
                    creative_stmt = select(DBCreative).where(
                        DBCreative.tenant_id == tenant["tenant_id"],
                        DBCreative.creative_id.in_(all_creative_ids),
                    )
                    creatives_list = session.scalars(creative_stmt).all()
                    creatives_map = {str(c.creative_id): c for c in creatives_list}

                for i, package in enumerate(req.packages):
                    if package.creative_ids:
                        package_id = f"{response.media_buy_id}_pkg_{i+1}"

                        # Get platform_line_item_id from response if available
                        platform_line_item_id = None
                        if response.packages and i < len(response.packages):
                            platform_line_item_id = response.packages[i].get("platform_line_item_id")

                        # Collect platform creative IDs for association
                        platform_creative_ids = []

                        for creative_id in package.creative_ids:
                            # Get creative from batch-loaded map
                            creative = creatives_map.get(creative_id)

                            if not creative:
                                logger.warning(
                                    f"Creative {creative_id} not found for package {package_id}, skipping assignment"
                                )
                                continue

                            # Create database assignment (always create, even if not yet uploaded to GAM)
                            # Get platform_creative_id from creative.data JSON
                            platform_creative_id = creative.data.get("platform_creative_id") if creative.data else None
                            if platform_creative_id:
                                # Add to association list for immediate GAM association
                                platform_creative_ids.append(platform_creative_id)
                            else:
                                logger.warning(
                                    f"Creative {creative_id} has not been uploaded to ad server yet (no platform_creative_id). "
                                    f"Database assignment will be created, but GAM association will be skipped until creative is uploaded."
                                )

                            # Create database assignment
                            assignment_id = f"assign_{uuid.uuid4().hex[:12]}"
                            assignment = DBAssignment(
                                assignment_id=assignment_id,
                                tenant_id=tenant["tenant_id"],
                                media_buy_id=response.media_buy_id,
                                package_id=package_id,
                                creative_id=creative_id,
                            )
                            session.add(assignment)

                        session.commit()

                        # Associate creatives with line items in ad server immediately
                        if platform_line_item_id and platform_creative_ids:
                            try:
                                console.print(
                                    f"[cyan]Associating {len(platform_creative_ids)} pre-synced creatives with line item {platform_line_item_id}[/cyan]"
                                )
                                association_results = adapter.associate_creatives(
                                    [platform_line_item_id], platform_creative_ids
                                )

                                # Log results
                                for result in association_results:
                                    if result.get("status") == "success":
                                        console.print(
                                            f"  ✓ Associated creative {result['creative_id']} with line item {result['line_item_id']}"
                                        )
                                    else:
                                        console.print(
                                            f"  ✗ Failed to associate creative {result['creative_id']}: {result.get('error', 'Unknown error')}"
                                        )
                            except Exception as e:
                                logger.error(
                                    f"Failed to associate creatives with line item {platform_line_item_id}: {e}"
                                )
                        elif platform_creative_ids:
                            logger.warning(
                                f"Package {package_id} has {len(platform_creative_ids)} creatives but no platform_line_item_id from adapter. "
                                f"Creatives will need to be associated via sync_creatives."
                            )

        # Handle creatives if provided
        if req.creatives:
            # Convert Creative objects to format expected by adapter
            assets = []
            for creative in req.creatives:
                try:
                    asset = _convert_creative_to_adapter_asset(creative, req.product_ids)
                    assets.append(asset)
                except Exception as e:
                    console.print(f"[red]Error converting creative {creative.creative_id}: {e}[/red]")
                    # Add a failed status for this creative
                    creative_statuses[creative.creative_id] = CreativeStatus(
                        creative_id=creative.creative_id, status="rejected", detail=f"Conversion error: {str(e)}"
                    )
                    continue
            statuses = adapter.add_creative_assets(response.media_buy_id, assets, datetime.now())
            for status in statuses:
                creative_statuses[status.creative_id] = CreativeStatus(
                    creative_id=status.creative_id,
                    status="approved" if status.status == "approved" else "pending_review",
                    detail="Creative submitted to ad server",
                )

        # Build packages list for response (AdCP v2.4 format)
        response_packages = []
        for i, package in enumerate(req.packages):
            # Serialize the package to dict to handle any nested Pydantic objects
            # Use model_dump_internal to avoid validation that requires package_id (not set yet on request packages)
            if hasattr(package, "model_dump_internal"):
                package_dict = package.model_dump_internal()
            elif hasattr(package, "model_dump"):
                # Fallback: use model_dump with exclude_none to avoid validation errors
                package_dict = package.model_dump(exclude_none=True, mode="python")
            else:
                package_dict = package

            # Validate and convert format_ids (request field) to format_ids_to_provide (response field)
            # Per AdCP spec: request has format_ids (array of FormatId), response has format_ids_to_provide (same)
            # STRICT ENFORCEMENT: Only FormatId objects accepted, must be registered agents, formats must exist
            if "format_ids" in package_dict and package_dict["format_ids"]:
                validated_format_ids = await _validate_and_convert_format_ids(
                    package_dict["format_ids"], tenant["tenant_id"], i
                )
                package_dict["format_ids_to_provide"] = validated_format_ids
                # Remove format_ids from response (only format_ids_to_provide should be in response)
                del package_dict["format_ids"]

            # Override/add response-specific fields (package_id and status are set by server)
            response_package = {
                **package_dict,
                "package_id": f"{response.media_buy_id}_pkg_{i+1}",
                "status": TaskStatus.WORKING,
            }
            response_packages.append(response_package)

        # Ensure buyer_ref is set (defensive check)
        buyer_ref_value = req.buyer_ref if req.buyer_ref else buyer_ref
        if not buyer_ref_value:
            logger.error(f"🚨 buyer_ref is missing! req.buyer_ref={req.buyer_ref}, buyer_ref={buyer_ref}")
            buyer_ref_value = f"missing-{response.media_buy_id}"

        # Create AdCP response (protocol fields like status are added by ProtocolEnvelope wrapper)
        adcp_response = CreateMediaBuyResponse(
            buyer_ref=buyer_ref_value,
            media_buy_id=response.media_buy_id,
            packages=response_packages,
            creative_deadline=response.creative_deadline,
        )

        # Log activity
        log_tool_activity(context, "create_media_buy", request_start_time)

        # Also log specific media buy activity
        try:
            principal_name = "Unknown"
            with get_db_session() as session:
                stmt = select(ModelPrincipal).filter_by(principal_id=principal_id, tenant_id=tenant["tenant_id"])
                principal_db = session.scalars(stmt).first()
                if principal_db:
                    principal_name = principal_db.name

            # Calculate duration using new datetime fields (resolved from 'asap' if needed)
            duration_days = (end_time - start_time).days + 1

            activity_feed.log_media_buy(
                tenant_id=tenant["tenant_id"],
                principal_name=principal_name,
                media_buy_id=response.media_buy_id,
                budget=total_budget,  # Extract total budget
                duration_days=duration_days,
                action="created",
            )
        except Exception as e:
            # Activity feed logging is non-critical, but we should log the failure
            logger.warning(f"Failed to log media buy creation to activity feed: {e}")

        # Apply testing hooks to response with campaign information (resolved from 'asap' if needed)
        campaign_info = {"start_date": start_time, "end_date": end_time, "total_budget": total_budget}

        response_data = (
            adcp_response.model_dump_internal()
            if hasattr(adcp_response, "model_dump_internal")
            else adcp_response.model_dump()
        )

        # Debug: Check if buyer_ref is in response_data before testing hooks
        if "buyer_ref" not in response_data:
            logger.error(f"🚨 buyer_ref MISSING after model_dump_internal! Keys: {list(response_data.keys())}")
        else:
            logger.info(f"✅ buyer_ref present after model_dump_internal: {response_data['buyer_ref']}")

        response_data = apply_testing_hooks(response_data, testing_ctx, "create_media_buy", campaign_info)

        # Debug: Check if buyer_ref is in response_data after testing hooks
        if "buyer_ref" not in response_data:
            logger.error(f"🚨 buyer_ref MISSING after apply_testing_hooks! Keys: {list(response_data.keys())}")
        else:
            logger.info(f"✅ buyer_ref present after apply_testing_hooks: {response_data['buyer_ref']}")

        # Reconstruct response from modified data
        # Filter out testing hook fields that aren't part of CreateMediaBuyResponse schema
        valid_fields = {
            "status",
            "buyer_ref",
            "task_id",
            "media_buy_id",
            "creative_deadline",
            "packages",
            "errors",
            "workflow_step_id",
        }
        filtered_data = {k: v for k, v in response_data.items() if k in valid_fields}

        # Debug: Check if buyer_ref is in filtered_data
        if "buyer_ref" not in filtered_data:
            logger.error(f"🚨 buyer_ref MISSING after filtering! filtered_data keys: {list(filtered_data.keys())}")
            logger.error(f"🚨 response_data keys: {list(response_data.keys())}")
            # Add buyer_ref back if it's somehow missing
            filtered_data["buyer_ref"] = buyer_ref_value
        else:
            logger.info(f"✅ buyer_ref present in filtered_data: {filtered_data['buyer_ref']}")

        # Ensure required fields are present (validator compliance)
        if "status" not in filtered_data:
            filtered_data["status"] = "completed"
        if "buyer_ref" not in filtered_data:
            filtered_data["buyer_ref"] = buyer_ref_value

        # Use explicit fields for validator (instead of **kwargs)
        modified_response = CreateMediaBuyResponse(
            buyer_ref=filtered_data["buyer_ref"],
            media_buy_id=filtered_data.get("media_buy_id"),
            creative_deadline=filtered_data.get("creative_deadline"),
            packages=filtered_data.get("packages"),
            errors=filtered_data.get("errors"),
        )

        # Mark workflow step as completed on success
        ctx_manager.update_workflow_step(step.step_id, status="completed")

        # Send Slack notification for successful media buy creation
        try:
            # Get principal name for notification (reuse from activity logging above)
            principal_name = "Unknown"
            with get_db_session() as session:
                stmt = select(ModelPrincipal).filter_by(principal_id=principal_id, tenant_id=tenant["tenant_id"])
                principal_db = session.scalars(stmt).first()
                if principal_db:
                    principal_name = principal_db.name

            # Build notifier config from tenant fields
            notifier_config = {
                "features": {
                    "slack_webhook_url": tenant.get("slack_webhook_url"),
                    "slack_audit_webhook_url": tenant.get("slack_audit_webhook_url"),
                }
            }
            slack_notifier = get_slack_notifier(notifier_config)

            # Create success notification details
            success_details = {
                "total_budget": total_budget,
                "po_number": req.po_number,
                "start_time": start_time.isoformat(),  # Resolved from 'asap' if needed
                "end_time": end_time.isoformat(),
                "product_ids": req.get_product_ids(),
                "duration_days": (end_time - start_time).days + 1,
                "packages_count": len(response_packages) if response_packages else 0,
                "creatives_count": len(req.creatives) if req.creatives else 0,
                "workflow_step_id": step.step_id,
            }

            slack_notifier.notify_media_buy_event(
                event_type="created",
                media_buy_id=response.media_buy_id,
                principal_name=principal_name,
                details=success_details,
                tenant_name=tenant.get("name", "Unknown"),
                tenant_id=tenant.get("tenant_id"),
                success=True,
            )

            console.print(f"[green]🎉 Sent success notification to Slack for media buy {response.media_buy_id}[/green]")
        except Exception as e:
            console.print(f"[yellow]⚠️ Failed to send success Slack notification: {e}[/yellow]")

        # Log to audit logs for business activity feed
        audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
        audit_logger.log_operation(
            operation="create_media_buy",
            principal_name=principal_name,
            principal_id=principal_id or "anonymous",
            adapter_id="mcp_server",
            success=True,
            details={
                "media_buy_id": response.media_buy_id,
                "total_budget": total_budget,
                "po_number": req.po_number,
                "duration_days": (end_time - start_time).days + 1,  # Resolved from 'asap' if needed
                "product_count": len(req.get_product_ids()),
                "packages_count": len(response_packages) if response_packages else 0,
            },
        )

        return modified_response

    except Exception as e:
        # Update workflow step as failed on any error during execution
        if step:
            ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=str(e))

        # Send Slack notification for failed media buy creation
        try:
            # Get principal name for notification
            principal_name = "Unknown"
            if principal:
                principal_name = principal.name

            # Build notifier config from tenant fields
            notifier_config = {
                "features": {
                    "slack_webhook_url": tenant.get("slack_webhook_url"),
                    "slack_audit_webhook_url": tenant.get("slack_audit_webhook_url"),
                }
            }
            slack_notifier = get_slack_notifier(notifier_config)

            # Create failure notification details
            failure_details = {
                "total_budget": total_budget if "total_budget" in locals() else 0,
                "po_number": req.po_number,
                "start_time": (
                    start_time.isoformat() if "start_time" in locals() else None
                ),  # Resolved from 'asap' if needed
                "end_time": end_time.isoformat() if "end_time" in locals() else None,
                "product_ids": req.get_product_ids(),
                "error_message": str(e),
                "workflow_step_id": step.step_id if step else "unknown",
            }

            slack_notifier.notify_media_buy_event(
                event_type="failed",
                media_buy_id=None,
                principal_name=principal_name,
                details=failure_details,
                tenant_name=tenant.get("name", "Unknown"),
                tenant_id=tenant.get("tenant_id"),
                success=False,
                error_message=str(e),
            )

            console.print(f"[red]❌ Sent failure notification to Slack: {str(e)}[/red]")
        except Exception as notify_error:
            console.print(f"[yellow]⚠️ Failed to send failure Slack notification: {notify_error}[/yellow]")

        # Log to audit logs for failed operation
        try:
            audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
            audit_logger.log_operation(
                operation="create_media_buy",
                principal_name=principal.name if principal else "unknown",
                principal_id=principal_id or "anonymous",
                adapter_id="mcp_server",
                success=False,
                error_message=str(e),
                details={
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "po_number": req.po_number if req else None,
                    "total_budget": total_budget if "total_budget" in locals() else 0,
                },
            )
        except Exception as audit_error:
            # Audit logging failure is non-critical, but we should log it
            logger.warning(f"Failed to log failed media buy creation to audit: {audit_error}")

        raise ToolError("MEDIA_BUY_CREATION_ERROR", f"Failed to create media buy: {str(e)}")


@mcp.tool()
async def create_media_buy(
    buyer_ref: str,
    brand_manifest: Any,  # BrandManifest | str - REQUIRED per AdCP v2.2.0 spec
    packages: list[Any],  # REQUIRED per AdCP spec
    start_time: Any,  # datetime | Literal["asap"] | str - REQUIRED per AdCP spec
    end_time: Any,  # datetime | str - REQUIRED per AdCP spec
    budget: Any,  # Budget | float | dict - REQUIRED per AdCP spec
    po_number: str | None = None,
    product_ids: list[str] | None = None,  # Legacy format conversion
    start_date: Any | None = None,  # Legacy format conversion
    end_date: Any | None = None,  # Legacy format conversion
    total_budget: float | None = None,  # Legacy format conversion
    targeting_overlay: dict[str, Any] | None = None,
    pacing: str = "even",
    daily_budget: float | None = None,
    creatives: list[Any] | None = None,
    reporting_webhook: dict[str, Any] | None = None,
    required_axe_signals: list[str] | None = None,
    enable_creative_macro: bool = False,
    strategy_id: str | None = None,
    push_notification_config: dict[str, Any] | None = None,
    webhook_url: str | None = None,
    context: Context | None = None,
) -> CreateMediaBuyResponse:
    """Create a media buy with the specified parameters.

    MCP tool wrapper that delegates to the shared implementation.

    Args:
        buyer_ref: Buyer reference for tracking (REQUIRED per AdCP spec)
        brand_manifest: Brand information manifest - inline object or URL string (REQUIRED per AdCP v2.2.0 spec)
        packages: Array of packages with products and budgets (REQUIRED)
        start_time: Campaign start time ISO 8601 or 'asap' (REQUIRED)
        end_time: Campaign end time ISO 8601 (REQUIRED)
        budget: Overall campaign budget (REQUIRED)
        po_number: Purchase order number (optional)
        product_ids: Legacy: Product IDs (converted to packages)
        start_date: Legacy: Start date (converted to start_time)
        end_date: Legacy: End date (converted to end_time)
        total_budget: Legacy: Total budget (converted to Budget object)
        targeting_overlay: Targeting overlay configuration
        pacing: Pacing strategy (even, asap, daily_budget)
        daily_budget: Daily_budget limit
        creatives: Creative assets for the campaign
        reporting_webhook: Webhook configuration for automated reporting delivery
        required_axe_signals: Required targeting signals
        enable_creative_macro: Enable AXE to provide creative_macro signal
        strategy_id: Optional strategy ID for linking operations
        push_notification_config: Push notification config dict with url, authentication (AdCP spec)
        context: FastMCP context (automatically provided)

    Returns:
        CreateMediaBuyResponse with media buy details
    """
    return await _create_media_buy_impl(
        buyer_ref=buyer_ref,
        brand_manifest=brand_manifest,
        po_number=po_number,
        packages=packages,
        start_time=start_time,
        end_time=end_time,
        budget=budget,
        product_ids=product_ids,
        start_date=start_date,
        end_date=end_date,
        total_budget=total_budget,
        targeting_overlay=targeting_overlay,
        pacing=pacing,
        daily_budget=daily_budget,
        creatives=creatives,
        reporting_webhook=reporting_webhook,
        required_axe_signals=required_axe_signals,
        enable_creative_macro=enable_creative_macro,
        strategy_id=strategy_id,
        push_notification_config=push_notification_config,
        context=context,
    )


# Unified update tools
def _update_media_buy_impl(
    media_buy_id: str,
    buyer_ref: str = None,
    active: bool = None,
    flight_start_date: str = None,
    flight_end_date: str = None,
    budget: float = None,
    currency: str = None,
    targeting_overlay: dict = None,
    start_time: str = None,
    end_time: str = None,
    pacing: str = None,
    daily_budget: float = None,
    packages: list = None,
    creatives: list = None,
    push_notification_config: dict | None = None,
    context: Context = None,
) -> UpdateMediaBuyResponse:
    """Shared implementation for update_media_buy (used by both MCP and A2A).

    Update a media buy with campaign-level and/or package-level changes.

    Args:
        media_buy_id: Media buy ID to update (required)
        buyer_ref: Update buyer reference
        active: True to activate, False to pause entire campaign
        flight_start_date: Change start date (if not started)
        flight_end_date: Extend or shorten campaign
        budget: Update total budget
        currency: Update currency (ISO 4217)
        targeting_overlay: Update global targeting
        start_time: Update start datetime
        end_time: Update end datetime
        pacing: Pacing strategy (even, asap, daily_budget)
        daily_budget: Daily spend cap across all packages
        packages: Package-specific updates
        creatives: Add new creatives
        push_notification_config: Push notification config for status updates (AdCP spec, optional)
        context: FastMCP context (automatically provided)

    Returns:
        UpdateMediaBuyResponse with updated media buy details
    """
    # Create request object from individual parameters (MCP-compliant)
    # Handle deprecated field names (backward compatibility)
    if flight_start_date and not start_time:
        start_time = flight_start_date
    if flight_end_date and not end_time:
        end_time = flight_end_date

    # Convert flat budget/currency/pacing to Budget object if budget provided
    budget_obj = None
    if budget is not None:
        from src.core.schemas import Budget

        budget_obj = Budget(
            total=budget,
            currency=currency or "USD",  # Default to USD if not specified
            pacing=pacing or "even",  # Default pacing
            daily_cap=daily_budget,  # Map daily_budget to daily_cap
        )

    # Build request with only valid AdCP fields
    # Note: flight_start_date, flight_end_date are mapped to start_time/end_time above
    # creatives and targeting_overlay are deprecated - use packages for updates
    # Filter out None values to avoid passing them to the request (strict validation in dev mode)
    request_params = {
        "media_buy_id": media_buy_id,
        "buyer_ref": buyer_ref,
        "active": active,
        "start_time": start_time,
        "end_time": end_time,
        "budget": budget_obj,
        "packages": packages,
        "push_notification_config": push_notification_config,
    }
    # Remove None values to avoid validation errors in strict mode
    request_params = {k: v for k, v in request_params.items() if v is not None}

    try:
        req = UpdateMediaBuyRequest(**request_params)  # type: ignore[arg-type]
    except ValidationError as e:
        raise ToolError(format_validation_error(e, context="update_media_buy request")) from e

    if context is None:
        raise ValueError("Context is required for update_media_buy")

    if not req.media_buy_id:
        # TODO: Handle buyer_ref case - for now just raise error
        raise ValueError("media_buy_id is required (buyer_ref lookup not yet implemented)")

    _verify_principal(req.media_buy_id, context)
    principal_id = _get_principal_id_from_context(context)  # Already verified by _verify_principal
    tenant = get_current_tenant()

    # Create or get persistent context
    ctx_manager = get_context_manager()
    ctx_id = context.headers.get("x-context-id") if hasattr(context, "headers") else None
    persistent_ctx = ctx_manager.get_or_create_context(
        tenant_id=tenant["tenant_id"],
        principal_id=principal_id,
        context_id=ctx_id,
        is_async=True,
    )

    # Create workflow step for this tool call
    step = ctx_manager.create_workflow_step(
        context_id=persistent_ctx.context_id,
        step_type="tool_call",
        owner="principal",
        status="in_progress",
        tool_name="update_media_buy",
        request_data=req.model_dump(mode="json"),  # Convert dates to strings
    )

    principal = get_principal_object(principal_id)
    if not principal:
        error_msg = f"Principal {principal_id} not found"
        ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
        return UpdateMediaBuyResponse(
            media_buy_id=req.media_buy_id or "",
            buyer_ref=req.buyer_ref or "",
            errors=[{"code": "principal_not_found", "message": error_msg}],
        )

    adapter = get_adapter(principal, dry_run=DRY_RUN_MODE)
    today = req.today or date.today()

    # Check if manual approval is required
    manual_approval_required = (
        adapter.manual_approval_required if hasattr(adapter, "manual_approval_required") else False
    )
    manual_approval_operations = (
        adapter.manual_approval_operations if hasattr(adapter, "manual_approval_operations") else []
    )

    if manual_approval_required and "update_media_buy" in manual_approval_operations:
        # Workflow step already created above - update its status
        ctx_manager.update_workflow_step(
            step.step_id,
            status="requires_approval",
            add_comment={"user": "system", "comment": "Publisher requires manual approval for all media buy updates"},
        )

        return UpdateMediaBuyResponse(
            media_buy_id=req.media_buy_id or "",
            buyer_ref=req.buyer_ref or "",
        )

    # Validate currency limits if flight dates or budget changes
    # This prevents workarounds where buyers extend flight to bypass daily max
    if req.start_time or req.end_time or req.budget or (req.packages and any(pkg.budget for pkg in req.packages)):
        from decimal import Decimal

        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import CurrencyLimit
        from src.core.database.models import MediaBuy as MediaBuyModel

        # Get media buy from database to check currency and current dates
        with get_db_session() as session:
            stmt = select(MediaBuyModel).where(MediaBuyModel.media_buy_id == req.media_buy_id)
            media_buy = session.scalars(stmt).first()

            if media_buy:
                # Determine currency (use updated or existing)
                # Extract currency from Budget object if present (and if it's an object, not plain number)
                request_currency: str
                if req.budget and hasattr(req.budget, "currency"):
                    request_currency = str(req.budget.currency)
                else:
                    request_currency = str(media_buy.currency) if media_buy.currency else "USD"

                # Get currency limit
                currency_stmt = select(CurrencyLimit).where(
                    CurrencyLimit.tenant_id == tenant["tenant_id"], CurrencyLimit.currency_code == request_currency
                )
                currency_limit = session.scalars(currency_stmt).first()

                if not currency_limit:
                    error_msg = f"Currency {request_currency} is not supported by this publisher."
                    ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
                    return UpdateMediaBuyResponse(
                        media_buy_id=req.media_buy_id or "",
                        buyer_ref=req.buyer_ref or "",
                        errors=[{"code": "currency_not_supported", "message": error_msg}],
                    )

                # Calculate new flight duration
                start = req.start_time if req.start_time else media_buy.start_time
                end = req.end_time if req.end_time else media_buy.end_time

                # Parse datetime strings if needed, handle 'asap' (AdCP v1.7.0)
                from datetime import datetime as dt

                if isinstance(start, str):
                    if start == "asap":
                        start = dt.now(UTC)
                    else:
                        start = dt.fromisoformat(start.replace("Z", "+00:00"))
                if isinstance(end, str):
                    end = dt.fromisoformat(end.replace("Z", "+00:00"))

                flight_days = (end - start).days
                if flight_days <= 0:
                    flight_days = 1

                # Validate max daily spend for packages
                if currency_limit.max_daily_package_spend and req.packages:
                    for pkg_update in req.packages:
                        if pkg_update.budget:
                            package_budget = Decimal(str(pkg_update.budget))
                            package_daily = package_budget / Decimal(str(flight_days))

                            if package_daily > currency_limit.max_daily_package_spend:
                                error_msg = (
                                    f"Updated package daily budget ({package_daily} {request_currency}) "
                                    f"exceeds maximum ({currency_limit.max_daily_package_spend} {request_currency}). "
                                    f"Flight date changes that reduce daily budget are not allowed to bypass limits."
                                )
                                ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
                                return UpdateMediaBuyResponse(
                                    media_buy_id=req.media_buy_id or "",
                                    buyer_ref=req.buyer_ref or "",
                                    errors=[{"code": "budget_limit_exceeded", "message": error_msg}],
                                )

    # Handle campaign-level updates
    if req.active is not None:
        action = "resume_media_buy" if req.active else "pause_media_buy"
        result = adapter.update_media_buy(
            media_buy_id=req.media_buy_id,
            buyer_ref=req.buyer_ref or "",
            action=action,
            package_id=None,
            budget=None,
            today=datetime.combine(today, datetime.min.time()),
        )
        if result.errors:
            return result

    # Handle package-level updates
    if req.packages:
        for pkg_update in req.packages:
            # Handle active/pause state
            if pkg_update.active is not None:
                action = "resume_package" if pkg_update.active else "pause_package"
                result = adapter.update_media_buy(
                    media_buy_id=req.media_buy_id,
                    buyer_ref=req.buyer_ref or "",
                    action=action,
                    package_id=pkg_update.package_id,
                    budget=None,
                    today=datetime.combine(today, datetime.min.time()),
                )
                if result.errors:
                    error_message = (
                        result.errors[0].get("message", "Update failed") if result.errors else "Update failed"
                    )
                    ctx_manager.update_workflow_step(
                        step.step_id,
                        status="failed",
                        error_message=error_message,
                    )
                    return result

            # Handle budget updates
            if pkg_update.budget is not None:
                result = adapter.update_media_buy(
                    media_buy_id=req.media_buy_id,
                    buyer_ref=req.buyer_ref or "",
                    action="update_package_budget",
                    package_id=pkg_update.package_id,
                    budget=int(pkg_update.budget),
                    today=datetime.combine(today, datetime.min.time()),
                )
                if result.errors:
                    error_message = (
                        result.errors[0].get("message", "Update failed") if result.errors else "Update failed"
                    )
                    ctx_manager.update_workflow_step(
                        step.step_id,
                        status="failed",
                        error_message=error_message,
                    )
                    return result

            # Handle creative_ids updates (AdCP v2.2.0+)
            if pkg_update.creative_ids is not None:
                from sqlalchemy import select

                from src.core.database.database_session import get_db_session
                from src.core.database.models import Creative as DBCreative
                from src.core.database.models import CreativeAssignment as DBAssignment

                with get_db_session() as session:
                    # Validate all creative IDs exist
                    creative_stmt = select(DBCreative).where(
                        DBCreative.tenant_id == tenant["tenant_id"],
                        DBCreative.creative_id.in_(pkg_update.creative_ids),
                    )
                    creatives_list = session.scalars(creative_stmt).all()
                    found_creative_ids = {c.creative_id for c in creatives_list}
                    missing_ids = set(pkg_update.creative_ids) - found_creative_ids

                    if missing_ids:
                        error_msg = f"Creative IDs not found: {', '.join(missing_ids)}"
                        ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
                        return UpdateMediaBuyResponse(
                            media_buy_id=req.media_buy_id or "",
                            buyer_ref=req.buyer_ref or "",
                            errors=[{"code": "creatives_not_found", "message": error_msg}],
                        )

                    # Get existing assignments for this package
                    assignment_stmt = select(DBAssignment).where(
                        DBAssignment.tenant_id == tenant["tenant_id"],
                        DBAssignment.media_buy_id == req.media_buy_id,
                        DBAssignment.package_id == pkg_update.package_id,
                    )
                    existing_assignments = session.scalars(assignment_stmt).all()
                    existing_creative_ids = {a.creative_id for a in existing_assignments}

                    # Determine added and removed creative IDs
                    requested_ids = set(pkg_update.creative_ids)
                    added_ids = requested_ids - existing_creative_ids
                    removed_ids = existing_creative_ids - requested_ids

                    # Remove old assignments
                    for assignment in existing_assignments:
                        if assignment.creative_id in removed_ids:
                            session.delete(assignment)

                    # Add new assignments
                    import uuid

                    for creative_id in added_ids:
                        assignment_id = f"assign_{uuid.uuid4().hex[:12]}"
                        assignment = DBAssignment(
                            assignment_id=assignment_id,
                            tenant_id=tenant["tenant_id"],
                            media_buy_id=req.media_buy_id,
                            package_id=pkg_update.package_id,
                            creative_id=creative_id,
                        )
                        session.add(assignment)

                    session.commit()

                    # Store results for affected_packages response
                    if not hasattr(req, "_affected_packages"):
                        req._affected_packages = []
                    req._affected_packages.append(
                        {
                            "buyer_package_ref": pkg_update.package_id,
                            "changes_applied": {
                                "creative_ids": {
                                    "added": list(added_ids),
                                    "removed": list(removed_ids),
                                    "current": pkg_update.creative_ids,
                                }
                            },
                        }
                    )

    # Handle budget updates (Budget object from AdCP spec - v1.8.0 compatible)
    if req.budget is not None:
        from src.core.schemas import extract_budget_amount

        # For UpdateMediaBuyRequest, budget is always a Budget object (not a float)
        # The currency comes from the Budget object itself or defaults to USD
        total_budget, currency = extract_budget_amount(req.budget, "USD")

        if total_budget <= 0:
            error_msg = f"Invalid budget: {total_budget}. Budget must be positive."
            ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
            return UpdateMediaBuyResponse(
                media_buy_id=req.media_buy_id or "",
                buyer_ref=req.buyer_ref or "",
                errors=[{"code": "invalid_budget", "message": error_msg}],
            )

        # Store budget update in media buy (update CreateMediaBuyRequest in place)
        if req.media_buy_id in media_buys:
            buy_data = media_buys[req.media_buy_id]
            if isinstance(buy_data, tuple) and len(buy_data) >= 2:
                # buy_data[0] is CreateMediaBuyRequest object - update it in place
                existing_req = buy_data[0]

                # Update total_budget field (legacy field on CreateMediaBuyRequest)
                if hasattr(existing_req, "total_budget"):
                    existing_req.total_budget = total_budget

                # Update buyer_ref if provided
                if req.buyer_ref and hasattr(existing_req, "buyer_ref"):
                    existing_req.buyer_ref = req.buyer_ref

                # Note: media_buys tuple stays as (CreateMediaBuyRequest, principal_id)

    # Note: Budget validation already done above (lines 4318-4336)
    # Package-level updates already handled above (lines 4266-4316)
    # Targeting updates are handled via packages (AdCP spec v2.4)

    # Create ObjectWorkflowMapping to link media buy update to workflow step
    # This enables webhook delivery when the update completes
    from src.core.database.database_session import get_db_session
    from src.core.database.models import ObjectWorkflowMapping

    with get_db_session() as session:
        mapping = ObjectWorkflowMapping(
            step_id=step.step_id,
            object_type="media_buy",
            object_id=req.media_buy_id,
            action="update",
        )
        session.add(mapping)
        session.commit()

    # Update workflow step with success
    ctx_manager.update_workflow_step(
        step.step_id,
        status="completed",
        response_data={
            "status": "accepted",
            "updates_applied": {
                "campaign_level": req.active is not None,
                "package_count": len(req.packages) if req.packages else 0,
                "budget": req.budget is not None,
                "flight_dates": req.start_time is not None or req.end_time is not None,
            },
        },
    )

    # Build affected_packages from stored results
    affected_packages = getattr(req, "_affected_packages", [])

    return UpdateMediaBuyResponse(
        media_buy_id=req.media_buy_id or "",
        buyer_ref=req.buyer_ref or "",
        affected_packages=affected_packages if affected_packages else None,
    )


@mcp.tool()
def update_media_buy(
    media_buy_id: str,
    buyer_ref: str = None,
    active: bool = None,
    flight_start_date: str = None,
    flight_end_date: str = None,
    budget: float = None,
    currency: str = None,
    targeting_overlay: dict = None,
    start_time: str = None,
    end_time: str = None,
    pacing: str = None,
    daily_budget: float = None,
    packages: list = None,
    creatives: list = None,
    push_notification_config: dict | None = None,
    context: Context = None,
) -> UpdateMediaBuyResponse:
    """Update a media buy with campaign-level and/or package-level changes.

    MCP tool wrapper that delegates to the shared implementation.

    Args:
        media_buy_id: Media buy ID to update (required)
        buyer_ref: Update buyer reference
        active: True to activate, False to pause entire campaign
        flight_start_date: Change start date (if not started)
        flight_end_date: Extend or shorten campaign
        budget: Update total budget
        currency: Update currency (ISO 4217)
        targeting_overlay: Update global targeting
        start_time: Update start datetime
        end_time: Update end datetime
        pacing: Pacing strategy (even, asap, daily_budget)
        daily_budget: Daily spend cap across all packages
        packages: Package-specific updates
        creatives: Add new creatives
        push_notification_config: Push notification config for async notifications (AdCP spec, optional)
        context: FastMCP context (automatically provided)

    Returns:
        UpdateMediaBuyResponse with updated media buy details
    """
    return _update_media_buy_impl(
        media_buy_id=media_buy_id,
        buyer_ref=buyer_ref,
        active=active,
        flight_start_date=flight_start_date,
        flight_end_date=flight_end_date,
        budget=budget,
        currency=currency,
        targeting_overlay=targeting_overlay,
        start_time=start_time,
        end_time=end_time,
        pacing=pacing,
        daily_budget=daily_budget,
        packages=packages,
        creatives=creatives,
        push_notification_config=push_notification_config,
        context=context,
    )


def _get_media_buy_delivery_impl(req: GetMediaBuyDeliveryRequest, context: Context) -> GetMediaBuyDeliveryResponse:
    """Get delivery data for one or more media buys.

    AdCP-compliant implementation that handles start_date/end_date parameters
    and returns spec-compliant response format.
    """
    from datetime import date, datetime, timedelta

    # Extract testing context for time simulation and event jumping
    testing_ctx = get_testing_context(context)

    principal_id = _get_principal_id_from_context(context)

    # Get the Principal object
    principal = get_principal_object(principal_id)
    if not principal:
        # Return AdCP-compliant error response
        return GetMediaBuyDeliveryResponse(
            reporting_period=ReportingPeriod(start=datetime.now().isoformat(), end=datetime.now().isoformat()),
            currency="USD",
            media_buy_deliveries=[],
            errors=[{"code": "principal_not_found", "message": f"Principal {principal_id} not found"}],
        )

    # Get the appropriate adapter
    adapter = get_adapter(principal, dry_run=DRY_RUN_MODE)

    # Determine reporting period
    if req.start_date and req.end_date:
        # Use provided date range
        start_dt = datetime.strptime(req.start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(req.end_date, "%Y-%m-%d")
    else:
        # Default to last 30 days
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=30)

    reporting_period = ReportingPeriod(start=start_dt.isoformat(), end=end_dt.isoformat())

    # Determine reference date for status calculations (use end_date or current date)
    reference_date = end_dt.date() if req.end_date else date.today()

    # Determine which media buys to fetch
    target_media_buys = []

    if req.media_buy_ids:
        # Specific media buy IDs requested
        for media_buy_id in req.media_buy_ids:
            if media_buy_id in media_buys:
                buy_request, buy_principal_id = media_buys[media_buy_id]
                if buy_principal_id == principal_id:
                    target_media_buys.append((media_buy_id, buy_request))
                else:
                    console.print(f"[yellow]Skipping {media_buy_id} - not owned by principal[/yellow]")
            else:
                console.print(f"[yellow]Media buy {media_buy_id} not found[/yellow]")
    elif req.buyer_refs:
        # Buyer references requested
        for media_buy_id, (buy_request, buy_principal_id) in media_buys.items():
            if (
                buy_principal_id == principal_id
                and hasattr(buy_request, "buyer_ref")
                and buy_request.buyer_ref in req.buyer_refs
            ):
                target_media_buys.append((media_buy_id, buy_request))
    else:
        # Use status_filter to determine which buys to fetch
        valid_statuses = ["active", "ready", "paused", "completed", "failed"]
        filter_statuses = []

        if req.status_filter:
            if isinstance(req.status_filter, str):
                if req.status_filter == "all":
                    filter_statuses = valid_statuses
                else:
                    filter_statuses = [req.status_filter]
            elif isinstance(req.status_filter, list):
                filter_statuses = req.status_filter
        else:
            # Default to active
            filter_statuses = ["active"]

        for media_buy_id, (buy_request, buy_principal_id) in media_buys.items():
            if buy_principal_id == principal_id:
                # Determine current status
                if reference_date < buy_request.flight_start_date:
                    current_status = "ready"
                elif reference_date > buy_request.flight_end_date:
                    current_status = "completed"
                else:
                    current_status = "active"

                if current_status in filter_statuses:
                    target_media_buys.append((media_buy_id, buy_request))

    # Collect delivery data for each media buy
    deliveries = []
    total_spend = 0.0
    total_impressions = 0
    media_buy_count = 0

    for media_buy_id, buy_request in target_media_buys:
        try:
            # Apply time simulation from testing context
            simulation_datetime = end_dt
            if testing_ctx.mock_time:
                simulation_datetime = testing_ctx.mock_time
            elif testing_ctx.jump_to_event:
                # Calculate time based on event
                simulation_datetime = TimeSimulator.jump_to_event_time(
                    testing_ctx.jump_to_event,
                    datetime.combine(buy_request.flight_start_date, datetime.min.time()),
                    datetime.combine(buy_request.flight_end_date, datetime.min.time()),
                )

            # Determine status
            if simulation_datetime.date() < buy_request.flight_start_date:
                status = "ready"
            elif simulation_datetime.date() > buy_request.flight_end_date:
                status = "completed"
            else:
                status = "active"

            # Create delivery metrics
            if any(
                [testing_ctx.dry_run, testing_ctx.mock_time, testing_ctx.jump_to_event, testing_ctx.test_session_id]
            ):
                # Use simulation for testing
                start_dt = datetime.combine(buy_request.flight_start_date, datetime.min.time())
                end_dt_campaign = datetime.combine(buy_request.flight_end_date, datetime.min.time())
                progress = TimeSimulator.calculate_campaign_progress(start_dt, end_dt_campaign, simulation_datetime)

                simulated_metrics = DeliverySimulator.calculate_simulated_metrics(
                    buy_request.total_budget, progress, testing_ctx
                )

                spend = simulated_metrics["spend"]
                impressions = simulated_metrics["impressions"]
            else:
                # Generate realistic delivery metrics
                campaign_days = (buy_request.flight_end_date - buy_request.flight_start_date).days
                days_elapsed = max(0, (simulation_datetime.date() - buy_request.flight_start_date).days)

                if campaign_days > 0:
                    progress = min(1.0, days_elapsed / campaign_days) if status != "ready" else 0.0
                else:
                    progress = 1.0 if status == "completed" else 0.0

                spend = float(buy_request.total_budget * progress)
                impressions = int(spend * 1000)  # Assume $1 CPM for simplicity

            # Create package delivery data
            package_deliveries = []
            if hasattr(buy_request, "product_ids"):
                for i, product_id in enumerate(buy_request.product_ids):
                    package_spend = spend / len(buy_request.product_ids)
                    package_impressions = impressions / len(buy_request.product_ids)

                    package_deliveries.append(
                        PackageDelivery(
                            package_id=f"pkg_{product_id}_{i}",
                            buyer_ref=getattr(buy_request, "buyer_ref", None),
                            impressions=package_impressions,
                            spend=package_spend,
                            pacing_index=1.0 if status == "active" else 0.0,
                        )
                    )

            # Create delivery data
            delivery_data = MediaBuyDeliveryData(
                media_buy_id=media_buy_id,
                buyer_ref=getattr(buy_request, "buyer_ref", None),
                status=status,
                totals=DeliveryTotals(impressions=impressions, spend=spend),
                by_package=package_deliveries,
            )

            deliveries.append(delivery_data)
            total_spend += spend
            total_impressions += impressions
            media_buy_count += 1

        except Exception as e:
            console.print(f"[red]Error getting delivery for {media_buy_id}: {e}[/red]")
            # Continue with other media buys

    # Create AdCP-compliant response
    response = GetMediaBuyDeliveryResponse(
        reporting_period=reporting_period,
        currency="USD",
        media_buy_deliveries=deliveries,
    )

    # Apply testing hooks if needed
    if any([testing_ctx.dry_run, testing_ctx.mock_time, testing_ctx.jump_to_event, testing_ctx.test_session_id]):
        # Create campaign info for testing hooks
        campaign_info = None
        if target_media_buys:
            first_buy = target_media_buys[0][1]
            campaign_info = {
                "start_date": datetime.combine(first_buy.flight_start_date, datetime.min.time()),
                "end_date": datetime.combine(first_buy.flight_end_date, datetime.min.time()),
                "total_budget": first_buy.total_budget,
            }

        # Convert to dict for testing hooks
        response_data = response.model_dump()
        response_data = apply_testing_hooks(response_data, testing_ctx, "get_media_buy_delivery", campaign_info)

        # Reconstruct response from modified data - filter out testing hook fields
        valid_fields = {
            "reporting_period",
            "currency",
            "media_buy_deliveries",
            "notification_type",
            "partial_data",
            "unavailable_count",
            "sequence_number",
            "next_expected_at",
            "errors",
        }
        filtered_data = {k: v for k, v in response_data.items() if k in valid_fields}

        # Ensure required fields are present (validator compliance)
        if "reporting_period" not in filtered_data:
            filtered_data["reporting_period"] = response_data.get("reporting_period", reporting_period)
        if "currency" not in filtered_data:
            filtered_data["currency"] = response_data.get("currency", "USD")
        if "media_buy_deliveries" not in filtered_data:
            filtered_data["media_buy_deliveries"] = response_data.get("media_buy_deliveries", [])

        # Use explicit fields for validator (instead of **kwargs)
        response = GetMediaBuyDeliveryResponse(
            reporting_period=filtered_data["reporting_period"],
            currency=filtered_data["currency"],
            media_buy_deliveries=filtered_data["media_buy_deliveries"],
            notification_type=filtered_data.get("notification_type"),
            partial_data=filtered_data.get("partial_data"),
            unavailable_count=filtered_data.get("unavailable_count"),
            sequence_number=filtered_data.get("sequence_number"),
            next_expected_at=filtered_data.get("next_expected_at"),
            errors=filtered_data.get("errors"),
        )

    return response


@mcp.tool()
def get_media_buy_delivery(
    media_buy_ids: list[str] = None,
    buyer_refs: list[str] = None,
    status_filter: str = None,
    start_date: str = None,
    end_date: str = None,
    webhook_url: str | None = None,
    context: Context = None,
) -> GetMediaBuyDeliveryResponse:
    """Get delivery data for media buys.

    AdCP-compliant implementation of get_media_buy_delivery tool.

    Args:
        media_buy_ids: Array of publisher media buy IDs to get delivery data for (optional)
        buyer_refs: Array of buyer reference IDs to get delivery data for (optional)
        status_filter: Filter by status - single status or array: 'active', 'pending', 'paused', 'completed', 'failed', 'all' (optional)
        start_date: Start date for reporting period in YYYY-MM-DD format (optional)
        end_date: End date for reporting period in YYYY-MM-DD format (optional)
        webhook_url: URL for async task completion notifications (AdCP spec, optional)
        context: FastMCP context (automatically provided)

    Returns:
        GetMediaBuyDeliveryResponse with AdCP-compliant delivery data for the requested media buys
    """
    # Create AdCP-compliant request object
    try:
        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=media_buy_ids,
            buyer_refs=buyer_refs,
            status_filter=status_filter,
            start_date=start_date,
            end_date=end_date,
        )
    except ValidationError as e:
        raise ToolError(format_validation_error(e, context="get_media_buy_delivery request")) from e

    return _get_media_buy_delivery_impl(req, context)


# --- Admin Tools ---


def _require_admin(context: Context) -> None:
    """Verify the request is from an admin user."""
    principal_id, tenant = get_principal_from_context(context)
    if tenant:
        set_current_tenant(tenant)
    if principal_id != "admin":
        raise PermissionError("This operation requires admin privileges")


@mcp.tool()
def update_performance_index(
    media_buy_id: str, performance_data: list[dict[str, Any]], webhook_url: str | None = None, context: Context = None
) -> UpdatePerformanceIndexResponse:
    """Update performance index data for a media buy.

    Args:
        media_buy_id: ID of the media buy to update
        performance_data: List of performance data objects
        webhook_url: URL for async task completion notifications (AdCP spec, optional)
        context: FastMCP context (automatically provided)

    Returns:
        UpdatePerformanceIndexResponse with operation status
    """
    # Create request object from individual parameters (MCP-compliant)
    # Convert dict performance_data to ProductPerformance objects
    from src.core.schemas import ProductPerformance

    try:
        performance_objects = [ProductPerformance(**perf) for perf in performance_data]
        req = UpdatePerformanceIndexRequest(media_buy_id=media_buy_id, performance_data=performance_objects)
    except ValidationError as e:
        raise ToolError(format_validation_error(e, context="update_performance_index request")) from e

    if context is None:
        raise ValueError("Context is required for update_performance_index")

    _verify_principal(req.media_buy_id, context)
    principal_id = _get_principal_id_from_context(context)  # Already verified by _verify_principal

    # Get the Principal object
    principal = get_principal_object(principal_id)
    if not principal:
        return UpdatePerformanceIndexResponse(
            status="failed",
            message=f"Principal {principal_id} not found",
            errors=[{"code": "principal_not_found", "message": f"Principal {principal_id} not found"}],
        )

    # Get the appropriate adapter
    adapter = get_adapter(principal, dry_run=DRY_RUN_MODE)

    # Convert ProductPerformance to PackagePerformance for the adapter
    package_performance = [
        PackagePerformance(package_id=perf.product_id, performance_index=perf.performance_index)
        for perf in req.performance_data
    ]

    # Call the adapter's update method
    success = adapter.update_media_buy_performance_index(req.media_buy_id, package_performance)

    # Log the performance update
    console.print(f"[bold green]Performance Index Update for {req.media_buy_id}:[/bold green]")
    for perf in req.performance_data:
        status_emoji = "📈" if perf.performance_index > 1.0 else "📉" if perf.performance_index < 1.0 else "➡️"
        console.print(
            f"  {status_emoji} {perf.product_id}: {perf.performance_index:.2f} (confidence: {perf.confidence_score or 'N/A'})"
        )

    # Simulate optimization based on performance
    if any(p.performance_index < 0.8 for p in req.performance_data):
        console.print("  [yellow]⚠️  Low performance detected - optimization recommended[/yellow]")

    return UpdatePerformanceIndexResponse(
        status="success" if success else "failed",
        detail=f"Performance index updated for {len(req.performance_data)} products",
    )


# --- Human-in-the-Loop Task Queue Tools ---


# @mcp.tool  # DEPRECATED - removed from MCP interface
def create_workflow_step_for_task(req, context):
    """DEPRECATED - Use context_mgr.create_workflow_step() directly."""
    raise ToolError("DEPRECATED", "This function has been deprecated. Use workflow steps directly.")
    # Original implementation removed - see git history if needed
    principal_id = get_principal_from_context(context)
    if not principal_id:
        raise ToolError("AUTHENTICATION_REQUIRED", "You must provide a valid x-adcp-auth header")

    # Get or create context for this workflow
    tenant = get_current_tenant()
    ctx_id = context.headers.get("x-context-id") if hasattr(context, "headers") else None

    # For human tasks, we always need a context
    if ctx_id:
        persistent_ctx = context_mgr.get_context(ctx_id)
    else:
        persistent_ctx = context_mgr.create_context(tenant_id=tenant["tenant_id"], principal_id=principal_id)
        ctx_id = persistent_ctx.context_id

    # Calculate due date
    due_by = None
    if req.due_in_hours:
        due_by = datetime.now() + timedelta(hours=req.due_in_hours)
    elif req.priority == "urgent":
        due_by = datetime.now() + timedelta(hours=4)
    elif req.priority == "high":
        due_by = datetime.now() + timedelta(hours=24)
    elif req.priority == "medium":
        due_by = datetime.now() + timedelta(hours=48)

    # Determine owner based on task type
    owner = "publisher"  # Most human tasks need publisher action
    if req.task_type in ["compliance_review", "manual_approval"]:
        owner = "publisher"
    elif req.task_type == "creative_approval":
        owner = "principal" if req.context_data and req.context_data.get("principal_action_needed") else "publisher"

    # Build object mappings if we have related objects
    object_mappings = []
    if req.media_buy_id:
        object_mappings.append(
            {
                "object_type": "media_buy",
                "object_id": req.media_buy_id,
                "action": "approval_required",
            }
        )
    if req.creative_id:
        object_mappings.append(
            {
                "object_type": "creative",
                "object_id": req.creative_id,
                "action": "approval_required",
            }
        )

    # Create workflow step
    step = context_mgr.create_workflow_step(
        context_id=ctx_id,
        is_async=True,
        step_type="approval",
        owner=owner,
        status="requires_approval",
        tool_name=req.operation,
        request_data={
            "task_type": req.task_type,
            "principal_id": principal_id,
            "adapter_name": req.adapter_name,
            "priority": req.priority,
            "media_buy_id": req.media_buy_id,
            "creative_id": req.creative_id,
            "operation": req.operation,
            "error_detail": req.error_detail,
            "context_data": req.context_data,
            "due_by": due_by.isoformat() if due_by else None,
        },
        assigned_to=req.assigned_to,
        object_mappings=object_mappings,
        initial_comment=req.error_detail or "Manual approval required",
    )

    task_id = step.step_id

    # Log task creation
    audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
    audit_logger.log_operation(
        operation="create_human_task",
        principal_name=principal_id,
        principal_id=principal_id,
        adapter_id="task_queue",
        success=True,
        details={
            "task_id": task_id,
            "task_type": req.task_type,
            "priority": req.priority,
        },
    )

    # Log high priority tasks
    if req.priority in ["high", "urgent"]:
        console.print(f"[bold red]🚨 HIGH PRIORITY TASK CREATED: {task_id}[/bold red]")
        console.print(f"   Type: {req.task_type}")
        console.print(f"   Error: {req.error_detail}")

    # Send webhook notification for urgent tasks (if configured)
    tenant = get_current_tenant()
    webhook_url = tenant.get("hitl_webhook_url")
    if webhook_url and req.priority == "urgent":
        try:
            import requests

            requests.post(
                webhook_url,
                json={
                    "task_id": task_id,
                    "type": req.task_type,
                    "priority": req.priority,
                    "principal": principal_id,
                    "error": req.error_detail,
                    "tenant": tenant["tenant_id"],
                },
                timeout=5,
            )
        except Exception as webhook_error:
            # Webhook notification is non-critical, but we should log the failure
            logger.warning(f"Failed to send webhook notification for task {task_id}: {webhook_error}")

    # Task is now handled entirely through WorkflowStep - no separate Task table needed
    console.print(f"[green]✅ Created workflow step {task_id}[/green]")

    # Send Slack notification for new tasks
    try:
        # Build notifier config from tenant fields
        notifier_config = {
            "features": {
                "slack_webhook_url": tenant.get("slack_webhook_url"),
                "slack_audit_webhook_url": tenant.get("slack_audit_webhook_url"),
            }
        }
        slack_notifier = get_slack_notifier(notifier_config)
        slack_notifier.notify_new_task(
            task_id=task_id,
            task_type=req.task_type,
            principal_name=principal_id,
            media_buy_id=req.media_buy_id,
            details={
                "priority": req.priority,
                "error": req.error_detail,
                "operation": req.operation,
                "adapter": req.adapter_name,
            },
            tenant_name=tenant["name"],
        )
    except Exception as e:
        console.print(f"[yellow]Failed to send Slack notification: {e}[/yellow]")

    return CreateHumanTaskResponse(task_id=task_id, status="pending", due_by=due_by)


# Removed get_pending_workflows - replaced by admin dashboard workflow views


# Removed assign_task - assignment handled through admin UI workflow management


# @mcp.tool  # DEPRECATED - removed from MCP interface
def complete_task(req, context):
    """Complete a human task with resolution details."""
    # DEPRECATED: This function has been deprecated in favor of workflow steps
    raise ToolError(
        "DEPRECATED", "Task system has been replaced with workflow steps. Use Admin UI workflow management."
    )

    with get_db_session() as db_session:
        stmt = select(Task).filter_by(task_id=req.task_id, tenant_id=tenant["tenant_id"])
        db_task = db_session.scalars(stmt).first()

        if not db_task:
            raise ToolError("NOT_FOUND", f"Task {req.task_id} not found")

        # Update database fields
        db_task.status = "completed" if req.resolution in ["approved", "completed"] else "failed"
        db_task.resolution = req.resolution
        db_task.resolution_notes = req.resolution_detail
        db_task.resolved_by = req.resolved_by
        db_task.completed_at = datetime.now(UTC)
        db_task.updated_at = datetime.now(UTC)

        # Preserve original metadata while adding resolution info
        original_metadata = db_task.task_metadata or {}
        db_task.task_metadata = {
            **original_metadata,  # Keep original fields
            "resolution": req.resolution,
            "resolution_detail": req.resolution_detail,
        }
        db_session.commit()

        # Create HumanTask object for backward compatibility (e.g., for Slack notifications)
        task = HumanTask(
            task_id=db_task.task_id,
            task_type=db_task.task_type,
            priority=original_metadata.get("priority", "medium"),
            media_buy_id=db_task.media_buy_id,
            creative_id=original_metadata.get("creative_id"),
            operation=original_metadata.get("operation"),
            principal_id=original_metadata.get("principal_id"),
            adapter_name=original_metadata.get("adapter_name"),
            error_detail=original_metadata.get("error_detail") or db_task.description,
            context_data=db_task.details or {},
            status=db_task.status,
            created_at=db_task.created_at,
            due_by=db_task.due_date,
            assigned_to=db_task.assigned_to,
            resolution=db_task.resolution,
            resolution_detail=db_task.resolution_notes,
            resolved_by=db_task.resolved_by,
            completed_at=db_task.completed_at,
        )

    audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
    audit_logger.log_operation(
        operation="complete_task",
        principal_name="admin",
        principal_id=principal_id,
        adapter_id="task_queue",
        success=True,
        details={
            "task_id": req.task_id,
            "resolution": req.resolution,
            "resolved_by": req.resolved_by,
        },
    )

    # Send Slack notification for task completion
    try:
        # Build notifier config from tenant fields
        notifier_config = {
            "features": {
                "slack_webhook_url": tenant.get("slack_webhook_url"),
                "slack_audit_webhook_url": tenant.get("slack_audit_webhook_url"),
            }
        }
        slack_notifier = get_slack_notifier(notifier_config)
        slack_notifier.notify_task_completed(
            task_id=req.task_id,
            task_type=task.task_type,
            completed_by=req.resolved_by,
            success=task.status == "completed",
            error_message=req.resolution_detail if task.status == "failed" else None,
        )
    except Exception as e:
        console.print(f"[yellow]Failed to send Slack notification: {e}[/yellow]")

    # Handle specific task types
    if task.task_type == "creative_approval" and task.creative_id:
        if req.resolution == "approved":
            # Update creative status
            if task.creative_id in creative_statuses:
                creative_statuses[task.creative_id].status = "approved"
                creative_statuses[task.creative_id].detail = "Manually approved by " + req.resolved_by
                console.print(f"[green]✅ Creative {task.creative_id} approved[/green]")

    elif task.task_type == "manual_approval" and task.operation:
        if req.resolution == "approved":
            # Execute the deferred operation
            console.print(f"[green]✅ Executing deferred operation: {task.operation}[/green]")

            # Get principal for the operation
            principal = get_principal_object(task.principal_id)
            if principal:
                adapter = get_adapter(principal, dry_run=DRY_RUN_MODE)

                if task.operation == "create_media_buy":
                    # Reconstruct and execute the create_media_buy request
                    original_req = CreateMediaBuyRequest(**task.context_data["request"])

                    # Get products for the media buy
                    catalog = get_product_catalog()
                    products_in_buy = [p for p in catalog if p.product_id in original_req.product_ids]

                    # Convert products to MediaPackages
                    packages = []
                    for product in products_in_buy:
                        first_format_id = product.formats[0] if product.formats else None

                        # Get CPM from pricing_options
                        cpm = 10.0  # Default
                        if product.pricing_options and len(product.pricing_options) > 0:
                            first_option = product.pricing_options[0]
                            if first_option.rate:
                                cpm = float(first_option.rate)

                        packages.append(
                            MediaPackage(
                                package_id=product.product_id,
                                name=product.name,
                                delivery_type=product.delivery_type,
                                cpm=cpm,
                                impressions=int(original_req.total_budget / cpm * 1000),
                                format_ids=[first_format_id] if first_format_id else [],
                            )
                        )

                    # Execute the actual creation
                    start_time = datetime.combine(original_req.start_date, datetime.min.time())
                    end_time = datetime.combine(original_req.end_date, datetime.max.time())
                    response = adapter.create_media_buy(original_req, packages, start_time, end_time)

                    # Store the media buy in memory (for backward compatibility)
                    media_buys[response.media_buy_id] = (
                        original_req,
                        task.principal_id,
                    )

                    # Store the media buy in database
                    tenant = get_current_tenant()
                    with get_db_session() as session:
                        principal = get_principal_object(task.principal_id)
                        new_media_buy = MediaBuy(
                            media_buy_id=response.media_buy_id,
                            tenant_id=tenant["tenant_id"],
                            principal_id=task.principal_id,
                            order_name=original_req.po_number or f"Order-{response.media_buy_id}",
                            advertiser_name=principal.name if principal else "Unknown",
                            campaign_objective=getattr(original_req, "campaign_objective", ""),  # Optional field
                            kpi_goal=getattr(original_req, "kpi_goal", ""),  # Optional field
                            budget=original_req.total_budget,
                            start_date=original_req.start_date.isoformat(),
                            end_date=original_req.end_date.isoformat(),
                            status=response.status or "active",
                            raw_request=original_req.model_dump(mode="json"),
                        )
                        session.add(new_media_buy)
                        session.commit()
                    console.print(f"[green]Media buy {response.media_buy_id} created after manual approval[/green]")

                elif task.operation == "update_media_buy":
                    # Reconstruct and execute the update_media_buy request
                    original_req = UpdateMediaBuyRequest(**task.context_data["request"])
                    today = original_req.today or date.today()

                    # Execute the updates
                    if original_req.active is not None:
                        action = "resume_media_buy" if original_req.active else "pause_media_buy"
                        adapter.update_media_buy(
                            media_buy_id=original_req.media_buy_id,
                            buyer_ref=original_req.buyer_ref or "",
                            action=action,
                            package_id=None,
                            budget=None,
                            today=datetime.combine(today, datetime.min.time()),
                        )

                    # Handle package updates
                    if original_req.packages:
                        for pkg_update in original_req.packages:
                            if pkg_update.active is not None:
                                action = "resume_package" if pkg_update.active else "pause_package"
                                adapter.update_media_buy(
                                    media_buy_id=original_req.media_buy_id,
                                    buyer_ref=original_req.buyer_ref or "",
                                    action=action,
                                    package_id=pkg_update.package_id,
                                    budget=None,
                                    today=datetime.combine(today, datetime.min.time()),
                                )

                    console.print(f"[green]Media buy {original_req.media_buy_id} updated after manual approval[/green]")
        else:
            console.print(f"[red]❌ Manual approval rejected for {task.operation}[/red]")

    return {
        "status": "success",
        "detail": f"Task {req.task_id} completed with resolution: {req.resolution}",
    }


# @mcp.tool  # DEPRECATED - removed from MCP interface
def verify_task(req, context):
    """Verify if a task was completed correctly by checking actual state."""
    # Get task from database
    tenant = get_current_tenant()
    task = get_task_from_db(req.task_id, tenant["tenant_id"])
    if not task:
        raise ToolError("NOT_FOUND", f"Task {req.task_id} not found")
    actual_state = {}
    expected_state = req.expected_outcome or {}
    discrepancies = []
    verified = True

    # Verify based on task type and operation
    if task.task_type == "manual_approval" and task.operation == "update_media_buy":
        # Extract expected changes from task context
        if task.context_data and "request" in task.context_data:
            update_req = task.context_data["request"]
            media_buy_id = update_req.get("media_buy_id")

            if media_buy_id and media_buy_id in media_buys:
                # Get current state
                buy_request, principal_id = media_buys[media_buy_id]

                # Check daily budget if it was being updated
                if "daily_budget" in update_req:
                    expected_budget = update_req["daily_budget"]
                    actual_budget = getattr(buy_request, "daily_budget", None)

                    actual_state["daily_budget"] = actual_budget
                    expected_state["daily_budget"] = expected_budget

                    if actual_budget != expected_budget:
                        discrepancies.append(f"Daily budget is ${actual_budget}, expected ${expected_budget}")
                        verified = False

                # Check active status
                if "active" in update_req:
                    # Would need to check adapter status
                    # For now, assume task completion means it worked
                    actual_state["active"] = task.status == "completed"
                    expected_state["active"] = update_req["active"]

                # Check package updates
                if "packages" in update_req:
                    for pkg_update in update_req["packages"]:
                        if "budget" in pkg_update:
                            # Would need to query adapter for actual package budget
                            expected_state[f"package_{pkg_update['package_id']}_budget"] = pkg_update["budget"]
                            # For demo, assume it matches if task completed
                            actual_state[f"package_{pkg_update['package_id']}_budget"] = (
                                pkg_update["budget"] if task.status == "completed" else 0
                            )

    elif task.task_type == "creative_approval":
        # Check if creative was actually approved
        creative_id = task.creative_id
        if creative_id and creative_id in creative_statuses:
            actual_status = creative_statuses[creative_id].status
            actual_state["creative_status"] = actual_status
            expected_state["creative_status"] = "approved"

            if actual_status != "approved" and task.resolution == "approved":
                discrepancies.append(f"Creative {creative_id} status is {actual_status}, expected approved")
                verified = False

    return VerifyTaskResponse(
        task_id=req.task_id,
        verified=verified,
        actual_state=actual_state,
        expected_state=expected_state,
        discrepancies=discrepancies,
    )


# @mcp.tool  # DEPRECATED - removed from MCP interface
def mark_task_complete(req, context):
    """Mark a task as complete with automatic verification."""
    # Admin only
    principal_id, tenant = get_principal_from_context(context)
    if tenant:
        set_current_tenant(tenant)
    else:
        tenant = get_current_tenant()
    if principal_id != f"{tenant['tenant_id']}_admin":
        raise ToolError("PERMISSION_DENIED", "Only administrators can mark tasks complete")

    # First verify the task
    verify_req = VerifyTaskRequest(task_id=req.task_id)
    verification = verify_task(verify_req, context)

    if not verification.verified and not req.override_verification:
        return {
            "status": "verification_failed",
            "verified": False,
            "discrepancies": verification.discrepancies,
            "message": "Task verification failed. Use override_verification=true to force completion.",
        }

    # Update task in database directly
    from src.core.database.database_session import get_db_session

    # DEPRECATED: Task model removed in favor of workflow system
    raise ToolError("DEPRECATED", "Task system has been replaced with workflow steps.")

    with get_db_session() as db_session:
        stmt = select(Task).filter_by(task_id=req.task_id, tenant_id=tenant["tenant_id"])
        db_task = db_session.scalars(stmt).first()

        if not db_task:
            raise ToolError("NOT_FOUND", f"Task {req.task_id} not found")

        # Mark as complete
        resolution_detail = f"Marked complete by {req.completed_by}"
        if not verification.verified:
            resolution_detail += " (verification overridden)"

        db_task.status = "completed"
        db_task.resolution = "completed"
        db_task.resolution_notes = resolution_detail
        db_task.resolved_by = req.completed_by
        db_task.completed_at = datetime.now(UTC)
        db_task.updated_at = datetime.now(UTC)

        # Update metadata with verification results
        original_metadata = db_task.task_metadata or {}
        db_task.task_metadata = {
            **original_metadata,
            "resolution": "completed",
            "resolution_detail": resolution_detail,
            "verification": verification.model_dump(),
        }
        db_session.commit()

    audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
    audit_logger.log_operation(
        operation="mark_task_complete",
        principal_name="admin",
        principal_id=principal_id,
        adapter_id="task_queue",
        success=True,
        details={
            "task_id": req.task_id,
            "verified": verification.verified,
            "override": req.override_verification,
            "completed_by": req.completed_by,
        },
    )

    return {
        "status": "success",
        "task_id": req.task_id,
        "verified": verification.verified,
        "verification_details": {
            "actual_state": verification.actual_state,
            "expected_state": verification.expected_state,
            "discrepancies": verification.discrepancies,
        },
        "message": f"Task marked complete by {req.completed_by}",
    }


# Dry run logs are now handled by the adapters themselves


def get_product_catalog() -> list[Product]:
    """Get products for the current tenant."""
    from sqlalchemy.orm import selectinload

    tenant = get_current_tenant()

    with get_db_session() as session:
        stmt = (
            select(ModelProduct)
            .filter_by(tenant_id=tenant["tenant_id"])
            .options(selectinload(ModelProduct.pricing_options))
        )
        products = session.scalars(stmt).all()

        loaded_products = []
        for product in products:
            # Convert ORM model to Pydantic schema
            # Parse JSON fields that might be strings (SQLite) or dicts (PostgreSQL)
            def safe_json_parse(value):
                if isinstance(value, str):
                    try:
                        return json.loads(value)
                    except (json.JSONDecodeError, TypeError):
                        return value
                return value

            # Parse formats - now stored as strings by the validator
            format_ids = safe_json_parse(product.formats) or []
            # Ensure it's a list of strings (validator guarantees this)
            if not isinstance(format_ids, list):
                format_ids = []

            # Convert pricing_options ORM objects to Pydantic objects
            from src.core.schemas import PricingOption as PricingOptionSchema

            pricing_options = []
            for po in product.pricing_options:
                pricing_option_data = {
                    "pricing_option_id": f"{po.pricing_model}_{po.currency}_{po.id}",
                    "pricing_model": po.pricing_model,
                    "rate": float(po.rate) if po.rate else None,
                    "currency": po.currency,
                    "is_fixed": po.is_fixed,
                    "price_guidance": safe_json_parse(po.price_guidance) if po.price_guidance else None,
                    "parameters": safe_json_parse(po.parameters) if po.parameters else None,
                    "min_spend_per_package": float(po.min_spend_per_package) if po.min_spend_per_package else None,
                }
                pricing_options.append(PricingOptionSchema(**pricing_option_data))

            product_data = {
                "product_id": product.product_id,
                "name": product.name,
                "description": product.description,
                "formats": format_ids,
                "delivery_type": product.delivery_type,
                "pricing_options": pricing_options,
                "measurement": (
                    safe_json_parse(product.measurement)
                    if hasattr(product, "measurement") and product.measurement
                    else None
                ),
                "creative_policy": (
                    safe_json_parse(product.creative_policy)
                    if hasattr(product, "creative_policy") and product.creative_policy
                    else None
                ),
                "is_custom": product.is_custom,
                "expires_at": product.expires_at,
                # Note: brief_relevance is populated dynamically when brief is provided
                "implementation_config": safe_json_parse(product.implementation_config),
                # Required per AdCP spec: either properties OR property_tags
                "properties": (
                    safe_json_parse(product.properties)
                    if hasattr(product, "properties") and product.properties
                    else None
                ),
                "property_tags": (
                    safe_json_parse(product.property_tags)
                    if hasattr(product, "property_tags") and product.property_tags
                    else ["all_inventory"]  # Default required per AdCP spec
                ),
            }
            loaded_products.append(Product(**product_data))

    return loaded_products


# Creative macro support is now simplified to a single creative_macro string
# that AEE can provide as a third type of provided_signal.
# Ad servers like GAM can inject this string into creatives.

if __name__ == "__main__":
    init_db(exit_on_error=True)  # Exit on error when run as main
    # Server is now run via run_server.py script

# Always add health check endpoint
from fastapi import Request
from fastapi.responses import JSONResponse

# --- Strategy and Simulation Control ---
from src.core.strategy import StrategyManager


def get_strategy_manager(context: Context | None) -> StrategyManager:
    """Get strategy manager for current context."""
    principal_id, tenant_config = get_principal_from_context(context)
    if tenant_config:
        set_current_tenant(tenant_config)
    else:
        tenant_config = get_current_tenant()

    if not tenant_config:
        raise ToolError("No tenant configuration found")

    return StrategyManager(tenant_id=tenant_config.get("tenant_id"), principal_id=principal_id)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request):
    """Health check endpoint."""
    return JSONResponse({"status": "healthy", "service": "mcp"})


@mcp.custom_route("/admin/reset-db-pool", methods=["POST"])
async def reset_db_pool(request: Request):
    """Reset database connection pool after external data changes.

    This is a testing-only endpoint that flushes the SQLAlchemy connection pool,
    ensuring fresh connections see recently committed data. Only works when
    ADCP_TESTING environment variable is set to 'true'.

    Use case: E2E tests that initialize data via external script need to ensure
    the running MCP server's connection pool picks up that fresh data.
    """
    # Security: Only allow in testing mode
    if os.getenv("ADCP_TESTING") != "true":
        logger.warning("Attempted to reset DB pool outside testing mode")
        return JSONResponse({"error": "This endpoint is only available in testing mode"}, status_code=403)

    try:
        from src.core.database.database_session import reset_engine

        logger.info("Resetting database connection pool, provider cache, and tenant context (testing mode)")

        # Reset SQLAlchemy connection pool
        reset_engine()
        logger.info("  ✓ Database connection pool reset")

        # CRITICAL: Also clear the product catalog provider cache
        # The provider cache holds DatabaseProductCatalog instances that may have
        # stale data from before init_database_ci.py ran
        from product_catalog_providers.factory import _provider_cache

        provider_count = len(_provider_cache)
        _provider_cache.clear()
        logger.info(f"  ✓ Cleared {provider_count} cached product catalog provider(s)")

        # CRITICAL: Clear tenant context ContextVar
        # After data initialization, the tenant context may contain stale tenant data
        # that was loaded before products were created. Force fresh tenant lookup.
        from src.core.config_loader import current_tenant

        try:
            current_tenant.set(None)
            logger.info("  ✓ Cleared tenant context (will force fresh lookup on next request)")
        except Exception as ctx_error:
            logger.warning(f"  ⚠️ Could not clear tenant context: {ctx_error}")

        return JSONResponse(
            {
                "status": "success",
                "message": "Database connection pool, provider cache, and tenant context reset successfully",
                "providers_cleared": provider_count,
            }
        )
    except Exception as e:
        logger.error(f"Failed to reset database state: {e}")
        return JSONResponse({"error": f"Failed to reset: {str(e)}"}, status_code=500)


@mcp.custom_route("/debug/db-state", methods=["GET"])
async def debug_db_state(request: Request):
    """Debug endpoint to show database state (testing only)."""
    if os.getenv("ADCP_TESTING") != "true":
        return JSONResponse({"error": "Only available in testing mode"}, status_code=403)

    try:
        from src.core.database.database_session import get_db_session

        with get_db_session() as session:
            # Count all products
            product_stmt = select(ModelProduct)
            all_products = session.scalars(product_stmt).all()

            # Get ci-test-token principal
            principal_stmt = select(ModelPrincipal).filter_by(access_token="ci-test-token")
            principal = session.scalars(principal_stmt).first()

            principal_info = None
            tenant_info = None
            tenant_products: list[ModelProduct] = []

            if principal:
                principal_info = {
                    "principal_id": principal.principal_id,
                    "tenant_id": principal.tenant_id,
                }

                # Get tenant
                tenant_stmt = select(Tenant).filter_by(tenant_id=principal.tenant_id)
                tenant = session.scalars(tenant_stmt).first()
                if tenant:
                    tenant_info = {
                        "tenant_id": tenant.tenant_id,
                        "name": tenant.name,
                        "is_active": tenant.is_active,
                    }

                # Get products for that tenant
                tenant_product_stmt = select(ModelProduct).filter_by(tenant_id=principal.tenant_id)
                tenant_products = list(session.scalars(tenant_product_stmt).all())

            return JSONResponse(
                {
                    "total_products": len(all_products),
                    "principal": principal_info,
                    "tenant": tenant_info,
                    "tenant_products_count": len(tenant_products),
                    "tenant_product_ids": [p.product_id for p in tenant_products],
                }
            )
    except Exception as e:
        logger.error(f"Debug endpoint error: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/debug/tenant", methods=["GET"])
async def debug_tenant(request: Request):
    """Debug endpoint to check tenant detection from headers."""
    headers = dict(request.headers)

    # Check for Apx-Incoming-Host header
    apx_host = headers.get("apx-incoming-host") or headers.get("Apx-Incoming-Host")
    host_header = headers.get("host") or headers.get("Host")

    # Resolve tenant using same logic as auth
    tenant_id = None
    tenant_name = None
    detection_method = None

    # Try Apx-Incoming-Host first
    if apx_host:
        tenant = get_tenant_by_virtual_host(apx_host)
        if tenant:
            tenant_id = tenant.get("tenant_id")
            tenant_name = tenant.get("name")
            detection_method = "apx-incoming-host"

    # Try Host header subdomain
    if not tenant_id and host_header:
        subdomain = host_header.split(".")[0] if "." in host_header else None
        if subdomain and subdomain not in ["localhost", "adcp-sales-agent", "www", "sales-agent"]:
            tenant_id = subdomain
            detection_method = "host-subdomain"

    response_data = {
        "tenant_id": tenant_id,
        "tenant_name": tenant_name,
        "detection_method": detection_method,
        "apx_incoming_host": apx_host,
        "host": host_header,
    }

    # Add X-Tenant-Id header to response
    response = JSONResponse(response_data)
    if tenant_id:
        response.headers["X-Tenant-Id"] = tenant_id

    return response


@mcp.custom_route("/debug/root", methods=["GET"])
async def debug_root(request: Request):
    """Debug endpoint to test root route logic without redirects."""
    headers = dict(request.headers)

    # Check for Apx-Incoming-Host header (Approximated.app virtual host)
    # Try both capitalized and lowercase versions since HTTP header names are case-insensitive
    apx_host = headers.get("apx-incoming-host") or headers.get("Apx-Incoming-Host")
    # Also check standard Host header for direct virtual hosts
    host_header = headers.get("host") or headers.get("Host")

    virtual_host = apx_host or host_header

    # Get tenant
    tenant = get_tenant_by_virtual_host(virtual_host) if virtual_host else None

    debug_info = {
        "all_headers": headers,
        "apx_host": apx_host,
        "host_header": host_header,
        "virtual_host": virtual_host,
        "tenant_found": tenant is not None,
        "tenant_id": tenant.get("tenant_id") if tenant else None,
        "tenant_name": tenant.get("name") if tenant else None,
    }

    # Also test landing page generation
    if tenant:
        try:
            html_content = generate_tenant_landing_page(tenant, virtual_host)
            debug_info["landing_page_generated"] = True
            debug_info["landing_page_length"] = len(html_content)
        except Exception as e:
            debug_info["landing_page_generated"] = False
            debug_info["landing_page_error"] = str(e)

    return JSONResponse(debug_info)


@mcp.custom_route("/debug/landing", methods=["GET"])
async def debug_landing(request: Request):
    """Debug endpoint to test landing page generation directly."""
    headers = dict(request.headers)

    # Same logic as root route
    apx_host = headers.get("apx-incoming-host") or headers.get("Apx-Incoming-Host")
    host_header = headers.get("host") or headers.get("Host")
    virtual_host = apx_host or host_header

    if virtual_host:
        tenant = get_tenant_by_virtual_host(virtual_host)
        if tenant:
            try:
                html_content = generate_tenant_landing_page(tenant, virtual_host)
                return HTMLResponse(content=html_content)
            except Exception as e:
                return JSONResponse({"error": f"Landing page generation failed: {e}"}, status_code=500)

    return JSONResponse({"error": "No tenant found"}, status_code=404)


@mcp.custom_route("/debug/root-logic", methods=["GET"])
async def debug_root_logic(request: Request):
    """Debug endpoint that exactly mimics the root route logic for testing."""
    headers = dict(request.headers)

    # Exact same logic as root route
    apx_host = headers.get("apx-incoming-host") or headers.get("Apx-Incoming-Host")
    host_header = headers.get("host") or headers.get("Host")
    virtual_host = apx_host or host_header

    debug_info = {"step": "initial", "virtual_host": virtual_host, "apx_host": apx_host, "host_header": host_header}

    if virtual_host:
        debug_info["step"] = "virtual_host_found"

        # First try to look up tenant by exact virtual host match
        tenant = get_tenant_by_virtual_host(virtual_host)
        debug_info["exact_tenant_lookup"] = tenant is not None

        # If no exact match, check for domain-based routing patterns
        if not tenant and ".sales-agent.scope3.com" in virtual_host and not virtual_host.startswith("admin."):
            debug_info["step"] = "subdomain_fallback"
            subdomain = virtual_host.split(".sales-agent.scope3.com")[0]
            debug_info["extracted_subdomain"] = subdomain

            # This is the fallback logic we don't need for test-agent
            try:
                with get_db_session() as db_session:
                    stmt = select(Tenant).filter_by(subdomain=subdomain, is_active=True)
                    tenant_obj = db_session.scalars(stmt).first()
                    if tenant_obj:
                        debug_info["subdomain_tenant_found"] = True
                        # Build tenant dict...
                    else:
                        debug_info["subdomain_tenant_found"] = False
            except Exception as e:
                debug_info["subdomain_error"] = str(e)

        if tenant:
            debug_info["step"] = "tenant_found"
            debug_info["tenant_id"] = tenant.get("tenant_id")
            debug_info["tenant_name"] = tenant.get("name")

            # Try landing page generation
            try:
                html_content = generate_tenant_landing_page(tenant, virtual_host)
                debug_info["step"] = "landing_page_success"
                debug_info["landing_page_length"] = len(html_content)
                debug_info["would_return"] = "HTMLResponse"
            except Exception as e:
                debug_info["step"] = "landing_page_error"
                debug_info["error"] = str(e)
                debug_info["would_return"] = "fallback HTMLResponse"
        else:
            debug_info["step"] = "no_tenant_found"
            debug_info["would_return"] = "redirect to /admin/"
    else:
        debug_info["step"] = "no_virtual_host"
        debug_info["would_return"] = "redirect to /admin/"

    return JSONResponse(debug_info)


@mcp.custom_route("/health/config", methods=["GET"])
async def health_config(request: Request):
    """Configuration health check endpoint."""
    try:
        from src.core.startup import validate_startup_requirements

        validate_startup_requirements()
        return JSONResponse(
            {
                "status": "healthy",
                "service": "mcp",
                "component": "configuration",
                "message": "All configuration validation passed",
            }
        )
    except Exception as e:
        return JSONResponse(
            {"status": "unhealthy", "service": "mcp", "component": "configuration", "error": str(e)}, status_code=500
        )


# Add admin UI routes when running unified
unified_mode = os.environ.get("ADCP_UNIFIED_MODE")
logger.info(f"STARTUP: ADCP_UNIFIED_MODE = '{unified_mode}' (type: {type(unified_mode)})")
if unified_mode:
    from fastapi.middleware.wsgi import WSGIMiddleware
    from fastapi.responses import HTMLResponse, RedirectResponse

    from src.admin.app import create_app

    # Create Flask app and get the app instance
    flask_admin_app, _ = create_app()

    # Create WSGI middleware for Flask app
    admin_wsgi = WSGIMiddleware(flask_admin_app)

    logger.info("STARTUP: Registering unified mode routes...")

    logger.info("STARTUP: ADCP_UNIFIED_MODE enabled, registering routes...")

    async def handle_landing_page(request: Request):
        """Common landing page logic for both root and /landing routes."""
        headers = dict(request.headers)
        apx_host = headers.get("apx-incoming-host") or headers.get("Apx-Incoming-Host")

        # Check if this is an external domain request
        if apx_host and apx_host.endswith(".adcontextprotocol.org"):
            # Look up tenant by virtual host
            tenant = get_tenant_by_virtual_host(apx_host)

            if tenant:
                # Generate tenant landing page
                try:
                    html_content = generate_tenant_landing_page(tenant, apx_host)
                    return HTMLResponse(content=html_content)
                except Exception as e:
                    logger.error(f"Error generating landing page: {e}", exc_info=True)
                    return HTMLResponse(
                        content=f"""
                    <html>
                    <body>
                    <h1>Welcome to {tenant.get('name', 'AdCP Sales Agent')}</h1>
                    <p>This is a sales agent for advertising inventory.</p>
                    <p>Domain: {apx_host}</p>
                    </body>
                    </html>
                    """
                    )

        # Check if this is a subdomain request
        if apx_host and ".sales-agent.scope3.com" in apx_host:
            # Extract subdomain from apx_host
            subdomain = apx_host.split(".sales-agent.scope3.com")[0]

            # Look up tenant by subdomain
            try:
                with get_db_session() as db_session:
                    stmt = select(Tenant).filter_by(subdomain=subdomain, is_active=True)
                    tenant_obj = db_session.scalars(stmt).first()
                    if tenant_obj:
                        tenant = {
                            "tenant_id": tenant_obj.tenant_id,
                            "name": tenant_obj.name,
                            "subdomain": tenant_obj.subdomain,
                            "virtual_host": tenant_obj.virtual_host,
                        }
                        # Generate tenant landing page for subdomain
                        try:
                            html_content = generate_tenant_landing_page(tenant, apx_host)
                            return HTMLResponse(content=html_content)
                        except Exception as e:
                            logger.error(f"Error generating subdomain landing page: {e}", exc_info=True)
                            return HTMLResponse(
                                content=f"""
                            <html>
                            <body>
                            <h1>Welcome to {tenant.get('name', 'AdCP Sales Agent')}</h1>
                            <p>Subdomain: {apx_host}</p>
                            </body>
                            </html>
                            """
                            )
            except Exception as e:
                logger.error(f"Error looking up subdomain {subdomain}: {e}")

        # Fallback for unrecognized domains
        return HTMLResponse(
            content=f"""
        <html>
        <body>
        <h1>🎉 LANDING PAGE WORKING!</h1>
        <p>Domain: {apx_host}</p>
        <p>Success! The landing page is working.</p>
        </body>
        </html>
        """
        )

    # Task Management Tools (for HITL)

    @mcp.tool
    def list_tasks(
        status: str = None,
        object_type: str = None,
        object_id: str = None,
        limit: int = 20,
        offset: int = 0,
        context: Context = None,
    ) -> dict:
        """List workflow tasks with filtering options.

        Args:
            status: Filter by task status ("pending", "in_progress", "completed", "failed", "requires_approval")
            object_type: Filter by object type ("media_buy", "creative", "product")
            object_id: Filter by specific object ID
            limit: Maximum number of tasks to return (default: 20)
            offset: Number of tasks to skip (default: 0)
            context: MCP context (automatically provided)

        Returns:
            Dict containing tasks list and pagination info
        """

        # Get tenant and principal info
        tenant = get_current_tenant()
        principal_id = _get_principal_id_from_context(context)

        with get_db_session() as session:
            # Base query for workflow steps in this tenant
            stmt = select(WorkflowStep).join(Context).where(Context.tenant_id == tenant["tenant_id"])

            # Apply status filter
            if status:
                stmt = stmt.where(WorkflowStep.status == status)

            # Apply object type/ID filters
            if object_type and object_id:
                stmt = stmt.join(ObjectWorkflowMapping).where(
                    ObjectWorkflowMapping.object_type == object_type, ObjectWorkflowMapping.object_id == object_id
                )
            elif object_type:
                stmt = stmt.join(ObjectWorkflowMapping).where(ObjectWorkflowMapping.object_type == object_type)

            # Get total count before pagination
            from sqlalchemy import func

            total = session.scalar(select(func.count()).select_from(stmt.subquery()))

            # Apply pagination and ordering
            tasks = session.scalars(stmt.order_by(WorkflowStep.created_at.desc()).offset(offset).limit(limit)).all()

            # Format tasks for response
            formatted_tasks = []
            for task in tasks:
                # Get associated objects
                stmt = select(ObjectWorkflowMapping).filter_by(step_id=task.step_id)
                mappings = session.scalars(stmt).all()

                formatted_task = {
                    "task_id": task.step_id,
                    "status": task.status,
                    "type": task.step_type,
                    "tool_name": task.tool_name,
                    "owner": task.owner,
                    "created_at": task.created_at.isoformat() if task.created_at else None,
                    "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                    "context_id": task.context_id,
                    "associated_objects": [
                        {"type": m.object_type, "id": m.object_id, "action": m.action} for m in mappings
                    ],
                }

                # Add error message if failed
                if task.status == "failed" and task.error:
                    formatted_task["error_message"] = task.error

                # Add basic request info if available
                if task.request_data:
                    if isinstance(task.request_data, dict):
                        formatted_task["summary"] = {
                            "operation": task.request_data.get("operation"),
                            "media_buy_id": task.request_data.get("media_buy_id"),
                            "po_number": (
                                task.request_data.get("request", {}).get("po_number")
                                if task.request_data.get("request")
                                else None
                            ),
                        }

                formatted_tasks.append(formatted_task)

            return {
                "tasks": formatted_tasks,
                "total": total,
                "offset": offset,
                "limit": limit,
                "has_more": offset + limit < total,
            }

    @mcp.tool
    def get_task(task_id: str, context: Context = None) -> dict:
        """Get detailed information about a specific task.

        Args:
            task_id: The unique task/workflow step ID
            context: MCP context (automatically provided)

        Returns:
            Dict containing complete task details
        """

        # Get tenant info
        tenant = get_current_tenant()
        principal_id = _get_principal_id_from_context(context)

        with get_db_session() as session:
            # Find the task in this tenant
            stmt = (
                select(WorkflowStep)
                .join(Context)
                .where(WorkflowStep.step_id == task_id, Context.tenant_id == tenant["tenant_id"])
            )
            task = session.scalars(stmt).first()

            if not task:
                raise ValueError(f"Task {task_id} not found")

            # Get associated objects
            stmt = select(ObjectWorkflowMapping).filter_by(step_id=task_id)
            mappings = session.scalars(stmt).all()

            # Build detailed response
            task_detail = {
                "task_id": task.step_id,
                "context_id": task.context_id,
                "status": task.status,
                "type": task.step_type,
                "tool_name": task.tool_name,
                "owner": task.owner,
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                "request_data": task.request_data,
                "response_data": task.response_data,
                "error_message": task.error,
                "associated_objects": [
                    {
                        "type": m.object_type,
                        "id": m.object_id,
                        "action": m.action,
                        "created_at": m.created_at.isoformat(),
                    }
                    for m in mappings
                ],
            }

            return task_detail

    @mcp.tool
    def complete_task(
        task_id: str,
        status: str = "completed",
        response_data: dict = None,
        error_message: str = None,
        context: Context = None,
    ) -> dict:
        """Complete a pending task (simulates human approval or async completion).

        Args:
            task_id: The unique task/workflow step ID
            status: New status ("completed" or "failed")
            response_data: Optional response data for completed tasks
            error_message: Error message if status is "failed"
            context: MCP context (automatically provided)

        Returns:
            Dict containing task completion status
        """

        # Get tenant info
        tenant = get_current_tenant()
        principal_id = _get_principal_id_from_context(context)

        if status not in ["completed", "failed"]:
            raise ValueError(f"Invalid status '{status}'. Must be 'completed' or 'failed'")

        with get_db_session() as session:
            # Find the task in this tenant
            stmt = (
                select(WorkflowStep)
                .join(Context)
                .where(WorkflowStep.step_id == task_id, Context.tenant_id == tenant["tenant_id"])
            )
            task = session.scalars(stmt).first()

            if not task:
                raise ValueError(f"Task {task_id} not found")

            if task.status not in ["pending", "in_progress", "requires_approval"]:
                raise ValueError(f"Task {task_id} is already {task.status} and cannot be completed")

            # Update task status
            task.status = status
            task.updated_at = datetime.now(UTC)

            if status == "completed":
                task.response_data = response_data or {"manually_completed": True, "completed_by": principal_id}
                task.error = None
            else:  # failed
                task.error = error_message or "Task marked as failed manually"
                if response_data:
                    task.response_data = response_data

            session.commit()

            # Log the completion
            audit_logger = get_audit_logger("task_management", tenant["tenant_id"])
            audit_logger.log_operation(
                operation="complete_task",
                principal_name="Manual Completion",
                principal_id=principal_id,
                adapter_id="system",
                success=True,
                details={
                    "task_id": task_id,
                    "new_status": status,
                    "original_status": "pending",  # We know it was pending/in_progress
                    "task_type": task.step_type,
                },
            )

            return {
                "task_id": task_id,
                "status": status,
                "message": f"Task {task_id} marked as {status}",
                "completed_at": task.updated_at.isoformat(),
                "completed_by": principal_id,
            }

    @mcp.custom_route("/", methods=["GET"])
    async def root(request: Request):
        """Root route handler for all domains."""
        return await handle_landing_page(request)

    @mcp.custom_route("/landing", methods=["GET"])
    async def landing_page(request: Request):
        """Landing page route for external domains."""
        return await handle_landing_page(request)

    logger.info("STARTUP: Registered root route")

    @mcp.custom_route(
        "/admin/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
    )
    async def admin_handler(request: Request, path: str = ""):
        """Handle admin UI requests."""
        # Forward to Flask app
        scope = request.scope.copy()
        scope["path"] = f"/{path}" if path else "/"

        receive = request.receive
        send = request._send

        await admin_wsgi(scope, receive, send)

    @mcp.custom_route(
        "/tenant/{tenant_id}/admin/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
    )
    async def tenant_admin_handler(request: Request, tenant_id: str, path: str = ""):
        """Handle tenant-specific admin requests."""
        # Forward to Flask app with tenant context
        scope = request.scope.copy()
        scope["path"] = f"/tenant/{tenant_id}/{path}" if path else f"/tenant/{tenant_id}"

        receive = request.receive
        send = request._send

        await admin_wsgi(scope, receive, send)

    @mcp.custom_route("/tenant/{tenant_id}", methods=["GET"])
    async def tenant_root(request: Request, tenant_id: str):
        """Redirect to tenant admin."""
        return RedirectResponse(url=f"/tenant/{tenant_id}/admin/")
