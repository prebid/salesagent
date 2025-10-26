"""Authentication functions for AdCP Sales Agent.

This module provides authentication and principal resolution functions used
by both MCP and A2A protocols.
"""

import logging

from fastmcp.server.context import Context
from fastmcp.server.dependencies import get_http_headers
from rich.console import Console
from sqlalchemy import select

from src.core.config_loader import (
    get_current_tenant,
    get_tenant_by_id,
    get_tenant_by_subdomain,
    get_tenant_by_virtual_host,
    set_current_tenant,
)
from src.core.database.database_session import get_db_session
from src.core.database.models import Principal as ModelPrincipal
from src.core.database.models import Tenant
from src.core.schemas import Principal

logger = logging.getLogger(__name__)
console = Console()


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
                    console.print(f"[red]Token not found in tenant '{tenant_id}'[/red]")
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


def get_push_notification_config_from_headers(headers: dict[str, str] | None) -> dict[str, any] | None:
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
    headers = None
    try:
        headers = get_http_headers(include_all=True)
    except Exception:
        pass  # Will try fallback below

    # If get_http_headers() returned empty dict or None, try context.meta fallback
    # This is necessary for sync tools where get_http_headers() may not work
    # CRITICAL: get_http_headers() returns {} for sync tools, so we need fallback even for empty dict
    if not headers:  # Handles both None and {}
        # Only try context fallbacks if context is not None
        if context is not None:
            if hasattr(context, "meta") and context.meta and "headers" in context.meta:
                headers = context.meta["headers"]
            # Try other possible attributes
            elif hasattr(context, "headers"):
                headers = context.headers
            elif hasattr(context, "_headers"):
                headers = context._headers

    # If still no headers dict available, return None
    if not headers:
        return (None, None)

    # Log all relevant headers for debugging
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
        apx_host = _get_header_case_insensitive(headers, "apx-incoming-host")

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


def get_principal_adapter_mapping(principal_id: str) -> dict[str, any]:
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
