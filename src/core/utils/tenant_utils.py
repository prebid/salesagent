"""Tenant serialization utilities.

This module provides a centralized function for converting Tenant ORM models
to dictionaries for use in context/config. This is the single source of truth
for tenant serialization.

MANDATORY: All tenant dict construction must use serialize_tenant_to_dict().
"""

from typing import Any

from src.core.config_loader import safe_json_loads
from src.core.database.models import Tenant


def serialize_tenant_to_dict(tenant: Tenant) -> dict[str, Any]:
    """Convert Tenant ORM model to dict for context/config.

    Single source of truth for tenant serialization.
    All tenant field access should use this function.

    Args:
        tenant: Tenant ORM model instance

    Returns:
        Dictionary with all tenant fields properly serialized

    Example:
        >>> with get_db_session() as session:
        ...     stmt = select(Tenant).filter_by(tenant_id="example")
        ...     tenant = session.scalars(stmt).first()
        ...     tenant_dict = serialize_tenant_to_dict(tenant)
        ...     set_current_tenant(tenant_dict)
    """
    return {
        "tenant_id": tenant.tenant_id,
        "name": tenant.name,
        "subdomain": tenant.subdomain,
        "virtual_host": tenant.virtual_host,
        "ad_server": tenant.ad_server,
        "enable_axe_signals": tenant.enable_axe_signals,
        "authorized_emails": safe_json_loads(tenant.authorized_emails, []),
        "authorized_domains": safe_json_loads(tenant.authorized_domains, []),
        "slack_webhook_url": tenant.slack_webhook_url,
        "admin_token": tenant.admin_token,
        "auto_approve_formats": safe_json_loads(tenant.auto_approve_format_ids, []),
        "human_review_required": tenant.human_review_required,
        "slack_audit_webhook_url": tenant.slack_audit_webhook_url,
        "hitl_webhook_url": tenant.hitl_webhook_url,
        "policy_settings": safe_json_loads(tenant.policy_settings, None),
        "signals_agent_config": safe_json_loads(tenant.signals_agent_config, None),
        "approval_mode": tenant.approval_mode,
        "account_approval_mode": tenant.account_approval_mode,
        "supported_billing": safe_json_loads(tenant.supported_billing, None),
        "gemini_api_key": tenant.gemini_api_key,
        "creative_review_criteria": tenant.creative_review_criteria,
        "brand_manifest_policy": tenant.brand_manifest_policy,
        "advertising_policy": safe_json_loads(tenant.advertising_policy, None),
        "product_ranking_prompt": tenant.product_ranking_prompt,
    }
