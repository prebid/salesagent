"""Authentication utilities for MCP server."""

import logging

from fastmcp.server import Context
from sqlalchemy import select

from src.core.config_loader import set_current_tenant
from src.core.database.database_session import execute_with_retry
from src.core.database.models import Principal, Tenant

logger = logging.getLogger(__name__)


def get_principal_from_token(token: str, tenant_id: str | None = None) -> str | None:
    """Looks up a principal_id from the database using a token with retry logic.

    If tenant_id is provided, only looks in that specific tenant.
    If not provided, searches globally by token and sets the tenant context.

    Args:
        token: Authentication token
        tenant_id: Optional tenant ID to restrict search

    Returns:
        Principal ID if found, None otherwise
    """

    def _lookup_principal(session):
        if tenant_id:
            # If tenant_id specified, ONLY look in that tenant
            stmt = select(Principal).filter_by(access_token=token, tenant_id=tenant_id)
            principal = session.scalars(stmt).first()
            if principal:
                return principal.principal_id
        else:
            # No tenant specified - search globally
            stmt = select(Principal).filter_by(access_token=token)
            principal = session.scalars(stmt).first()
            logger.debug(f"[AUTH] Looking up principal with token: {token[:20]}...")
            if principal:
                logger.info(f"[AUTH] Principal found: {principal.principal_id}, tenant_id={principal.tenant_id}")
                # Found principal - set tenant context
                stmt = select(Tenant).filter_by(tenant_id=principal.tenant_id, is_active=True)
                tenant = session.scalars(stmt).first()
                if tenant:
                    logger.info(f"[AUTH] Tenant found: {tenant.tenant_id}, is_active={tenant.is_active}")
                    from src.core.utils.tenant_utils import serialize_tenant_to_dict

                    tenant_dict = serialize_tenant_to_dict(tenant)
                    set_current_tenant(tenant_dict)
                    return principal.principal_id
                else:
                    logger.error(
                        f"[AUTH] ERROR: Tenant NOT FOUND for tenant_id={principal.tenant_id} with is_active=True"
                    )
                    # Try without is_active filter to see if tenant exists but is_active is wrong
                    stmt_debug = select(Tenant).filter_by(tenant_id=principal.tenant_id)
                    tenant_debug = session.scalars(stmt_debug).first()
                    if tenant_debug:
                        logger.warning(f"[AUTH] DEBUG: Tenant EXISTS but is_active={tenant_debug.is_active}")
                    else:
                        logger.warning("[AUTH] DEBUG: Tenant does not exist at all")
            else:
                logger.error(f"[AUTH] ERROR: Principal NOT FOUND for token {token[:20]}...")

        return None

    try:
        return execute_with_retry(_lookup_principal)
    except Exception as e:
        logger.error(f"[AUTH] Database error during principal lookup: {e}", exc_info=True)
        return None


def get_principal_from_context(context: Context | None) -> str | None:
    """Extract principal ID from the FastMCP context using authentication headers.

    Accepts authentication via either:
    - x-adcp-auth: <token> (AdCP convention, preferred for MCP)
    - Authorization: Bearer <token> (standard HTTP, for compatibility with A2A clients)

    Args:
        context: FastMCP context object

    Returns:
        Principal ID if authenticated, None otherwise
    """
    if not context:
        return None

    try:
        # Extract token from headers
        token = None
        auth_source = None
        headers_found = {}

        if hasattr(context, "meta") and isinstance(context.meta, dict):
            headers_found = context.meta.get("headers", {})
            logger.debug(f"[AUTH] Headers from context.meta: {list(headers_found.keys())}")
        elif hasattr(context, "headers"):
            headers_found = context.headers
            logger.debug(f"[AUTH] Headers from context.headers: {list(headers_found.keys())}")
        else:
            logger.warning("[AUTH] No headers found in context!")
            return None

        # Try both authentication headers (prefer x-adcp-auth for MCP)
        for key, value in headers_found.items():
            if key.lower() == "x-adcp-auth":
                token = value
                auth_source = "x-adcp-auth"
                break  # Prefer x-adcp-auth
            elif key.lower() == "authorization":
                auth_header = value.strip()
                if auth_header.startswith("Bearer "):
                    token = auth_header[7:]  # Remove "Bearer " prefix
                    auth_source = "Authorization"
                    # Don't break - prefer x-adcp-auth if both present

        if not token:
            logger.debug(
                f"[AUTH] No authentication token found (checked x-adcp-auth and Authorization). Available headers: {list(headers_found.keys())}"
            )
            return None

        logger.debug(f"[AUTH] Found token from {auth_source}: {token[:20]}...")

        # Validate token and get principal ID
        return get_principal_from_token(token)

    except Exception as e:
        logger.error(f"[AUTH] Error extracting principal from context: {e}", exc_info=True)
        return None


def get_principal_object(principal_id: str) -> Principal | None:
    """Get the Principal object with platform mappings using retry logic.

    Args:
        principal_id: The principal ID to look up

    Returns:
        Principal object or None if not found
    """
    if not principal_id:
        return None

    def _get_principal_object(session):
        from src.core.schemas import Principal as PrincipalSchema

        # Query the database for the principal
        stmt = select(Principal).filter_by(principal_id=principal_id)
        db_principal = session.scalars(stmt).first()

        if db_principal:
            # Convert to Pydantic model
            return PrincipalSchema(
                principal_id=db_principal.principal_id,
                name=db_principal.name,
                platform_mappings=db_principal.platform_mappings or {},
            )

        return None

    try:
        return execute_with_retry(_get_principal_object)
    except Exception as e:
        logger.error(f"[AUTH] Database error during principal object lookup: {e}", exc_info=True)
        return None
