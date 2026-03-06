"""Admin API authentication dependencies for FastAPI.

Two auth patterns:
- Platform API key: X-Tenant-Management-API-Key header for cross-tenant operations
- Tenant admin token: Authorization: Bearer <admin_token> for per-tenant operations
"""

from __future__ import annotations

import hmac
import logging

from fastapi import Request
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant, TenantManagementConfig
from src.core.exceptions import AdCPAuthenticationError, AdCPAuthorizationError, AdCPNotFoundError

logger = logging.getLogger(__name__)


def require_platform_api_key(request: Request) -> str:
    """Validate X-Tenant-Management-API-Key header against stored key.

    Returns the validated API key string.
    Raises AdCPAuthenticationError if missing or invalid.
    """
    api_key = request.headers.get("x-tenant-management-api-key")
    if not api_key:
        raise AdCPAuthenticationError("Missing X-Tenant-Management-API-Key header")

    with get_db_session() as session:
        # Check both config key names used by the existing Flask APIs
        for config_key in ("tenant_management_api_key", "api_key"):
            stmt = select(TenantManagementConfig).filter_by(config_key=config_key)
            config = session.scalars(stmt).first()
            if config and config.config_value and hmac.compare_digest(api_key, config.config_value):
                return api_key

    raise AdCPAuthenticationError("Invalid API key")


def require_tenant_admin(request: Request, tenant_id: str) -> Tenant:
    """Validate Bearer token matches tenant.admin_token for the given tenant_id.

    Returns the Tenant ORM object.
    Raises AdCPAuthenticationError if token missing, AdCPNotFoundError if tenant
    doesn't exist, AdCPAuthorizationError if token doesn't match.
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise AdCPAuthenticationError("Missing or invalid Authorization: Bearer header")

    token = auth_header[7:].strip()
    if not token:
        raise AdCPAuthenticationError("Empty Bearer token")

    with get_db_session() as session:
        stmt = select(Tenant).filter_by(tenant_id=tenant_id)
        tenant = session.scalars(stmt).first()

        if not tenant:
            raise AdCPNotFoundError(f"Tenant '{tenant_id}' not found")

        if not tenant.admin_token or not hmac.compare_digest(token, tenant.admin_token):
            raise AdCPAuthorizationError("Invalid admin token for this tenant")

        # Expunge so it's usable outside the session
        session.expunge(tenant)
        return tenant
