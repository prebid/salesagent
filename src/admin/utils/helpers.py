"""Utility functions shared across admin UI modules."""

from __future__ import annotations

import json
import logging
import os
from functools import wraps
from typing import NamedTuple, TypeVar

from flask import abort, g, jsonify, redirect, session, url_for
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant, TenantManagementConfig, User

T = TypeVar("T")

logger = logging.getLogger(__name__)


def is_admin_production() -> bool:
    """Return True when admin should behave in production-safe mode.

    Treats both PRODUCTION=true and ENVIRONMENT=production as authoritative
    so security-sensitive checks do not drift between deployment styles.
    """
    return (
        os.environ.get("PRODUCTION", "").lower() == "true" or os.environ.get("ENVIRONMENT", "").lower() == "production"
    )


def parse_json_config(config_str):
    """Parse JSON config string."""
    if not config_str:
        return {}
    try:
        return json.loads(config_str)
    except (json.JSONDecodeError, TypeError):
        return {}


def get_tenant_config_from_db(tenant_id):
    """Get tenant configuration from database.

    Args:
        tenant_id: The tenant ID to fetch config for

    Returns:
        dict: The tenant configuration with adapter settings, features, etc.
    """
    if not tenant_id:
        logger.warning("get_tenant_config_from_db called with empty tenant_id")
        return {}

    try:
        with get_db_session() as db_session:
            stmt = select(Tenant).filter_by(tenant_id=tenant_id)
            tenant = db_session.scalars(stmt).first()
            if not tenant:
                logger.warning(f"Tenant not found: {tenant_id}")
                return {}

            # Build config from individual columns
            config = {
                "adapters": {},
                "features": {},
                "creative_engine": {},
                "admin_token": tenant.admin_token or "",
                "slack_webhook_url": tenant.slack_webhook_url or "",
                "policy_settings": {},
            }

            # Build adapter config from relationship
            if tenant.adapter_config:
                adapter_obj = tenant.adapter_config
                adapter_type = adapter_obj.adapter_type

                # Build the legacy JSON structure for backward compatibility
                adapter_config = {adapter_type: {"enabled": True}}

                # Add adapter-specific fields
                if adapter_type == "google_ad_manager":
                    if adapter_obj.gam_network_code:
                        adapter_config[adapter_type]["network_code"] = adapter_obj.gam_network_code
                    if adapter_obj.gam_refresh_token:
                        adapter_config[adapter_type]["refresh_token"] = adapter_obj.gam_refresh_token
                    # NOTE: gam_company_id removed - advertiser_id is per-principal
                    if adapter_obj.gam_trafficker_id:
                        adapter_config[adapter_type]["trafficker_id"] = adapter_obj.gam_trafficker_id
                    adapter_config[adapter_type]["manual_approval_required"] = (
                        adapter_obj.gam_manual_approval_required or False
                    )
                elif adapter_type == "mock":
                    adapter_config[adapter_type]["dry_run"] = adapter_obj.mock_dry_run or False
                elif adapter_type == "kevel":
                    if adapter_obj.kevel_network_id:
                        adapter_config[adapter_type]["network_id"] = adapter_obj.kevel_network_id
                    if adapter_obj.kevel_api_key:
                        adapter_config[adapter_type]["api_key"] = adapter_obj.kevel_api_key
                    adapter_config[adapter_type]["manual_approval_required"] = (
                        adapter_obj.kevel_manual_approval_required or False
                    )
                elif adapter_type == "triton":
                    if adapter_obj.triton_station_id:
                        adapter_config[adapter_type]["station_id"] = adapter_obj.triton_station_id
                    if adapter_obj.triton_api_key:
                        adapter_config[adapter_type]["api_key"] = adapter_obj.triton_api_key

                config["adapters"] = adapter_config

            # Build features config from individual columns
            # Note: max_daily_budget moved to currency_limits table (per-currency limits)
            config["features"] = {
                "enable_axe_signals": tenant.enable_axe_signals,
            }

            # Build creative engine config from individual columns
            config["creative_engine"] = {
                "auto_approve_formats": tenant.auto_approve_format_ids or [],
                "human_review_required": tenant.human_review_required,
            }

            # Add policy settings
            if tenant.policy_settings:
                policy_settings = parse_json_config(tenant.policy_settings)
                if policy_settings:
                    config["policy_settings"] = policy_settings

            return config

    except Exception as e:
        logger.error(f"Error getting tenant config: {e}")
        return {}


def is_super_admin(email):
    """Check if user is a super admin based on email or domain.

    Checks environment variables first, then falls back to database configuration.
    This ensures robust authentication even if database initialization hasn't run.
    """
    if not email:
        return False

    email_lower = email.lower()

    # 0. Check session cache first (if available) to avoid redundant checks
    try:
        if session.get("is_super_admin") and session.get("admin_email") == email_lower:
            logger.debug(f"Super admin access granted via session cache: {email}")
            return True
    except (RuntimeError, KeyError):
        # No session context available (e.g., outside request context)
        pass

    # 1. FIRST: Check environment variables (most reliable)
    env_emails = os.environ.get("SUPER_ADMIN_EMAILS", "")
    if env_emails:
        env_emails_list = [e.strip().lower() for e in env_emails.split(",") if e.strip()]
        if email_lower in env_emails_list:
            logger.debug(f"Super admin access granted via environment: {email}")
            _cache_admin_status(email_lower, True)
            return True

    env_domains = os.environ.get("SUPER_ADMIN_DOMAINS", "")
    if env_domains:
        env_domains_list = [d.strip().lower() for d in env_domains.split(",") if d.strip()]
        email_domain = email_lower.split("@")[1] if "@" in email_lower else ""
        if email_domain in env_domains_list:
            logger.debug(f"Super admin access granted via environment domain: {email}")
            _cache_admin_status(email_lower, True)
            return True

    # 2. FALLBACK: Check database configuration
    try:
        with get_db_session() as db_session:
            # Check exact emails
            stmt = select(TenantManagementConfig).filter_by(config_key="super_admin_emails")
            emails_config = db_session.scalars(stmt).first()
            if emails_config and emails_config.config_value:
                emails_list = [e.strip().lower() for e in emails_config.config_value.split(",")]
                if email_lower in emails_list:
                    logger.debug(f"Super admin access granted via database: {email}")
                    _cache_admin_status(email_lower, True)
                    return True

            # Check domains
            stmt = select(TenantManagementConfig).filter_by(config_key="super_admin_domains")
            domains_config = db_session.scalars(stmt).first()
            if domains_config and domains_config.config_value:
                domains_list = [d.strip().lower() for d in domains_config.config_value.split(",")]
                email_domain = email_lower.split("@")[1] if "@" in email_lower else ""
                if email_domain in domains_list:
                    logger.debug(f"Super admin access granted via database domain: {email}")
                    _cache_admin_status(email_lower, True)
                    return True

    except Exception as e:
        logger.error(f"Error checking super admin status in database: {e}")
        # Don't fail completely - environment check already happened above

    # Cache negative result too (to avoid repeated expensive checks)
    _cache_admin_status(email_lower, False)
    return False


def _cache_admin_status(email, is_admin):
    """Cache admin status in session if available."""
    try:
        session["is_super_admin"] = is_admin
        session["admin_email"] = email
        if is_admin:
            logger.debug(f"Admin status cached in session: {email}")
    except (RuntimeError, KeyError):
        # Session not available or read-only
        pass


def is_tenant_admin(email, tenant_id=None):
    """Check if user is a tenant admin.

    Args:
        email: User's email address
        tenant_id: Optional tenant ID to check admin status for specific tenant

    Returns:
        bool: True if user is a tenant admin
    """
    if not email:
        return False

    # Super admins are implicitly tenant admins
    if is_super_admin(email):
        return True

    # Check if user is a tenant admin in the database
    try:
        with get_db_session() as db_session:
            stmt = select(User).filter_by(email=email.lower(), is_active=True, is_admin=True)

            if tenant_id:
                # Check for specific tenant
                stmt = stmt.filter_by(tenant_id=tenant_id)

            user = db_session.scalars(stmt).first()
            return user is not None

    except Exception as e:
        logger.error(f"Error checking tenant admin status: {e}")
        return False

    return False


def require_auth(admin_only=False):
    """Decorator to require authentication for routes."""

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Check for test mode
            test_mode = os.environ.get("ADCP_AUTH_TEST_MODE", "").lower() == "true"
            if test_mode and "test_user" in session:
                g.user = session["test_user"]
                return f(*args, **kwargs)

            if "user" not in session:
                logger.info(f"require_auth: No 'user' in session. Session keys: {list(session.keys())}")
                # Store the original URL to redirect back after login
                from flask import request

                return redirect(url_for("auth.login", next=request.url))

            # Store user in g for access in view functions
            g.user = session["user"]

            # Handle both string email and dict user info formats for admin check
            user_info = session["user"]
            if isinstance(user_info, dict):
                email = user_info.get("email", "")
            else:
                email = str(user_info)

            # Check admin requirement
            if admin_only and not is_super_admin(email):
                abort(403)

            return f(*args, **kwargs)

        return decorated_function

    return decorator


def require_tenant_access(api_mode=False):
    """Decorator to require tenant access for routes."""

    def decorator(f):
        @wraps(f)
        def decorated_function(tenant_id, *args, **kwargs):
            # Debug logging for SSE authentication issues
            from flask import request

            has_session = "user" in session
            has_cookies = bool(request.cookies)
            logger.info(
                f"Auth check - tenant: {tenant_id}, method: {request.method}, has_session: {has_session}, has_cookies: {has_cookies}, session_keys: {list(session.keys())}"
            )

            # Check for test mode (global env var OR per-tenant auth_setup_mode)
            test_mode = os.environ.get("ADCP_AUTH_TEST_MODE", "").lower() == "true"

            # Also check per-tenant auth_setup_mode if test_user is in session
            if not test_mode and "test_user" in session:
                try:
                    with get_db_session() as db_session:
                        tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
                        if tenant and getattr(tenant, "auth_setup_mode", False):
                            test_mode = True
                            logger.debug(f"Auth setup mode enabled for tenant {tenant_id}")
                except Exception as e:
                    logger.warning(f"Error checking tenant auth_setup_mode: {e}")

            if test_mode and "test_user" in session:
                g.user = session["test_user"]
                # Test users can access their assigned tenant
                if "test_tenant_id" in session and session["test_tenant_id"] == tenant_id:
                    return f(tenant_id, *args, **kwargs)
                # Super admins can access all tenants
                if session.get("test_user_role") == "super_admin":
                    return f(tenant_id, *args, **kwargs)

            if "user" not in session:
                if api_mode:
                    return jsonify({"error": "Authentication required"}), 401
                # Redirect to tenant-specific login (preserves tenant context)
                return redirect(url_for("auth.tenant_login", tenant_id=tenant_id, next=request.url))

            user_info = session["user"]

            # Handle both string email and dict user info formats
            if isinstance(user_info, dict):
                email = user_info.get("email", "")
            else:
                email = str(user_info)

            # Check super admin status (is_super_admin handles env + db + session caching internally)
            if is_super_admin(email):
                return f(tenant_id, *args, **kwargs)

            # Check if user has access to this specific tenant
            try:
                with get_db_session() as db_session:
                    stmt = select(User).filter_by(email=email.lower(), tenant_id=tenant_id, is_active=True)
                    user = db_session.scalars(stmt).first()

                    if not user:
                        if api_mode:
                            return jsonify({"error": "Access denied"}), 403
                        abort(403)

                    return f(tenant_id, *args, **kwargs)

            except Exception as e:
                # Don't catch abort exceptions (they should propagate)
                if hasattr(e, "code") and e.code in [403, 404]:
                    raise

                logger.error(f"Error checking tenant access: {e}")
                if api_mode:
                    return jsonify({"error": "Internal server error"}), 500
                abort(500)

        return decorated_function

    return decorator


def validate_gam_network_response(network):
    """Validate GAM network API response structure."""
    if not network:
        return False, "Network response is empty"

    # Check required fields
    required_fields = ["networkCode", "displayName", "id"]
    for field in required_fields:
        if field not in network:
            return False, f"Missing required field: {field}"

    # Validate field types
    try:
        int(network["networkCode"])
        int(network["id"])
    except (ValueError, TypeError):
        return False, "Network code and ID must be numeric"

    if not isinstance(network["displayName"], str):
        return False, "Display name must be a string"

    return True, None


def validate_gam_user_response(user):
    """Validate GAM user API response structure."""
    if not user:
        return False, "User response is empty"

    # Check required fields
    if "id" not in user:
        return False, "Missing user ID"

    # Validate ID is numeric
    try:
        int(user["id"])
    except (ValueError, TypeError):
        return False, "User ID must be numeric"

    return True, None


def get_custom_targeting_mappings(tenant_id=None):
    """Get custom targeting key and value mappings for a tenant.

    Returns tuple of (key_mappings, value_mappings) dicts.
    """
    # Default mappings for header bidding (common across many publishers)
    key_mappings = {
        "13748922": "hb_pb",
        "14095946": "hb_source",
        "14094596": "hb_format",
    }

    value_mappings = {
        "448589710493": "0.01",
        "448946107548": "freestar",
        "448946356517": "prebid",
        "448946353802": "video",
    }

    if tenant_id:
        try:
            with get_db_session() as db_session:
                stmt = select(Tenant).filter_by(tenant_id=tenant_id)
                tenant = db_session.scalars(stmt).first()
                # TODO: Custom targeting mappings should be stored in a dedicated table or column
                # For now, return default mappings
                if tenant:
                    pass  # Could override with tenant-specific mappings
        except Exception as e:
            logger.error(f"Error getting custom targeting mappings: {e}")

    return key_mappings, value_mappings


def translate_custom_targeting(custom_targeting_node, tenant_id=None):
    """Translate GAM custom targeting structure to readable format."""
    if not custom_targeting_node:
        return None

    # Get mappings (could be tenant-specific in future)
    key_mappings, value_mappings = get_custom_targeting_mappings(tenant_id)

    def translate_node(node):
        if not node:
            return None

        if isinstance(node, dict):
            # Handle dict-based nodes (from tests/API)
            if "logicalOperator" in node:
                # This is a group node with AND/OR logic
                operator = node["logicalOperator"].lower()
                children = []
                if "children" in node and node["children"]:
                    for child in node["children"]:
                        translated_child = translate_node(child)
                        if translated_child:
                            children.append(translated_child)

                if len(children) == 1:
                    return children[0]
                elif len(children) > 1:
                    return {operator: children}
                return None

            elif "keyId" in node:
                # This is a key-value targeting node
                key_id = str(node["keyId"])
                key_name = key_mappings.get(key_id, f"key_{key_id}")

                operator = node.get("operator", "IS")
                value_ids = node.get("valueIds", [])

                # Translate value IDs to names
                values = []
                for value_id in value_ids:
                    value_name = value_mappings.get(str(value_id), str(value_id))
                    values.append(value_name)

                if operator == "IS":
                    return {"key": key_name, "in": values}
                elif operator == "IS_NOT":
                    return {"key": key_name, "not_in": values}
                else:
                    return {"key": key_name, "operator": operator, "values": values}

        elif hasattr(node, "logicalOperator"):
            # Handle SOAP/object-based nodes (from GAM)
            operator = node.logicalOperator.lower()
            children = []
            if hasattr(node, "children") and node.children:
                for child in node.children:
                    translated_child = translate_node(child)
                    if translated_child:
                        children.append(translated_child)

            if len(children) == 1:
                return children[0]
            elif len(children) > 1:
                return {operator: children}
            return None

        elif hasattr(node, "keyId"):
            # This is a SOAP key-value targeting node
            key_id = str(node.keyId)
            key_name = key_mappings.get(key_id, f"key_{key_id}")

            operator = getattr(node, "operator", "IS")
            value_ids = getattr(node, "valueIds", [])

            # Translate value IDs to names
            values = []
            for value_id in value_ids:
                value_name = value_mappings.get(str(value_id), str(value_id))
                values.append(value_name)

            if operator == "IS":
                return {"key": key_name, "in": values}
            elif operator == "IS_NOT":
                return {"key": key_name, "not_in": values}
            else:
                return {"key": key_name, "operator": operator, "values": values}

        return None

    return translate_node(custom_targeting_node)


# ---------------------------------------------------------------------------
# Query limiting
# ---------------------------------------------------------------------------


class LimitedResult(NamedTuple):
    """Result of a limited query — rows plus a truncation flag."""

    rows: list
    truncated: bool


def execute_limited(db_session: Session, stmt: Select, limit: int) -> LimitedResult:
    """Execute *stmt* with a row limit and report whether the result was truncated.

    Returns ``LimitedResult(rows, truncated)`` where *truncated* is ``True``
    when the database returned exactly *limit* rows (meaning more may exist).
    """
    rows = list(db_session.scalars(stmt.limit(limit)).all())
    return LimitedResult(rows=rows, truncated=len(rows) >= limit)
