"""Authentication functions for Prebid Sales Agent.

This module provides authentication and principal resolution functions used
by both MCP and A2A protocols.
"""

import logging
import os
from typing import TYPE_CHECKING, Any, Union

from fastmcp.server.context import Context

if TYPE_CHECKING:
    from src.core.tool_context import ToolContext
from fastmcp.server.dependencies import get_http_headers
from sqlalchemy import select

from src.core.auth_utils import get_principal_from_token
from src.core.config_loader import (
    get_current_tenant,
    get_tenant_by_id,
    get_tenant_by_subdomain,
    get_tenant_by_virtual_host,
    set_current_tenant,
)
from src.core.database.database_session import get_db_session
from src.core.database.models import Principal as ModelPrincipal
from src.core.schemas import Principal

logger = logging.getLogger(__name__)

# Enable verbose auth logging only in development
_VERBOSE_AUTH_LOG = not (os.environ.get("FLY_APP_NAME") or os.environ.get("PRODUCTION"))


from src.core.http_utils import get_header_case_insensitive as _get_header_case_insensitive


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
    context: Union[Context, "ToolContext", None], require_valid_token: bool = True
) -> tuple[str | None, dict | None]:
    """Extract principal ID and tenant context from the FastMCP context or ToolContext.

    For FastMCP Context: Uses get_http_headers() to extract from x-adcp-auth header.
    For ToolContext: Directly returns principal_id and tenant_id from the context object.

    Args:
        context: FastMCP Context, ToolContext, or None
        require_valid_token: If True (default), raises error for invalid tokens.
                           If False, treats invalid tokens like missing tokens (for discovery endpoints).

    Returns:
        tuple[principal_id, tenant_context]: Principal ID and tenant dict, or (None, tenant) if no/invalid auth

    Note: Returns tenant context explicitly because ContextVar changes in sync functions
    don't reliably propagate to async callers (Python ContextVar + async/sync boundary issue).
    The caller MUST call set_current_tenant(tenant_context) in their own context.
    """
    # Import here to avoid circular dependency
    from src.core.tool_context import ToolContext

    # Handle ToolContext directly (already has principal_id and tenant_id)
    if isinstance(context, ToolContext):
        return (context.principal_id, {"tenant_id": context.tenant_id})

    # Get headers using the recommended FastMCP approach
    # NOTE: get_http_headers() works via context vars, so it can work even when context=None
    # This allows unauthenticated public discovery endpoints to detect tenant from headers
    # CRITICAL: Use include_all=True to get Host header (excluded by default)
    headers = None
    try:
        headers = get_http_headers(include_all=True)
    except Exception:
        logger.debug("get_http_headers() unavailable, trying fallback", exc_info=True)

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

    # Extract headers for tenant detection
    host_header = _get_header_case_insensitive(headers, "host")
    apx_host_header = _get_header_case_insensitive(headers, "apx-incoming-host")
    tenant_header = _get_header_case_insensitive(headers, "x-adcp-tenant")

    if _VERBOSE_AUTH_LOG:
        logger.info(
            "Tenant detection - Host: %s, Apx-Host: %s, x-adcp-tenant: %s", host_header, apx_host_header, tenant_header
        )

    # ALWAYS resolve tenant from headers first (even without auth for public discovery endpoints)
    requested_tenant_id = None
    tenant_context = None
    detection_method = None

    # 1. Check host header - try virtual host FIRST, then fall back to subdomain
    if not requested_tenant_id:
        host = _get_header_case_insensitive(headers, "host") or ""
        apx_host = _get_header_case_insensitive(headers, "apx-incoming-host")

        # CRITICAL: Try virtual host lookup FIRST before extracting subdomain
        # This prevents issues where a subdomain happens to match a virtual host
        tenant_context = get_tenant_by_virtual_host(host)
        if tenant_context:
            requested_tenant_id = tenant_context["tenant_id"]
            detection_method = "host header (virtual host)"
            set_current_tenant(tenant_context)
            if _VERBOSE_AUTH_LOG:
                logger.info("Tenant detected from Host header: %s -> %s", host, requested_tenant_id)
        else:
            # Fallback to subdomain extraction if virtual host lookup failed
            subdomain = host.split(".")[0] if "." in host else None
            if subdomain and subdomain not in ["localhost", "adcp-sales-agent", "www", "admin"]:
                tenant_context = get_tenant_by_subdomain(subdomain)
                if tenant_context:
                    requested_tenant_id = tenant_context["tenant_id"]
                    detection_method = "subdomain"
                    set_current_tenant(tenant_context)
                    if _VERBOSE_AUTH_LOG:
                        logger.info("Tenant detected from subdomain: %s -> %s", subdomain, requested_tenant_id)

    # 2. Check x-adcp-tenant header (set by nginx for path-based routing)
    if not requested_tenant_id:
        tenant_hint = _get_header_case_insensitive(headers, "x-adcp-tenant")
        if tenant_hint:
            # Try to look up by subdomain first (most common case)
            tenant_context = get_tenant_by_subdomain(tenant_hint)
            if tenant_context:
                requested_tenant_id = tenant_context["tenant_id"]
                detection_method = "x-adcp-tenant header (subdomain lookup)"
                set_current_tenant(tenant_context)
                if _VERBOSE_AUTH_LOG:
                    logger.info("Tenant detected from x-adcp-tenant: %s -> %s", tenant_hint, requested_tenant_id)
            else:
                # Fallback: assume it's already a tenant_id
                requested_tenant_id = tenant_hint
                detection_method = "x-adcp-tenant header (direct)"
                tenant_context = get_tenant_by_id(tenant_hint)
                if tenant_context:
                    set_current_tenant(tenant_context)

    # 3. Check Apx-Incoming-Host header (for Approximated.app virtual hosts)
    if not requested_tenant_id:
        apx_host = _get_header_case_insensitive(headers, "apx-incoming-host")
        if apx_host:
            tenant_context = get_tenant_by_virtual_host(apx_host)
            if tenant_context:
                requested_tenant_id = tenant_context["tenant_id"]
                detection_method = "apx-incoming-host"
                set_current_tenant(tenant_context)
                if _VERBOSE_AUTH_LOG:
                    logger.info("Tenant detected from Apx-Incoming-Host: %s -> %s", apx_host, requested_tenant_id)

    # 4. Fallback for localhost in development: use "default" tenant
    if not requested_tenant_id:
        host = _get_header_case_insensitive(headers, "host") or ""
        hostname = host.split(":")[0]
        if hostname in ["localhost", "127.0.0.1", "localhost.localdomain"]:
            tenant_context = get_tenant_by_subdomain("default")
            if tenant_context:
                requested_tenant_id = tenant_context["tenant_id"]
                detection_method = "localhost fallback (default tenant)"
                set_current_tenant(tenant_context)

    if _VERBOSE_AUTH_LOG:
        if requested_tenant_id:
            logger.info("Final tenant_id: %s (via %s)", requested_tenant_id, detection_method)
        else:
            logger.debug("No tenant detected from headers")

    # NOW check for auth token (after tenant resolution)
    # Accept either x-adcp-auth (preferred) or Authorization: Bearer (standard HTTP/MCP)
    # This ensures compatibility with MCP clients that only support Authorization header
    auth_token = _get_header_case_insensitive(headers, "x-adcp-auth")
    auth_source = "x-adcp-auth" if auth_token else None

    # If x-adcp-auth not present, try Authorization: Bearer (for Anthropic, standard MCP clients)
    if not auth_token:
        authorization_header = _get_header_case_insensitive(headers, "Authorization")
        if authorization_header:
            # RFC 6750 specifies "Bearer" but accept case-insensitive for compatibility
            auth_header_lower = authorization_header.lower()
            if auth_header_lower.startswith("bearer "):
                potential_token = authorization_header[7:].strip()  # Remove "Bearer " prefix and whitespace
                if potential_token:  # Only use if there's actually a token after the prefix
                    auth_token = potential_token
                    auth_source = "Authorization: Bearer"

    if _VERBOSE_AUTH_LOG and auth_source:
        logger.info("Auth token found via: %s", auth_source)

    if not auth_token:
        logger.debug("No auth token found - OK for discovery endpoints")
        return (None, tenant_context)

    # Validate token and get principal
    # If requested_tenant_id is set: validate token belongs to that specific tenant
    # If requested_tenant_id is None: do global lookup and set tenant context from token
    if not requested_tenant_id:
        # No tenant detected from headers - use global token lookup
        # SECURITY NOTE: This is safe because get_principal_from_token() will:
        # 1. Look up the token globally
        # 2. Find which tenant it belongs to
        # 3. Return (principal_id, tenant_dict) — caller sets context
        # 4. Return principal_id only if token is valid for that tenant
        logger.debug("Using global token lookup (finds tenant from token)")
        detection_method = "global token lookup"

    principal_id, token_tenant = get_principal_from_token(auth_token, requested_tenant_id)

    # If token was provided but invalid, raise an error (unless require_valid_token=False for discovery)
    # This distinguishes between "no auth" (OK) and "bad auth" (error or warning)
    if principal_id is None:
        if require_valid_token:
            from src.core.exceptions import AdCPAuthenticationError

            raise AdCPAuthenticationError(
                f"Authentication token is invalid for tenant '{requested_tenant_id or 'any'}'. "
                f"The token may be expired, revoked, or associated with a different tenant.",
                details={"error_code": "INVALID_AUTH_TOKEN"},
            )
        else:
            # For discovery endpoints, treat invalid token like missing token
            logger.debug(
                "Invalid token for tenant '%s' - continuing without auth (discovery endpoint)",
                requested_tenant_id or "any",
            )
            return (None, tenant_context)

    # If tenant_context wasn't set by header detection, use tenant discovered from token
    if not tenant_context and token_tenant:
        tenant_context = token_tenant

    # Return both principal_id and tenant_context explicitly
    # Caller MUST call set_current_tenant(tenant_context) in their async context
    return (principal_id, tenant_context)


def get_principal_adapter_mapping(principal_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    """Get the platform mappings for a principal."""
    if tenant_id is None:
        tenant = get_current_tenant()
        tenant_id = tenant["tenant_id"]
    with get_db_session() as session:
        stmt = select(ModelPrincipal).filter_by(principal_id=principal_id, tenant_id=tenant_id)
        principal = session.scalars(stmt).first()
        return principal.platform_mappings if principal else {}


def get_principal_object(principal_id: str, tenant_id: str | None = None) -> Principal | None:
    """Get a Principal object for the given principal_id."""
    if tenant_id is None:
        tenant = get_current_tenant()
        tenant_id = tenant["tenant_id"]
    with get_db_session() as session:
        stmt = select(ModelPrincipal).filter_by(principal_id=principal_id, tenant_id=tenant_id)
        principal = session.scalars(stmt).first()

        if principal:
            return Principal(
                principal_id=principal.principal_id,
                name=principal.name,
                platform_mappings=principal.platform_mappings,
            )
    return None


def get_adapter_principal_id(principal_id: str, adapter: str, tenant_id: str | None = None) -> str | None:
    """Get the adapter-specific ID for a principal."""
    mappings = get_principal_adapter_mapping(principal_id, tenant_id=tenant_id)

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
