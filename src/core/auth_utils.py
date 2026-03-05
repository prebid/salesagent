"""Authentication utilities for MCP server."""

import hmac
import logging

from sqlalchemy import select

from src.core.database.database_session import execute_with_retry
from src.core.database.models import Principal, Tenant

logger = logging.getLogger(__name__)


def get_principal_from_token(token: str, tenant_id: str | None = None) -> tuple[str | None, dict | None]:
    """Looks up a principal_id from the database using a token with retry logic.

    If tenant_id is provided, only looks in that specific tenant.
    If not provided, searches globally by token and returns the discovered tenant.

    Args:
        token: Authentication token
        tenant_id: Optional tenant ID to restrict search

    Returns:
        (principal_id, tenant_dict) tuple. tenant_dict is only populated when
        the tenant was discovered from a global token lookup (no tenant_id provided).
    """

    def _lookup_principal(session):
        if tenant_id:
            # If tenant_id specified, ONLY look in that tenant
            stmt = select(Principal).filter_by(access_token=token, tenant_id=tenant_id)
            principal = session.scalars(stmt).first()
            if principal:
                return principal.principal_id, None

            # Check if it's the admin token for this specific tenant
            tenant_stmt = select(Tenant).filter_by(tenant_id=tenant_id, is_active=True)
            tenant_obj = session.scalars(tenant_stmt).first()
            if tenant_obj and tenant_obj.admin_token and hmac.compare_digest(tenant_obj.admin_token, token):
                logger.debug("Token matches admin token for tenant '%s'", tenant_id)
                return f"{tenant_id}_admin", None

            return None, None
        else:
            # No tenant specified - search globally
            stmt = select(Principal).filter_by(access_token=token)
            principal = session.scalars(stmt).first()
            logger.debug(f"[AUTH] Looking up principal with token: {token[:20]}...")
            if principal:
                logger.info(f"[AUTH] Principal found: {principal.principal_id}, tenant_id={principal.tenant_id}")
                # Found principal - look up tenant to return
                stmt = select(Tenant).filter_by(tenant_id=principal.tenant_id, is_active=True)
                tenant = session.scalars(stmt).first()
                if tenant:
                    logger.info(f"[AUTH] Tenant found: {tenant.tenant_id}, is_active={tenant.is_active}")
                    from src.core.utils.tenant_utils import serialize_tenant_to_dict

                    tenant_dict = serialize_tenant_to_dict(tenant)
                    return principal.principal_id, tenant_dict
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

        return None, None

    try:
        return execute_with_retry(_lookup_principal)
    except Exception as e:
        logger.error(f"[AUTH] Database error during principal lookup: {e}", exc_info=True)
        return None, None
