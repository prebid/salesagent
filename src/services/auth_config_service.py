"""Authentication configuration service.

Manages per-tenant OIDC configuration stored in the database.
"""

import logging
import os
from datetime import UTC, datetime

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant, TenantAuthConfig
from src.core.domain_config import get_sales_agent_domain, get_sales_agent_url

logger = logging.getLogger(__name__)


# Well-known OIDC discovery URLs
OIDC_PROVIDERS = {
    "google": "https://accounts.google.com/.well-known/openid-configuration",
    "microsoft": "https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration",
}

# Well-known OIDC logout URLs
OIDC_LOGOUT_URLS = {
    "google": "https://accounts.google.com/Logout",
    "microsoft": "https://login.microsoftonline.com/common/oauth2/v2.0/logout",
}


def get_tenant_auth_config(tenant_id: str) -> TenantAuthConfig | None:
    """Get the authentication configuration for a tenant.

    Args:
        tenant_id: The tenant ID

    Returns:
        TenantAuthConfig or None if not configured
    """
    with get_db_session() as session:
        return session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant_id)).first()


def get_or_create_auth_config(tenant_id: str) -> TenantAuthConfig:
    """Get or create authentication configuration for a tenant.

    Args:
        tenant_id: The tenant ID

    Returns:
        TenantAuthConfig (existing or newly created)
    """
    with get_db_session() as session:
        config = session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant_id)).first()

        if not config:
            config = TenantAuthConfig(
                tenant_id=tenant_id,
                oidc_enabled=False,
                created_at=datetime.now(UTC),
            )
            session.add(config)
            session.commit()
            session.refresh(config)

        return config


def save_oidc_config(
    tenant_id: str,
    provider: str,
    client_id: str,
    client_secret: str | None,
    discovery_url: str | None = None,
    scopes: str = "openid email profile",
    logout_url: str | None = None,
) -> TenantAuthConfig:
    """Save OIDC configuration for a tenant.

    Args:
        tenant_id: The tenant ID
        provider: Provider name (google, microsoft, custom)
        client_id: OAuth client ID
        client_secret: OAuth client secret (will be encrypted). If None/empty,
                      keeps existing secret.
        discovery_url: OIDC discovery URL (auto-set for known providers)
        scopes: OAuth scopes
        logout_url: Optional IdP logout URL for proper OIDC logout

    Returns:
        Updated TenantAuthConfig
    """
    # Resolve discovery URL for known providers
    if not discovery_url and provider in OIDC_PROVIDERS:
        discovery_url = OIDC_PROVIDERS[provider]

    if not discovery_url:
        raise ValueError("Discovery URL is required for custom OIDC providers")

    # Auto-set logout URL for known providers
    if not logout_url and provider in OIDC_LOGOUT_URLS:
        logout_url = OIDC_LOGOUT_URLS[provider]

    with get_db_session() as session:
        config = session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant_id)).first()

        if not config:
            config = TenantAuthConfig(
                tenant_id=tenant_id,
                created_at=datetime.now(UTC),
            )
            session.add(config)

        # Check if key settings changed (requires re-verification)
        settings_changed = (
            config.oidc_provider != provider
            or config.oidc_client_id != client_id
            or config.oidc_discovery_url != discovery_url
        )

        config.oidc_provider = provider
        config.oidc_client_id = client_id
        # Only update secret if a new one is provided
        if client_secret:
            config.oidc_client_secret = client_secret  # Uses setter for encryption
            settings_changed = True
        config.oidc_discovery_url = discovery_url
        config.oidc_scopes = scopes
        config.oidc_logout_url = logout_url
        config.updated_at = datetime.now(UTC)

        # Reset verification only if key settings changed
        if settings_changed:
            config.oidc_verified_at = None
            config.oidc_verified_redirect_uri = None

        session.commit()
        session.refresh(config)

        logger.info(f"Saved OIDC config for tenant {tenant_id}: provider={provider}")
        return config


def enable_oidc(tenant_id: str) -> bool:
    """Enable OIDC authentication for a tenant.

    Only succeeds if the config has been verified.

    Args:
        tenant_id: The tenant ID

    Returns:
        True if enabled, False if not verified
    """
    with get_db_session() as session:
        config = session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant_id)).first()

        if not config:
            logger.error(f"Cannot enable OIDC: no config for tenant {tenant_id}")
            return False

        if not is_oidc_config_valid(tenant_id):
            logger.error(f"Cannot enable OIDC: config not verified for tenant {tenant_id}")
            return False

        config.oidc_enabled = True
        config.updated_at = datetime.now(UTC)
        session.commit()

        logger.info(f"Enabled OIDC for tenant {tenant_id}")
        return True


def disable_oidc(tenant_id: str) -> bool:
    """Disable OIDC authentication for a tenant.

    Args:
        tenant_id: The tenant ID

    Returns:
        True if disabled
    """
    with get_db_session() as session:
        config = session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant_id)).first()

        if config:
            config.oidc_enabled = False
            config.updated_at = datetime.now(UTC)
            session.commit()
            logger.info(f"Disabled OIDC for tenant {tenant_id}")

        return True


def mark_oidc_verified(tenant_id: str, redirect_uri: str) -> None:
    """Mark OIDC configuration as verified.

    Called after a successful test OAuth flow.

    Args:
        tenant_id: The tenant ID
        redirect_uri: The redirect URI that was tested
    """
    with get_db_session() as session:
        config = session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant_id)).first()

        if config:
            config.oidc_verified_at = datetime.now(UTC)
            config.oidc_verified_redirect_uri = redirect_uri
            config.updated_at = datetime.now(UTC)
            session.commit()

            logger.info(f"Marked OIDC verified for tenant {tenant_id}")


def get_tenant_redirect_uri(tenant: Tenant) -> str:
    """Get the OAuth redirect URI for a tenant.

    The redirect URI is based on the tenant's domain configuration.

    Args:
        tenant: The Tenant object

    Returns:
        Full redirect URI
    """
    if tenant.virtual_host:
        # Custom domain takes highest priority
        base = f"https://{tenant.virtual_host}"
    elif tenant.subdomain and get_sales_agent_domain():
        # Subdomain on main domain (multi-tenant mode with SALES_AGENT_DOMAIN set)
        base = f"https://{tenant.subdomain}.{get_sales_agent_domain()}"
    elif main_url := get_sales_agent_url():
        # Explicit SALES_AGENT_DOMAIN URL
        base = main_url
    elif fly_app := os.environ.get("FLY_APP_NAME"):
        # Single-tenant mode on Fly.io - use the app's URL
        base = f"https://{fly_app}.fly.dev"
    else:
        # Local development fallback
        port = os.environ.get("ADCP_SALES_PORT", "8080")
        base = f"http://localhost:{port}"

    return f"{base}/auth/oidc/callback"


def is_oidc_config_valid(tenant_id: str) -> bool:
    """Check if OIDC configuration is valid and verified.

    A config is valid if:
    1. It has been verified (successful test OAuth flow)
    2. The verified redirect URI matches the current redirect URI

    Args:
        tenant_id: The tenant ID

    Returns:
        True if config is valid
    """
    with get_db_session() as session:
        config = session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant_id)).first()

        if not config:
            return False

        if not config.oidc_verified_at:
            return False

        if not config.oidc_verified_redirect_uri:
            return False

        # Get current redirect URI
        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()

        if not tenant:
            return False

        current_uri = get_tenant_redirect_uri(tenant)

        # Check if verified URI matches current
        if config.oidc_verified_redirect_uri != current_uri:
            logger.warning(
                f"OIDC config invalid for tenant {tenant_id}: "
                f"redirect URI changed from {config.oidc_verified_redirect_uri} to {current_uri}"
            )
            return False

        return True


def get_oidc_config_for_auth(tenant_id: str) -> dict | None:
    """Get OIDC configuration for authentication.

    Returns the config only if OIDC is enabled and valid.

    Args:
        tenant_id: The tenant ID

    Returns:
        Dict with client_id, client_secret, discovery_url, scopes or None
    """
    with get_db_session() as session:
        config = session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant_id)).first()

        if not config or not config.oidc_enabled:
            return None

        if not is_oidc_config_valid(tenant_id):
            logger.warning(f"OIDC config invalid for tenant {tenant_id}")
            return None

        return {
            "client_id": config.oidc_client_id,
            "client_secret": config.oidc_client_secret,  # Uses getter for decryption
            "discovery_url": config.oidc_discovery_url,
            "scopes": config.oidc_scopes or "openid email profile",
            "provider": config.oidc_provider,
        }


def delete_oidc_config(tenant_id: str) -> bool:
    """Delete OIDC configuration for a tenant.

    Args:
        tenant_id: The tenant ID

    Returns:
        True if deleted
    """
    with get_db_session() as session:
        config = session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant_id)).first()

        if config:
            session.delete(config)
            session.commit()
            logger.info(f"Deleted OIDC config for tenant {tenant_id}")
            return True

        return False


def get_auth_config_summary(tenant_id: str) -> dict:
    """Get a summary of authentication configuration for a tenant.

    Args:
        tenant_id: The tenant ID

    Returns:
        Dict with auth configuration summary
    """
    with get_db_session() as session:
        config = session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant_id)).first()

        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()

        if not tenant:
            return {"error": "Tenant not found"}

        current_redirect_uri = get_tenant_redirect_uri(tenant)

        if not config:
            return {
                "oidc_configured": False,
                "oidc_enabled": False,
                "redirect_uri": current_redirect_uri,
            }

        return {
            "oidc_configured": bool(config.oidc_client_id),
            "oidc_enabled": config.oidc_enabled,
            "oidc_provider": config.oidc_provider,
            "oidc_verified": config.oidc_verified_at is not None,
            "oidc_verified_at": config.oidc_verified_at.isoformat() if config.oidc_verified_at else None,
            "oidc_valid": is_oidc_config_valid(tenant_id),
            "redirect_uri": current_redirect_uri,
            "redirect_uri_changed": (
                config.oidc_verified_redirect_uri is not None
                and config.oidc_verified_redirect_uri != current_redirect_uri
            ),
            # Include actual config values for form population (not secret)
            "provider": config.oidc_provider,
            "client_id": config.oidc_client_id,
            "discovery_url": config.oidc_discovery_url,
            "scopes": config.oidc_scopes,
            # Indicate if secret is saved (don't return actual secret)
            "has_client_secret": bool(config.oidc_client_secret_encrypted),
        }
