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

Two variants:
    TenantContext       — fully loaded (all fields populated from DB)
    LazyTenantContext   — holds tenant_id immediately, defers DB load
                          until a non-tenant_id field is accessed
"""

import logging
from typing import Any

from pydantic import BaseModel

from src.core.config_loader import safe_json_loads

logger = logging.getLogger(__name__)


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
    supported_billing: list[str] | None = None  # BR-RULE-059: seller billing policy
    approval_mode: str = "require-human"  # BR-RULE-037: creative approval mode
    account_approval_mode: str | None = None  # BR-RULE-060: account approval mode (auto|credit_review|legal_review)
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
            supported_billing=safe_json_loads(tenant.supported_billing, None),
            approval_mode=tenant.approval_mode or "require-human",
            account_approval_mode=tenant.account_approval_mode,
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


class LazyTenantContext:
    """Lazy-loading tenant context — defers DB query until needed.

    Holds tenant_id immediately. The first access to any other field triggers
    a DB load, producing a full TenantContext. Subsequent accesses use the
    cached result.

    Supports the same dict-like and attribute access as TenantContext:
        tenant.tenant_id        # immediate (no DB)
        tenant["tenant_id"]     # immediate (no DB)
        tenant.approval_mode    # triggers DB load on first access
        tenant["name"]          # triggers DB load on first access
        "field" in tenant       # no DB (checks known field names)
        bool(tenant)            # always True (no DB)

    Call ensure_resolved() at transport boundaries to populate the
    tenant ContextVar for legacy code that reads get_current_tenant().
    """

    __slots__ = ("_tenant_id", "_resolved")

    def __init__(self, tenant_id: str) -> None:
        object.__setattr__(self, "_tenant_id", tenant_id)
        object.__setattr__(self, "_resolved", None)

    def _resolve(self) -> TenantContext:
        """Load full tenant from DB on first access. Cache the result.

        Does NOT mutate the tenant ContextVar. That must happen at
        explicit transport boundaries via ensure_resolved().
        """
        resolved = self._resolved
        if resolved is not None:
            return resolved

        from sqlalchemy.exc import SQLAlchemyError

        from src.core.config_loader import get_tenant_by_id

        try:
            tenant_dict = get_tenant_by_id(self._tenant_id)
            if tenant_dict:
                resolved = TenantContext.from_dict(tenant_dict)
                object.__setattr__(self, "_resolved", resolved)
                return resolved
        except (SQLAlchemyError, RuntimeError) as e:
            logger.debug(f"Could not load tenant from database: {e}")

        # Fallback: minimal TenantContext (unit tests, DB unavailable)
        resolved = TenantContext(tenant_id=self._tenant_id)
        object.__setattr__(self, "_resolved", resolved)
        return resolved

    @property
    def is_loaded(self) -> bool:
        """Check if the full tenant has been loaded from DB."""
        return self._resolved is not None

    def ensure_resolved(self) -> "TenantContext":
        """Force DB load and ContextVar population.

        Call at the transport boundary to guarantee that downstream code
        reading from get_current_tenant() sees a valid tenant dict.
        This is the ONLY place where LazyTenantContext sets the ContextVar.
        """
        resolved = self._resolve()

        from src.core.config_loader import get_tenant_by_id, set_current_tenant

        # Set the ContextVar at this explicit boundary call
        tenant_dict = get_tenant_by_id(self._tenant_id)
        if tenant_dict:
            set_current_tenant(tenant_dict)

        return resolved

    # --- tenant_id is always available without DB ---

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    # --- Attribute access: delegate to resolved TenantContext ---

    def __getattr__(self, name: str) -> Any:
        # __slots__ attrs (_tenant_id, _resolved) are handled by the descriptor
        # protocol, so __getattr__ is only called for other attributes.
        return getattr(self._resolve(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("LazyTenantContext is immutable")

    # --- Dict-like access for backward compat ---

    def __getitem__(self, key: str) -> Any:
        if key == "tenant_id":
            return self._tenant_id
        return self._resolve()[key]

    def get(self, key: str, default: Any = None) -> Any:
        if key == "tenant_id":
            return self._tenant_id
        return self._resolve().get(key, default)

    def keys(self) -> list[str]:
        return list(TenantContext.model_fields.keys())

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in TenantContext.model_fields

    def __iter__(self):
        return iter(TenantContext.model_fields.keys())

    def __bool__(self) -> bool:
        return True  # A lazy tenant is always truthy (tenant_id exists)

    def __repr__(self) -> str:
        if self._resolved is not None:
            return f"LazyTenantContext(tenant_id={self._tenant_id!r}, loaded=True)"
        return f"LazyTenantContext(tenant_id={self._tenant_id!r}, loaded=False)"
