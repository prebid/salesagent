"""Unified identity type for transport-agnostic business logic.

ResolvedIdentity is created at each transport boundary (MCP, A2A, REST) and
passed to _impl functions instead of transport-specific Context types.

This eliminates isinstance checks and auth extraction inside business logic.
"""

import logging
from typing import Any, Literal

from pydantic import BaseModel

from src.core.config_loader import (
    get_tenant_by_id,
    get_tenant_by_subdomain,
    get_tenant_by_virtual_host,
)
from src.core.testing_hooks import AdCPTestContext

logger = logging.getLogger(__name__)


class ResolvedIdentity(BaseModel, frozen=True):
    """Transport-agnostic identity resolved at the boundary.

    Created by resolve_identity() before any _impl function is called.
    Immutable after creation — identity should not change during request processing.
    """

    principal_id: str | None = None
    tenant_id: str | None = None
    tenant: Any = None  # TenantContext | dict[str, Any] | None (transitional)
    auth_token: str | None = None
    protocol: Literal["mcp", "a2a", "rest"] = "mcp"
    testing_context: AdCPTestContext | None = None

    @property
    def is_authenticated(self) -> bool:
        """Check if this identity has a resolved principal."""
        return self.principal_id is not None and self.principal_id != ""


from src.core.http_utils import get_header_case_insensitive as _get_header_case_insensitive


def _extract_auth_token(headers: dict) -> tuple[str | None, str | None]:
    """Extract auth token from headers.

    Checks x-adcp-auth first, then Authorization: Bearer.

    Returns:
        (token, source) tuple — source is "x-adcp-auth" or "Authorization: Bearer"
    """
    token = _get_header_case_insensitive(headers, "x-adcp-auth")
    if token:
        return token, "x-adcp-auth"

    authorization = _get_header_case_insensitive(headers, "Authorization")
    if authorization and authorization.lower().startswith("bearer "):
        potential_token = authorization[7:].strip()
        if potential_token:
            return potential_token, "Authorization: Bearer"

    return None, None


def _detect_tenant(headers: dict) -> tuple[str | None, dict | None]:
    """Detect tenant from request headers using 4-strategy resolution.

    Strategy order:
    1. Host header → virtual host lookup, then subdomain extraction
    2. x-adcp-tenant header → subdomain lookup, then direct tenant_id
    3. Apx-Incoming-Host header → virtual host lookup
    4. localhost fallback → "default" tenant

    Returns:
        (tenant_id, tenant_dict) tuple
    """
    tenant_id = None
    tenant_context = None

    # 1. Host header: try virtual host FIRST, then subdomain
    host = _get_header_case_insensitive(headers, "host") or ""

    tenant_context = get_tenant_by_virtual_host(host)
    if tenant_context:
        tenant_id = tenant_context["tenant_id"]
    else:
        subdomain = host.split(".")[0] if "." in host else None
        if subdomain and subdomain not in ["localhost", "adcp-sales-agent", "www", "admin"]:
            tenant_context = get_tenant_by_subdomain(subdomain)
            if tenant_context:
                tenant_id = tenant_context["tenant_id"]

    # 2. x-adcp-tenant header (nginx path-based routing)
    if not tenant_id:
        tenant_hint = _get_header_case_insensitive(headers, "x-adcp-tenant")
        if tenant_hint:
            tenant_context = get_tenant_by_subdomain(tenant_hint)
            if tenant_context:
                tenant_id = tenant_context["tenant_id"]
            else:
                tenant_id = tenant_hint
                tenant_context = get_tenant_by_id(tenant_hint)

    # 3. Apx-Incoming-Host header (Approximated.app virtual hosts)
    if not tenant_id:
        apx_host = _get_header_case_insensitive(headers, "apx-incoming-host")
        if apx_host:
            tenant_context = get_tenant_by_virtual_host(apx_host)
            if tenant_context:
                tenant_id = tenant_context["tenant_id"]

    # 4. Localhost fallback → "default" tenant
    if not tenant_id:
        hostname = host.split(":")[0]
        if hostname in ["localhost", "127.0.0.1", "localhost.localdomain"]:
            tenant_context = get_tenant_by_subdomain("default")
            if tenant_context:
                tenant_id = tenant_context["tenant_id"]

    return tenant_id, tenant_context


def resolve_identity(
    headers: dict,
    auth_token: str | None = None,
    protocol: Literal["mcp", "a2a", "rest"] = "mcp",
    require_valid_token: bool = True,
    testing_context: AdCPTestContext | None = None,
) -> ResolvedIdentity:
    """Resolve identity from request headers at the transport boundary.

    This is the single entry point for identity resolution, called by each
    transport boundary (MCP wrapper, A2A handler, REST middleware) before
    invoking _impl functions.

    Args:
        headers: HTTP request headers dict
        auth_token: Pre-extracted auth token (if already parsed by transport).
                   If None, will extract from headers.
        protocol: Which transport is calling ("mcp", "a2a", "rest")
        require_valid_token: If True, raises AdCPAuthenticationError for invalid tokens.
                           If False, treats invalid tokens like missing (for discovery).
        testing_context: Pre-extracted testing context, if available.

    Returns:
        ResolvedIdentity with all fields resolved

    Raises:
        AdCPAuthenticationError: If token is present but invalid and require_valid_token=True
    """
    # Import here to avoid circular dependency (auth_utils imports from database)
    from src.core.auth_utils import get_principal_from_token

    # Step 1: Detect tenant from headers
    tenant_id, tenant_context = _detect_tenant(headers)

    # Step 2: Extract auth token if not pre-provided
    if auth_token is None:
        auth_token, _ = _extract_auth_token(headers)

    # Step 3: Validate token → principal_id (and discover tenant from token if needed)
    principal_id = None
    if auth_token:
        principal_id, token_tenant = get_principal_from_token(auth_token, tenant_id)

        if principal_id is None:
            if require_valid_token:
                from src.core.exceptions import AdCPAuthenticationError

                raise AdCPAuthenticationError(
                    f"Authentication token is invalid for tenant '{tenant_id or 'any'}'. "
                    f"The token may be expired, revoked, or associated with a different tenant.",
                    details={"error_code": "INVALID_AUTH_TOKEN"},
                )
            # For discovery endpoints, continue without auth
        elif not tenant_context and token_tenant:
            # Tenant discovered from token lookup (no headers matched)
            tenant_context = token_tenant
            tenant_id = token_tenant.get("tenant_id", tenant_id)

    # Wrap raw dict in TenantContext if possible (both paths produce typed model)
    tenant_model = tenant_context
    if isinstance(tenant_context, dict) and "tenant_id" in tenant_context:
        from src.core.tenant_context import TenantContext

        try:
            tenant_model = TenantContext.from_dict(tenant_context)  # type: ignore[assignment]  # TenantContext is a dict-compatible proxy
        except Exception:
            tenant_model = tenant_context  # Keep dict if model construction fails

    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant=tenant_model,
        auth_token=auth_token,
        protocol=protocol,
        testing_context=testing_context,
    )
