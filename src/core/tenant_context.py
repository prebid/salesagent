"""Typed tenant context model.

Replaces the fragile dict[str, Any] tenant representation with a typed,
validated Pydantic model. All tenant fields are explicitly defined with
appropriate defaults.

Constructed at the transport boundary (resolve_identity / resolve_identity_from_context)
and passed through ResolvedIdentity to _impl functions.

Supports dict-like access for backward compatibility with existing code:
    tenant["tenant_id"]     # works (backward compat)
    tenant.get("field")     # works (backward compat)
    tenant.tenant_id        # preferred for new code
"""

from typing import Any

from pydantic import BaseModel

from src.core.config_loader import safe_json_loads


class TenantContext(BaseModel):
    """Typed tenant context — replaces dict[str, Any] for tenant data.

    Created from the database Tenant ORM model at the transport boundary.
    Immutable after creation. All fields have sensible defaults so tests
    can construct with just TenantContext(tenant_id="test").
    """

    tenant_id: str
    name: str = ""
    subdomain: str = ""
    virtual_host: str | None = None
    ad_server: str | None = None
    enable_axe_signals: bool = True
    authorized_emails: list[str] = []
    authorized_domains: list[str] = []
    slack_webhook_url: str | None = None
    slack_audit_webhook_url: str | None = None
    hitl_webhook_url: str | None = None
    admin_token: str | None = None
    auto_approve_format_ids: list[str] = []
    human_review_required: bool = True
    policy_settings: dict[str, Any] | None = None
    signals_agent_config: dict[str, Any] | None = None
    approval_mode: str = "require-human"
    gemini_api_key: str | None = None
    creative_review_criteria: str | None = None
    brand_manifest_policy: str = "require_auth"
    advertising_policy: dict[str, Any] | None = None
    product_ranking_prompt: str | None = None

    # --- Dict-like access for backward compatibility ---

    def __getitem__(self, key: str) -> Any:
        """Allow tenant['field'] access."""
        if key in type(self).model_fields:
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        """Allow tenant.get('field', default) access."""
        if key in type(self).model_fields:
            return getattr(self, key)
        return default

    def keys(self) -> list[str]:
        """Allow dict(tenant) and iteration over keys."""
        return list(type(self).model_fields.keys())

    def __contains__(self, key: object) -> bool:
        """Allow 'field' in tenant checks."""
        return isinstance(key, str) and key in type(self).model_fields

    def __iter__(self):
        """Allow dict(tenant) conversion and for key in tenant."""
        return iter(type(self).model_fields.keys())

    # --- Construction helpers ---

    @classmethod
    def from_orm_model(cls, tenant: Any) -> "TenantContext":
        """Construct from database Tenant ORM model.

        This is the primary constructor for production use. Reads all fields
        from the ORM model and deserializes JSON columns.
        """
        return cls(
            tenant_id=tenant.tenant_id,
            name=tenant.name or "",
            subdomain=tenant.subdomain or "",
            virtual_host=tenant.virtual_host,
            ad_server=tenant.ad_server,
            enable_axe_signals=tenant.enable_axe_signals if tenant.enable_axe_signals is not None else True,
            authorized_emails=safe_json_loads(tenant.authorized_emails, []),
            authorized_domains=safe_json_loads(tenant.authorized_domains, []),
            slack_webhook_url=tenant.slack_webhook_url,
            slack_audit_webhook_url=tenant.slack_audit_webhook_url,
            hitl_webhook_url=tenant.hitl_webhook_url,
            admin_token=tenant.admin_token,
            auto_approve_format_ids=safe_json_loads(tenant.auto_approve_format_ids, []),
            human_review_required=tenant.human_review_required if tenant.human_review_required is not None else True,
            policy_settings=safe_json_loads(tenant.policy_settings, None),
            signals_agent_config=safe_json_loads(tenant.signals_agent_config, None),
            approval_mode=tenant.approval_mode or "require-human",
            gemini_api_key=tenant.gemini_api_key,
            creative_review_criteria=tenant.creative_review_criteria,
            brand_manifest_policy=tenant.brand_manifest_policy or "require_auth",
            advertising_policy=safe_json_loads(tenant.advertising_policy, None),
            product_ranking_prompt=tenant.product_ranking_prompt,
        )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TenantContext":
        """Construct from a tenant dict (e.g., from serialize_tenant_to_dict).

        Handles the key mismatch where the old serializer used
        'auto_approve_formats' instead of 'auto_approve_format_ids'.
        """
        data = dict(d)
        # Handle legacy key name from serialize_tenant_to_dict
        if "auto_approve_formats" in data and "auto_approve_format_ids" not in data:
            data["auto_approve_format_ids"] = data.pop("auto_approve_formats")
        # Filter to only known fields
        known = cls.model_fields.keys()
        return cls(**{k: v for k, v in data.items() if k in known})
