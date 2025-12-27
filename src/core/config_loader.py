"""Configuration loader for multi-tenant setup."""

import json
import logging
import os
from contextvars import ContextVar
from typing import Any

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant

logger = logging.getLogger(__name__)


def safe_json_loads(value, default=None):
    """Safely load JSON value that might already be deserialized (SQLite vs PostgreSQL)."""
    if value is None:
        return default
    if isinstance(value, list | dict):
        # Already deserialized (SQLite)
        return value
    if isinstance(value, str):
        # JSON string (PostgreSQL)
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return default


# Thread-safe tenant context
current_tenant: ContextVar[dict[str, Any] | None] = ContextVar("current_tenant", default=None)


def get_current_tenant() -> dict[str, Any]:
    """Get current tenant from context.

    CRITICAL: This function must only be called AFTER tenant context has been established
    via get_principal_id_from_context() or get_principal_from_context() + set_current_tenant().

    Common mistake: Calling get_current_tenant() before authenticating the request.
    Correct order:
        1. principal_id = get_principal_id_from_context(ctx)  # Sets tenant context
        2. tenant = get_current_tenant()  # Now safe to call

    Raises:
        RuntimeError: If tenant context is not set (indicates authentication/ordering bug)
    """
    import inspect

    tenant = current_tenant.get()
    if not tenant:
        # SECURITY: Do NOT fall back to default tenant in production.
        # This would cause tenant isolation breach.
        # Only CLI/testing scripts should call this without context.

        # Get caller information for debugging
        frame = inspect.currentframe()
        caller_frame = frame.f_back if frame else None
        caller_info = ""
        if caller_frame:
            caller_file = caller_frame.f_code.co_filename
            caller_line = caller_frame.f_lineno
            caller_func = caller_frame.f_code.co_name
            caller_info = f"\n  Called from: {caller_file}:{caller_line} in {caller_func}()"

        raise RuntimeError(
            "No tenant context set. Tenant must be set via set_current_tenant() "
            "before calling this function. This is a critical security error - "
            "falling back to default tenant would breach tenant isolation.\n"
            "\n"
            "COMMON CAUSE: Calling get_current_tenant() before authenticating the request.\n"
            "FIX: Ensure get_principal_id_from_context(ctx) is called BEFORE get_current_tenant()."
            f"{caller_info}"
        )
    return tenant


def get_default_tenant() -> dict[str, Any] | None:
    """Get the default tenant for CLI/testing."""
    try:
        with get_db_session() as db_session:
            # Get first active tenant or specific default
            # Try to get 'default' tenant first, fall back to first active tenant
            stmt = select(Tenant).filter_by(tenant_id="default", is_active=True)
            tenant = db_session.scalars(stmt).first()

            if not tenant:
                # Fall back to first active tenant by creation date
                stmt = select(Tenant).filter_by(is_active=True).order_by(Tenant.created_at)
                tenant = db_session.scalars(stmt).first()

            if tenant:
                from src.core.utils.tenant_utils import serialize_tenant_to_dict

                return serialize_tenant_to_dict(tenant)
            return None
    except Exception as e:
        # If table doesn't exist or other DB errors, return None
        if "no such table" in str(e) or "does not exist" in str(e):
            return None
        raise


def load_config() -> dict[str, Any]:
    """
    Load configuration from current tenant.

    For backward compatibility, this returns config in the old format.
    In multi-tenant mode, config comes from database.
    """
    tenant = get_current_tenant()

    # Build config from tenant fields
    config = {
        "ad_server": {"adapter": tenant.get("ad_server", "mock"), "enabled": True},
        "creative_engine": {
            "auto_approve_format_ids": tenant.get("auto_approve_format_ids", []),
            "human_review_required": tenant.get("human_review_required", True),
        },
        "features": {
            "max_daily_budget": tenant.get("max_daily_budget", 10000),
            "enable_axe_signals": tenant.get("enable_axe_signals", True),
            "slack_webhook_url": tenant.get("slack_webhook_url"),
            "slack_audit_webhook_url": tenant.get("slack_audit_webhook_url"),
            "hitl_webhook_url": tenant.get("hitl_webhook_url"),
        },
        "admin_token": tenant.get("admin_token"),
        "dry_run": False,
    }

    # Add policy settings if present
    if tenant.get("policy_settings"):
        config["policy_settings"] = tenant["policy_settings"]

    # Apply environment variable overrides (for development/testing)
    if gemini_key := os.environ.get("GEMINI_API_KEY"):
        config["gemini_api_key"] = gemini_key

    # System-level overrides
    if dry_run := os.environ.get("ADCP_DRY_RUN"):
        config["dry_run"] = dry_run.lower() == "true"

    return config


def get_tenant_config(key: str, default=None):
    """Get config value for current tenant."""
    tenant = get_current_tenant()

    # Check if it's a top-level tenant field
    if key in tenant:
        return tenant[key]

    # Otherwise return default
    return default


def set_current_tenant(tenant_dict: dict[str, Any]):
    """Set the current tenant context."""
    current_tenant.set(tenant_dict)


def get_tenant_by_subdomain(subdomain: str) -> dict[str, Any] | None:
    """Get tenant by subdomain.

    Args:
        subdomain: The subdomain to look up (e.g., 'wonderstruck' from wonderstruck.sales-agent.scope3.com)

    Returns:
        Tenant dict if found, None otherwise
    """
    try:
        with get_db_session() as db_session:
            stmt = select(Tenant).filter_by(subdomain=subdomain, is_active=True)
            tenant = db_session.scalars(stmt).first()

            if tenant:
                from src.core.utils.tenant_utils import serialize_tenant_to_dict

                return serialize_tenant_to_dict(tenant)
            return None
    except Exception as e:
        # If table doesn't exist or other DB errors, return None
        if "no such table" in str(e) or "does not exist" in str(e):
            return None
        raise


def get_tenant_by_id(tenant_id: str) -> dict[str, Any] | None:
    """Get tenant by tenant_id.

    Args:
        tenant_id: The tenant_id to look up (e.g., 'tenant_wonderstruck')

    Returns:
        Tenant dict if found, None otherwise
    """
    try:
        with get_db_session() as db_session:
            stmt = select(Tenant).filter_by(tenant_id=tenant_id, is_active=True)
            tenant = db_session.scalars(stmt).first()

            if tenant:
                from src.core.utils.tenant_utils import serialize_tenant_to_dict

                return serialize_tenant_to_dict(tenant)
            return None
    except Exception as e:
        # If table doesn't exist or other DB errors, return None
        if "no such table" in str(e) or "does not exist" in str(e):
            return None
        raise


def get_tenant_by_virtual_host(virtual_host: str) -> dict[str, Any] | None:
    """Get tenant by virtual host."""
    try:
        with get_db_session() as db_session:
            stmt = select(Tenant).filter_by(virtual_host=virtual_host, is_active=True)
            tenant = db_session.scalars(stmt).first()

            if tenant:
                from src.core.utils.tenant_utils import serialize_tenant_to_dict

                return serialize_tenant_to_dict(tenant)
            return None
    except Exception as e:
        # If table doesn't exist or other DB errors, return None
        if "no such table" in str(e) or "does not exist" in str(e):
            return None
        raise


def get_secret(key: str, default: str | None = None) -> str | None:
    """Get a secret from environment or config."""
    return os.environ.get(key, default)


def is_single_tenant_mode() -> bool:
    """Check if the system is running in single-tenant mode.

    Single-tenant mode is the default. Multi-tenant mode must be explicitly enabled
    via ADCP_MULTI_TENANT=true environment variable.

    Returns:
        True if single-tenant mode (default), False if multi-tenant mode
    """
    return os.environ.get("ADCP_MULTI_TENANT", "false").lower() != "true"


def ensure_default_tenant_exists() -> dict[str, Any] | None:
    """Ensure a default tenant exists for single-tenant deployments.

    In single-tenant mode, this creates a default tenant if none exists.
    This should be called after database migrations complete.

    Returns:
        The default tenant dict if created/exists, None if in multi-tenant mode
    """
    if not is_single_tenant_mode():
        logger.debug("Multi-tenant mode enabled, skipping default tenant creation")
        return None

    try:
        with get_db_session() as db_session:
            # Check if any tenant exists
            stmt = select(Tenant).filter_by(is_active=True)
            existing = db_session.scalars(stmt).first()

            if existing:
                logger.debug(f"Tenant already exists: {existing.name}")
                from src.core.utils.tenant_utils import serialize_tenant_to_dict

                return serialize_tenant_to_dict(existing)

            # Create default tenant for single-tenant deployments
            logger.info("Single-tenant mode: Creating default tenant...")

            # Get super admin email for initial authorization
            super_admin_emails = os.environ.get("SUPER_ADMIN_EMAILS", "")
            authorized_emails = [e.strip() for e in super_admin_emails.split(",") if e.strip()]

            # Get super admin domains for initial authorization
            super_admin_domains = os.environ.get("SUPER_ADMIN_DOMAINS", "")
            authorized_domains = [d.strip() for d in super_admin_domains.split(",") if d.strip()]

            from datetime import UTC, datetime

            now = datetime.now(UTC)
            default_tenant = Tenant(
                tenant_id="default",
                name="Default Publisher",
                subdomain="default",  # Required field for routing
                ad_server="mock",  # Start with mock adapter, user can configure later
                authorized_emails=authorized_emails,
                authorized_domains=authorized_domains,
                is_active=True,
                created_at=now,
                updated_at=now,
            )

            db_session.add(default_tenant)
            db_session.commit()
            db_session.refresh(default_tenant)

            logger.info(f"Created default tenant: {default_tenant.name} (id: {default_tenant.tenant_id})")

            from src.core.utils.tenant_utils import serialize_tenant_to_dict

            return serialize_tenant_to_dict(default_tenant)

    except Exception as e:
        # Don't fail startup if tenant creation fails - log and continue
        logger.warning(f"Could not ensure default tenant exists: {e}")
        return None


def get_single_tenant() -> dict[str, Any] | None:
    """Get the single tenant for single-tenant deployments.

    In single-tenant mode, returns the only active tenant.
    In multi-tenant mode, returns None.

    Returns:
        The single tenant dict, or None if multi-tenant mode or no tenant exists
    """
    if not is_single_tenant_mode():
        return None

    return get_default_tenant()
