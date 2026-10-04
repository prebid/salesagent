"""Typed tenant context model.

A typed, validated Pydantic model of a tenant: every field is explicitly
declared, with a default where one is sound.

The resolver (``src/core/resolved_identity._resolve_identity``) loads it once per request
and hands it on as ``ResolvedIdentity.tenant``. Nothing downstream loads a tenant again.

It is read by attribute only: ``tenant.tenant_id``. There is deliberately no dict shim
(``__getitem__``, ``get``, ``keys``, ``__contains__``), so ``tenant["tenant_id"]`` does not
compile and mypy sees every field a reader names.
"""

import logging
from typing import Any

from pydantic import BaseModel, field_validator

from src.core.config_loader import safe_json_loads

logger = logging.getLogger(__name__)


class TenantContext(BaseModel):
    """A tenant's fields, typed, as every reader downstream of the boundary sees them.

    Created from the database Tenant ORM model at the transport boundary.
    Immutable after creation. Two fields are required, because the row always carries
    them and a projection that admitted less would put the decision back on every
    reader: ``tenant_id`` names the tenant, and ``virtual_host`` is the host it is
    served at — the type is how ``canonical_agent_url`` stops needing a fallback. Every
    other field has a default, so a test names only what it is grading.
    """

    tenant_id: str
    name: str = ""
    virtual_host: str
    ad_server: str | None = None
    enable_axe_signals: bool = True
    authorized_emails: list[str] = []
    authorized_domains: list[str] = []
    slack_webhook_url: str | None = None
    slack_audit_webhook_url: str | None = None
    hitl_webhook_url: str | None = None
    auto_approve_format_ids: list[str] = []
    human_review_required: bool = True
    policy_settings: dict[str, Any] | None = None
    signals_agent_config: dict[str, Any] | None = None
    supported_billing: list[str] | None = None  # BR-RULE-059: seller billing policy
    account_sandbox: bool = False  # #1592 C2/A2: account.sandbox + sync_accounts provisioning gate
    approval_mode: str = "require-human"  # BR-RULE-037: creative approval mode
    account_approval_mode: str | None = None  # BR-RULE-060: account approval mode (auto|credit_review|legal_review)
    gemini_api_key: str | None = None
    creative_review_criteria: str | None = None
    brand_manifest_policy: str = "require_auth"
    advertising_policy: dict[str, Any] | None = None
    product_ranking_prompt: str | None = None
    # #1592 T1a: implementation-backed AdCP capability declaration blocks.
    # None = nothing declared = the pre-#1592 capabilities wire.
    capability_declarations: dict[str, Any] | None = None

    @field_validator("virtual_host")
    @classmethod
    def _fold_virtual_host(cls, value: str) -> str:
        """The host this projection carries is LOWERCASE, whoever built it.

        The read half of the same rule ``Tenant.virtual_host``'s validator states on the
        write half: a ``Host`` names a DNS name and DNS is case-insensitive, so the one
        canonical spelling is the folded one. Every reader that publishes or byte-matches
        this tenant's origin — the agent card, ``get_adcp_capabilities`` — reads it from
        here through ``canonical_agent_url``, so folding once at the projection is what
        keeps a row written before that validator existed from publishing an origin in a
        case no other reader would produce.
        """
        return value.strip().lower()

    @property
    def agent_url(self) -> str:
        """Where this tenant's agent is reachable: its canonical ORIGIN, scheme included.

        The ONE accessor, same name the ORM row carries, so a caller holding either shape
        reads the same thing and no caller anywhere puts a scheme in front of a host itself
        (#1845). The derivation stays in
        :func:`src.core.agent_identity.canonical_agent_url`: one place computes, one name
        reads.

        Always a string, and never a fallback — ``virtual_host`` is required on this model.
        """
        from src.core.agent_identity import canonical_agent_url

        return canonical_agent_url(self)

    # --- Construction helpers ---

    @classmethod
    def load(cls, tenant_id: str) -> "TenantContext | None":
        """The tenant row for *tenant_id* from the database, or ``None`` when no such tenant.

        The one place a tenant is loaded by id. The resolver calls it for the tenant the
        request names; a background path that starts from a stored row's ``tenant_id``
        (delivery reporting for a media buy) calls it for the same reason.
        """
        from src.core.config_loader import get_tenant_by_id

        row = get_tenant_by_id(tenant_id)
        return cls.from_dict(row) if row else None

    @classmethod
    def from_orm_model(cls, tenant: Any) -> "TenantContext":
        """Construct from database Tenant ORM model.

        This is the primary constructor for production use. Reads all fields
        from the ORM model and deserializes JSON columns.
        """
        return cls(
            tenant_id=tenant.tenant_id,
            name=tenant.name or "",
            virtual_host=tenant.virtual_host,
            ad_server=tenant.ad_server,
            enable_axe_signals=tenant.enable_axe_signals if tenant.enable_axe_signals is not None else True,
            authorized_emails=safe_json_loads(tenant.authorized_emails, []),
            authorized_domains=safe_json_loads(tenant.authorized_domains, []),
            slack_webhook_url=tenant.slack_webhook_url,
            slack_audit_webhook_url=tenant.slack_audit_webhook_url,
            hitl_webhook_url=tenant.hitl_webhook_url,
            auto_approve_format_ids=safe_json_loads(tenant.auto_approve_format_ids, []),
            human_review_required=tenant.human_review_required if tenant.human_review_required is not None else True,
            policy_settings=safe_json_loads(tenant.policy_settings, None),
            signals_agent_config=safe_json_loads(tenant.signals_agent_config, None),
            supported_billing=safe_json_loads(tenant.supported_billing, None),
            account_sandbox=tenant.account_sandbox,
            approval_mode=tenant.approval_mode or "require-human",
            account_approval_mode=tenant.account_approval_mode,
            gemini_api_key=tenant.gemini_api_key,
            creative_review_criteria=tenant.creative_review_criteria,
            brand_manifest_policy=tenant.brand_manifest_policy or "require_auth",
            advertising_policy=safe_json_loads(tenant.advertising_policy, None),
            product_ranking_prompt=tenant.product_ranking_prompt,
            capability_declarations=safe_json_loads(tenant.capability_declarations, None),
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
