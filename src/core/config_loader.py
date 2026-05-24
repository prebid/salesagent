"""Configuration loader for multi-tenant setup.

Environment variables:
    ADCP_MULTI_TENANT: Set to "true" to enable multi-tenant mode with subdomain routing.
    SALES_AGENT_DOMAIN: Required in multi-tenant mode (e.g., "sales-agent.example.com").
    SUPER_ADMIN_EMAILS: Comma-separated list of super admin emails.
    SUPER_ADMIN_DOMAINS: Comma-separated list of super admin email domains.
"""

import json
import logging
import os
from contextvars import ContextVar
from typing import Any

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import CurrencyLimit, PricingOption, Principal, Product, PropertyTag, Tenant

logger = logging.getLogger(__name__)


def validate_multi_tenant_config() -> list[str]:
    """Validate configuration for multi-tenant mode.

    Returns:
        List of validation error messages, empty if valid.
    """
    errors = []

    if not is_single_tenant_mode():
        # Multi-tenant mode requires SALES_AGENT_DOMAIN
        if not os.environ.get("SALES_AGENT_DOMAIN"):
            errors.append("SALES_AGENT_DOMAIN is required for multi-tenant mode")

    return errors


def safe_json_loads(value, default=None):
    """Safely load JSON value that might already be deserialized (e.g. JSONB) or a JSON string."""
    if value is None:
        return default
    if isinstance(value, list | dict):
        # Already deserialized (JSONB column)
        return value
    if isinstance(value, str):
        # JSON string
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
    via resolve_identity() at the transport boundary + set_current_tenant().

    Common mistake: Calling get_current_tenant() before authenticating the request.
    Correct order:
        1. identity = resolve_identity(headers, protocol=...)  # At transport boundary
        2. set_current_tenant(identity.tenant)  # Sets tenant context
        3. tenant = get_current_tenant()  # Now safe to call

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
            "FIX: Ensure resolve_identity() is called at the transport boundary BEFORE get_current_tenant()."
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


def set_current_tenant(tenant_data: Any) -> None:
    """Set the current tenant context.

    Normalizes TenantContext / LazyTenantContext to a plain dict before
    storing in the ContextVar.  This is the SINGLE conversion point —
    callers pass whatever they have and this function ensures the ContextVar
    always holds dict[str, Any].
    """
    from src.core.tenant_context import LazyTenantContext, TenantContext

    if isinstance(tenant_data, (TenantContext, LazyTenantContext)):
        tenant_data = dict(tenant_data)
    current_tenant.set(tenant_data)


def get_tenant_by_subdomain(subdomain: str) -> dict[str, Any] | None:
    """Get tenant by subdomain.

    Args:
        subdomain: The subdomain to look up (e.g., 'wonderstruck' from wonderstruck.sales-agent.example.com)

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


def _ensure_default_principal(db_session: Any, tenant_id: str) -> None:
    """Seed a default principal for the given tenant from ADCP_AUTH_TOKEN env, idempotent.

    Storyboard CI and ``docker compose up`` need an authenticated principal in the
    default tenant so MCP tool calls (sync_accounts, create_media_buy, etc.) don't
    fail with "Authentication token is invalid for tenant 'default'". Migrations alone
    only create the Tenant row — they don't seed principals. The full demo seeder
    (``scripts/setup/init_database.py``) does, but it is not part of the docker
    startup chain.

    Seeding here is opt-in via ``ADCP_AUTH_TOKEN`` so production deployments without
    that env var are unaffected. The lookup-and-skip pattern mirrors the existing
    init_database.py:140 idempotency.
    """
    token = os.environ.get("ADCP_AUTH_TOKEN")
    if not token:
        return

    existing_principal = db_session.scalars(select(Principal).filter_by(tenant_id=tenant_id)).first()
    if existing_principal:
        logger.debug("Tenant %s already has a principal, skipping seed", tenant_id)
        return

    principal = Principal(
        tenant_id=tenant_id,
        principal_id="default_principal",
        name="Default Principal",
        platform_mappings={"mock": {"advertiser_id": "mock-default"}},
        access_token=token,
    )
    db_session.add(principal)
    db_session.commit()
    logger.info("Seeded default principal for tenant %s (token len=%d)", tenant_id, len(token))


def _ensure_default_storyboard_fixtures(db_session: Any, tenant_id: str) -> None:
    """Seed the dependency chain a storyboard `get_products` call needs.

    The storyboard runner (``@adcp/sdk`` compliance scenarios) hits
    ``get_products`` against the default tenant and asserts that products are
    present with ``product_id`` populated. The default tenant ships with the
    tenant row + principal row only; without at least one Product the
    storyboard scenarios ``refine_products``, ``inventory_list_targeting``,
    and ``inventory_list_no_match`` all fail at the
    ``field_present: products[0].product_id`` validation.

    Per CLAUDE.md tenant-setup dependency order, products require:

    1. ``CurrencyLimit`` (USD) — budget validation looks this up
    2. ``PropertyTag`` (``all_inventory``) — products reference it via
       ``property_tags=["all_inventory"]`` (the by_tag selector path)
    3. ``Product`` — at least one
    4. ``PricingOption`` — at least one per product

    Each step is idempotent so repeated ``docker compose up`` runs are safe.
    Gated on ``ADCP_AUTH_TOKEN`` (matches ``_ensure_default_principal``) so
    production deployments without that env var are unaffected.
    """
    if not os.environ.get("ADCP_AUTH_TOKEN"):
        return

    # Step 1: CurrencyLimit for USD (idempotent)
    existing_currency = db_session.scalars(
        select(CurrencyLimit).filter_by(tenant_id=tenant_id, currency_code="USD")
    ).first()
    if not existing_currency:
        db_session.add(
            CurrencyLimit(
                tenant_id=tenant_id,
                currency_code="USD",
                min_package_budget=None,
                max_daily_package_spend=None,
            )
        )

    # Step 2: PropertyTag for "all_inventory" (idempotent)
    existing_tag = db_session.scalars(
        select(PropertyTag).filter_by(tenant_id=tenant_id, tag_id="all_inventory")
    ).first()
    if not existing_tag:
        db_session.add(
            PropertyTag(
                tenant_id=tenant_id,
                tag_id="all_inventory",
                name="All Inventory",
                description="Catch-all tag covering the publisher's full property set",
            )
        )

    # Step 3: Product (idempotent — guard on product_id)
    existing_product = db_session.scalars(
        select(Product).filter_by(tenant_id=tenant_id, product_id="default_display")
    ).first()
    if existing_product:
        db_session.commit()
        return

    from decimal import Decimal

    product = Product(
        tenant_id=tenant_id,
        product_id="default_display",
        name="Default Display",
        description="Default display product seeded so storyboard get_products scenarios succeed",
        format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
        targeting_template={"geo_countries": ["US"]},
        delivery_type="guaranteed",
        property_tags=["all_inventory"],
        delivery_measurement={"provider": "publisher"},
    )
    db_session.add(product)

    # Step 4: PricingOption (CPM, fixed, $10)
    db_session.add(
        PricingOption(
            tenant_id=tenant_id,
            product_id="default_display",
            pricing_model="cpm",
            rate=Decimal("10.00"),
            currency="USD",
            is_fixed=True,
        )
    )
    db_session.commit()
    logger.info("Seeded default storyboard fixtures (product + pricing) for tenant %s", tenant_id)


def ensure_default_tenant_exists() -> dict[str, Any] | None:
    """Ensure a default tenant exists for single-tenant deployments.

    In single-tenant mode, this creates a default tenant if none exists.
    This should be called after database migrations complete.

    Also seeds a default Principal from ADCP_AUTH_TOKEN when that env var is
    set (storyboard CI + ``docker compose up`` developer ergonomics) — see
    ``_ensure_default_principal``.

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
                # Even when the tenant already exists, ensure a principal is seeded
                # (idempotent) so storyboard CI works on subsequent docker compose runs.
                _ensure_default_principal(db_session, existing.tenant_id)
                _ensure_default_storyboard_fixtures(db_session, existing.tenant_id)
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

            _ensure_default_principal(db_session, default_tenant.tenant_id)
            _ensure_default_storyboard_fixtures(db_session, default_tenant.tenant_id)

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
