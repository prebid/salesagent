"""Configuration loader for multi-tenant setup."""

import json
import os
from contextvars import ContextVar
from typing import Any

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant


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
    """Get current tenant from context."""
    tenant = current_tenant.get()
    if not tenant:
        # Fallback for CLI/testing - use default tenant
        tenant = get_default_tenant()
        if not tenant:
            raise RuntimeError("No tenant in context and no default tenant found")
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
                return {
                    "tenant_id": tenant.tenant_id,
                    "name": tenant.name,
                    "subdomain": tenant.subdomain,
                    "virtual_host": tenant.virtual_host,
                    "ad_server": tenant.ad_server,
                    "max_daily_budget": tenant.max_daily_budget,
                    "enable_axe_signals": tenant.enable_axe_signals,
                    "authorized_emails": safe_json_loads(tenant.authorized_emails, []),
                    "authorized_domains": safe_json_loads(tenant.authorized_domains, []),
                    "slack_webhook_url": tenant.slack_webhook_url,
                    "admin_token": tenant.admin_token,
                    "auto_approve_formats": safe_json_loads(tenant.auto_approve_formats, []),
                    "human_review_required": tenant.human_review_required,
                    "slack_audit_webhook_url": tenant.slack_audit_webhook_url,
                    "hitl_webhook_url": tenant.hitl_webhook_url,
                    "policy_settings": safe_json_loads(tenant.policy_settings, None),
                    "signals_agent_config": safe_json_loads(tenant.signals_agent_config, None),
                }
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
            "auto_approve_formats": tenant.get("auto_approve_formats", []),
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
                return {
                    "tenant_id": tenant.tenant_id,
                    "name": tenant.name,
                    "subdomain": tenant.subdomain,
                    "virtual_host": tenant.virtual_host,
                    "ad_server": tenant.ad_server,
                    "max_daily_budget": tenant.max_daily_budget,
                    "enable_axe_signals": tenant.enable_axe_signals,
                    "authorized_emails": safe_json_loads(tenant.authorized_emails, []),
                    "authorized_domains": safe_json_loads(tenant.authorized_domains, []),
                    "slack_webhook_url": tenant.slack_webhook_url,
                    "admin_token": tenant.admin_token,
                    "auto_approve_formats": safe_json_loads(tenant.auto_approve_formats, []),
                    "human_review_required": tenant.human_review_required,
                    "slack_audit_webhook_url": tenant.slack_audit_webhook_url,
                    "hitl_webhook_url": tenant.hitl_webhook_url,
                    "policy_settings": safe_json_loads(tenant.policy_settings, None),
                    "signals_agent_config": safe_json_loads(tenant.signals_agent_config, None),
                }
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
                return {
                    "tenant_id": tenant.tenant_id,
                    "name": tenant.name,
                    "subdomain": tenant.subdomain,
                    "virtual_host": tenant.virtual_host,
                    "ad_server": tenant.ad_server,
                    "max_daily_budget": tenant.max_daily_budget,
                    "enable_axe_signals": tenant.enable_axe_signals,
                    "authorized_emails": safe_json_loads(tenant.authorized_emails, []),
                    "authorized_domains": safe_json_loads(tenant.authorized_domains, []),
                    "slack_webhook_url": tenant.slack_webhook_url,
                    "admin_token": tenant.admin_token,
                    "auto_approve_formats": safe_json_loads(tenant.auto_approve_formats, []),
                    "human_review_required": tenant.human_review_required,
                    "slack_audit_webhook_url": tenant.slack_audit_webhook_url,
                    "hitl_webhook_url": tenant.hitl_webhook_url,
                    "policy_settings": safe_json_loads(tenant.policy_settings, None),
                    "signals_agent_config": safe_json_loads(tenant.signals_agent_config, None),
                }
            return None
    except Exception as e:
        # If table doesn't exist or other DB errors, return None
        if "no such table" in str(e) or "does not exist" in str(e):
            return None
        raise


def get_secret(key: str, default: str = None) -> str:
    """Get a secret from environment or config."""
    return os.environ.get(key, default)
