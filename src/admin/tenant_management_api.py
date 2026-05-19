"""Tenant Management API for managing tenants.

Sprint 1 of [embedded-mode](../../docs/design/embedded-mode.md)
extends this blueprint with spectree-validated endpoints for the platform-managed
surface (provision / list / get / patch / deactivate / reactivate / delete /
adapter-config / adapter-config test). Legacy non-spectree endpoints below
remain for direct-customer (open-instance) callers.
"""

import json
import logging
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from flask import Blueprint, jsonify, request
from spectree import Response, SpecTree
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import attributes

from src.admin.api_schemas.tenant_management import (
    WEBHOOK_EVENT_TYPES,
    AccountDetail,
    AccountSummary,
    AdapterCapabilitiesSummary,
    AdapterCatalogEntry,
    AdapterConfigResponse,
    AdapterStatusResponse,
    ApiError,
    ApproveWorkflowRequest,
    BroadstreetAdapterConfig,
    BuyerAdvertiserMapping,
    CreateAccountRequest,
    CreateBuyerAdvertiserMappingRequest,
    CreateWebhookSubscriptionRequest,
    FreeWheelAdapterConfig,
    GAMAdapterConfig,
    ListAccountsManagedResponse,
    ListAdaptersResponse,
    ListAuditLogResponse,
    ListBuyerAdvertiserMappingsResponse,
    ListGamAdvertisersResponse,
    ListMediaBuysResponse,
    ListRecentBuyersResponse,
    ListSyncHistoryResponse,
    ListTenantsResponse,
    ListWebhooksResponse,
    ListWorkflowsResponse,
    MediaBuyDetail,
    MockAdapterConfig,
    PreviewAdapterRequest,
    PreviewAdapterResponse,
    ProvisionedPrincipalResponse,
    ProvisionTenantRequest,
    ProvisionTenantResponse,
    RecentBuyer,
    RefreshConflictResponse,
    RefreshResponse,
    RejectWorkflowRequest,
    SpringServeAdapterConfig,
    TenantDetail,
    TenantStatusResponse,
    TenantSummary,
    TestConnectionResponse,
    UpdateBuyerAdvertiserMappingRequest,
    UpdateTenantRequest,
    WebhookSubscriptionCreatedResponse,
    WebhookSubscriptionSummary,
    WebhookTestDeliveryResult,
    WebhookTestResponse,
    WorkflowDetail,
)
from src.admin.api_schemas.tenant_management import (
    AdapterConfig as AdapterConfigSchema,
)
from src.admin.api_schemas.tenant_management import (
    GamAdvertiser as GamAdvertiserSchema,
)
from src.admin.auth_helpers import require_api_key_auth
from src.admin.services.adapter_connection_tester import (
    ProbeResult,
    preview_adapter,
    probe_adapter_connection,
)
from src.admin.services.tenant_status_service import get_tenant_status, invalidate_status_cache
from src.core.database.database_session import get_db_session
from src.core.database.embedded_tenant_guard import EmbeddedTenantWriteError
from src.core.database.models import (
    Account,
    AdapterConfig,
    AdvertiserRoutingRule,
    CurrencyLimit,
    GamAdvertiser,
    MediaBuy,
    Principal,
    PropertyTag,
    SyncJob,
    Tenant,
)
from src.services.recent_buyers_service import compute_recent_buyers

logger = logging.getLogger(__name__)

# Create Blueprint
tenant_management_api = Blueprint("tenant_management_api", __name__, url_prefix="/api/v1/tenant-management")

# OpenAPI spec is served by spectree under the blueprint's `path="docs"`:
#   spec:       {blueprint_prefix}/docs/openapi.json
#   Swagger UI: {blueprint_prefix}/docs/swagger
#   Redoc:      {blueprint_prefix}/docs/redoc
# In production the admin app is WSGI-mounted under /admin/, so the public URLs are
# /admin/api/v1/tenant-management/docs/{openapi.json,swagger,redoc}.
#
# The spec endpoint is intentionally unauthenticated — it describes shapes,
# not data. Every endpoint below is gated by ``require_tenant_management_api_key``
# (``X-Tenant-Management-API-Key`` header). If you ever mount additional routes
# on this blueprint without that decorator, revisit this assumption.
spec = SpecTree(
    "flask",
    title="Sales Agent — Tenant Management API",
    version="v1",
    path="docs",
    openapi_url_prefix="",
)


require_tenant_management_api_key = require_api_key_auth(
    env_var="TENANT_MANAGEMENT_API_KEY",
    config_key="tenant_management_api_key",
    header="X-Tenant-Management-API-Key",
)


# ---------------------------------------------------------------------------
# Helpers shared by the new spectree endpoints
# ---------------------------------------------------------------------------


def _api_error(code: str, message: str, status: int, details: dict | None = None):
    """Build a (jsonified, status) tuple matching the :class:`ApiError` schema."""
    body = ApiError(error=code, message=message, details=details).model_dump(exclude_none=True)
    return jsonify(body), status


def _adapter_probe_error(adapter_type: str, probe: ProbeResult):
    """Map a failed adapter probe into the appropriate ``ApiError`` response.

    Translates the typed sub-code from :class:`ProbeResult` into the
    ``adapter_{code}`` family of API error codes. Forwards the structured
    ``details`` block (``vendor_fault``) and the ``remediation`` hint so
    downstream consumers can branch on machine-readable fields rather than
    parsing the human message. See :mod:`src.admin.services.adapter_connection_tester`
    for the classification.
    """
    code = probe.error_code or "connection_failed"
    details: dict[str, Any] = {
        "adapter_type": adapter_type,
        "error": probe.error_message,
    }
    if probe.remediation:
        details["remediation"] = probe.remediation
    if probe.details:
        details.update(probe.details)
    return _api_error(
        f"adapter_{code}",
        f"Adapter {adapter_type!r} connection probe failed: {probe.error_message}",
        400,
        details=details,
    )


def _tenant_to_summary(tenant: Tenant, adapter_configured: bool) -> dict:
    """Serialize a :class:`Tenant` as a :class:`TenantSummary`-compatible dict."""
    embedded = bool(tenant.is_embedded)
    return TenantSummary(
        tenant_id=tenant.tenant_id,
        name=tenant.name,
        subdomain=tenant.subdomain,
        external_org_id=tenant.external_org_id,
        external_source=tenant.external_source,
        # Both fields populated from the same source; ``managed_externally`` is a
        # deprecated alias kept on the wire so existing Storefront callers keep working.
        is_embedded=embedded,
        managed_externally=embedded,
        is_active=bool(tenant.is_active),
        billing_plan=tenant.billing_plan or "standard",
        ad_server=tenant.ad_server,
        adapter_configured=adapter_configured,
        created_at=tenant.created_at,
    ).model_dump(mode="json")


def _tenant_to_detail(tenant: Tenant, adapter_configured: bool) -> dict:
    """Serialize a :class:`Tenant` as a :class:`TenantDetail`-compatible dict."""
    contact_email = tenant.billing_contact if tenant.billing_contact and "@" in (tenant.billing_contact or "") else None
    default_currency = _resolve_default_currency(tenant.tenant_id)
    embedded = bool(tenant.is_embedded)
    return TenantDetail(
        tenant_id=tenant.tenant_id,
        name=tenant.name,
        subdomain=tenant.subdomain,
        external_org_id=tenant.external_org_id,
        external_source=tenant.external_source,
        # Both fields populated from the same source; ``managed_externally`` is a
        # deprecated alias kept on the wire so existing Storefront callers keep working.
        is_embedded=embedded,
        managed_externally=embedded,
        is_active=bool(tenant.is_active),
        billing_plan=tenant.billing_plan or "standard",
        ad_server=tenant.ad_server,
        adapter_configured=adapter_configured,
        created_at=tenant.created_at,
        contact_email=contact_email,
        default_currency=default_currency,
        public_agent_url=tenant.public_agent_url,
        default_gam_advertiser_id=tenant.default_gam_advertiser_id,
        embed_breadcrumb_root=tenant.embed_breadcrumb_root,
    ).model_dump(mode="json")


def _resolve_default_currency(tenant_id: str) -> str | None:
    """Return the default currency for a tenant, or None if no currency limits exist."""
    with get_db_session() as session:
        stmt = select(CurrencyLimit).filter_by(tenant_id=tenant_id)
        first = session.scalars(stmt).first()
        return first.currency_code if first else None


def _adapter_config_to_dict(adapter: AdapterConfigSchema) -> dict:
    """Flatten the discriminated AdapterConfig into a dict for adapter test/persistence."""
    if isinstance(adapter, GAMAdapterConfig):
        return {
            "type": "google_ad_manager",
            "network_code": adapter.network_code,
            "service_account_email": adapter.service_account_email,
            "service_account_json": adapter.service_account_key_json.get_secret_value(),
            "refresh_token": adapter.refresh_token.get_secret_value() if adapter.refresh_token else None,
        }
    if isinstance(adapter, MockAdapterConfig):
        return {"type": "mock", "dry_run": adapter.dry_run}
    if isinstance(adapter, FreeWheelAdapterConfig):
        return {
            "type": "freewheel",
            "username": adapter.username,
            "password": adapter.password.get_secret_value() if adapter.password else None,
            "api_token": adapter.api_token.get_secret_value() if adapter.api_token else None,
            "environment": adapter.environment,
            "default_advertiser_id": adapter.default_advertiser_id,
        }
    if isinstance(adapter, BroadstreetAdapterConfig):
        return {
            "type": "broadstreet",
            "network_id": adapter.network_id,
            "api_key": adapter.api_key.get_secret_value(),
            "default_advertiser_id": adapter.default_advertiser_id,
        }
    if isinstance(adapter, SpringServeAdapterConfig):
        return {
            "type": "springserve",
            "email": adapter.email,
            "password": adapter.password.get_secret_value() if adapter.password else None,
            "api_token": adapter.api_token.get_secret_value() if adapter.api_token else None,
            "environment": adapter.environment,
            "default_demand_partner_id": adapter.default_demand_partner_id,
        }
    raise ValueError(f"Unsupported adapter type: {type(adapter).__name__}")


def _persist_adapter_config(session, tenant_id: str, adapter: AdapterConfigSchema) -> AdapterConfig:
    """Create or replace the AdapterConfig row for a tenant from a validated schema."""
    stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id)
    existing = session.scalars(stmt).first()
    if existing is not None:
        session.delete(existing)
        session.flush()

    if isinstance(adapter, GAMAdapterConfig):
        # Service-account JSON is required by the schema; refresh_token is optional.
        # Set gam_auth_method to match the credential that's actually present so
        # background sync paths that branch on it (inventory, custom_targeting) don't
        # fall through to the OAuth code path with no refresh token.
        sa_json = adapter.service_account_key_json.get_secret_value() if adapter.service_account_key_json else None
        refresh_token = adapter.refresh_token.get_secret_value() if adapter.refresh_token else None
        auth_method = "service_account" if sa_json else "oauth"
        ac = AdapterConfig(
            tenant_id=tenant_id,
            adapter_type="google_ad_manager",
            gam_network_code=adapter.network_code,
            gam_service_account_email=adapter.service_account_email,
            gam_refresh_token=refresh_token,
            gam_auth_method=auth_method,
        )
        # Encryption is wired via the property setter (see models.py:AdapterConfig).
        if sa_json is not None:
            ac.gam_service_account_json = sa_json
    elif isinstance(adapter, MockAdapterConfig):
        ac = AdapterConfig(
            tenant_id=tenant_id,
            adapter_type="mock",
            mock_dry_run=adapter.dry_run,
        )
    elif isinstance(adapter, FreeWheelAdapterConfig):
        # Round-trip through the adapter's own connection schema so secret
        # encryption (Fernet) lands consistently in config_json — same path
        # the legacy /api/tenant/<id>/adapter-config endpoint takes.
        from src.adapters.freewheel import FreeWheelConnectionConfig

        fw_validated = FreeWheelConnectionConfig(
            username=adapter.username,
            password=adapter.password.get_secret_value() if adapter.password else None,
            api_token=adapter.api_token.get_secret_value() if adapter.api_token else None,
            environment=adapter.environment,
            default_advertiser_id=adapter.default_advertiser_id,
        )
        ac = AdapterConfig(
            tenant_id=tenant_id,
            adapter_type="freewheel",
            config_json=fw_validated.model_dump(),
        )
    elif isinstance(adapter, BroadstreetAdapterConfig):
        from src.adapters.broadstreet.schemas import BroadstreetConnectionConfig

        bs_validated = BroadstreetConnectionConfig(
            network_id=adapter.network_id,
            api_key=adapter.api_key.get_secret_value(),
            default_advertiser_id=adapter.default_advertiser_id,
        )
        ac = AdapterConfig(
            tenant_id=tenant_id,
            adapter_type="broadstreet",
            config_json=bs_validated.model_dump(),
        )
    elif isinstance(adapter, SpringServeAdapterConfig):
        # Same Fernet-encryption round-trip as FreeWheel so secrets land
        # consistently in config_json.
        from src.adapters.springserve import SpringServeConnectionConfig

        ss_validated = SpringServeConnectionConfig(
            email=adapter.email,
            password=adapter.password.get_secret_value() if adapter.password else None,
            api_token=adapter.api_token.get_secret_value() if adapter.api_token else None,
            environment=adapter.environment,
            default_demand_partner_id=adapter.default_demand_partner_id,
        )
        ac = AdapterConfig(
            tenant_id=tenant_id,
            adapter_type="springserve",
            config_json=ss_validated.model_dump(),
        )
    else:
        raise ValueError(f"Unsupported adapter type: {type(adapter).__name__}")
    session.add(ac)
    return ac


def _build_adapter_config_response(adapter: AdapterConfig | None) -> AdapterConfigResponse:
    """Build the redacted :class:`AdapterConfigResponse` from a stored row."""
    if adapter is None:
        return AdapterConfigResponse(type="none", configured=False)
    if adapter.adapter_type == "google_ad_manager":
        return AdapterConfigResponse(
            type="google_ad_manager",
            configured=True,
            network_code=adapter.gam_network_code,
            service_account_email=adapter.gam_service_account_email,
            service_account_key_json="<encrypted>" if adapter._gam_service_account_json else None,
            refresh_token="<redacted>" if adapter.gam_refresh_token else None,
        )
    return AdapterConfigResponse(type=adapter.adapter_type, configured=True)


def _surface_urls(tenant_id: str) -> tuple[str, str, str]:
    """Return ``(mcp_url, a2a_url, admin_url_path)`` for a tenant.

    Only the path component is stable in v1 — the host comes from the deployment env.
    """
    base = os.environ.get("ADCP_BASE_URL", "").rstrip("/")
    mcp = f"{base}/mcp/" if base else "/mcp/"
    a2a = f"{base}/a2a" if base else "/a2a"
    admin_path = f"/tenant/{tenant_id}"
    return mcp, a2a, admin_path


@tenant_management_api.route("/health", methods=["GET"])
@require_tenant_management_api_key
def health_check():
    """Health check endpoint for the tenant management API."""
    return jsonify({"status": "healthy", "timestamp": datetime.now(UTC).isoformat()})


# Display metadata for adapter types. Sourced here rather than from adapter
# classes so the catalog can carry embedder-facing copy without coupling
# every adapter to UX strings. Keys mirror ADAPTER_REGISTRY's canonical
# names (the values that go into AdapterConfig.type).
#
# ``tier="test"`` flags adapters that are simulated/dev-only (Mock). Embedders
# should filter these out of production pickers; default behaviour is to show
# them so dev consoles still see the full set.
_ADAPTER_CATALOG_METADATA: dict[str, dict[str, str]] = {
    "google_ad_manager": {
        "name": "Google Ad Manager",
        "description": "Direct sold inventory via Google Ad Manager — line items, orders, creatives.",
        "tier": "live",
    },
    "mock": {
        "name": "Mock Ad Server",
        "description": "Simulated ad server for testing and development; no real backend calls.",
        "tier": "test",
    },
    "freewheel": {
        "name": "FreeWheel",
        "description": "Video and CTV advertising via Comcast/FreeWheel's Publisher API.",
        "tier": "live",
    },
    "broadstreet": {
        "name": "Broadstreet",
        "description": "Direct sold display and email-newsletter inventory via the Broadstreet Ads API.",
        "tier": "live",
    },
    "springserve": {
        "name": "SpringServe (Magnite)",
        "description": "Direct-sold CTV, online video, and audio inventory via Magnite's SpringServe ad server.",
        "tier": "live",
    },
}

# Map from ADAPTER_REGISTRY key → the typed AdapterConfig member whose
# JSON Schema describes the connection payload for that adapter.
_ADAPTER_CONFIG_TYPED = {
    "google_ad_manager": GAMAdapterConfig,
    "mock": MockAdapterConfig,
    "freewheel": FreeWheelAdapterConfig,
    "broadstreet": BroadstreetAdapterConfig,
    "springserve": SpringServeAdapterConfig,
}


@tenant_management_api.route("/adapters", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=ListAdaptersResponse, HTTP_500=ApiError))
def list_adapters():
    """Return the full catalog of supported ad-server adapter types.

    Embedder clients (Scope3 storefront, etc.) call this to discover what
    adapters this Sales Agent instance supports. Returns one entry per
    adapter type that has a typed AdapterConfig member — covering the
    full set surfaced to operators in the tenant settings UI.

    Each entry carries:
      - ``type`` — the value that goes into ``AdapterConfig.type``
      - ``name`` / ``description`` — human-readable display strings
      - ``tier`` — ``"live"`` for production adapters, ``"test"`` for
        simulated/dev-only adapters (Mock). Embedders should filter
        ``tier="test"`` out of production pickers.
      - ``default_channels`` — channels this adapter is primarily used for
      - ``capabilities`` — static AdapterCapabilities flags
      - ``connection_schema`` — JSON Schema for the typed connection payload

    Optional query params:
      - ``tier=live`` to return only production-grade adapters (omit
        Mock). Useful for production storefronts that should never offer
        a simulated picker option.
    """
    from src.adapters import ADAPTER_REGISTRY

    tier_filter = request.args.get("tier")
    if tier_filter is not None and tier_filter not in ("live", "test"):
        return jsonify({"error": "invalid_tier", "message": "tier must be 'live' or 'test'"}), 400

    seen_types: set[str] = set()
    entries: list[AdapterCatalogEntry] = []

    # ADAPTER_REGISTRY has multiple aliases per adapter class (e.g. "gam"
    # and "google_ad_manager"). Dedupe via the registered class identity
    # so each adapter appears once, keyed by its canonical name (the one
    # present in _ADAPTER_CATALOG_METADATA).
    for registry_key, adapter_class in ADAPTER_REGISTRY.items():
        if registry_key not in _ADAPTER_CATALOG_METADATA:
            continue
        if registry_key in seen_types:
            continue
        seen_types.add(registry_key)

        metadata = _ADAPTER_CATALOG_METADATA[registry_key]
        tier = metadata.get("tier", "live")
        if tier_filter is not None and tier != tier_filter:
            continue
        caps_dataclass = getattr(adapter_class, "capabilities", None)
        capabilities_summary = (
            AdapterCapabilitiesSummary(
                supports_inventory_sync=caps_dataclass.supports_inventory_sync,
                supports_inventory_profiles=caps_dataclass.supports_inventory_profiles,
                inventory_entity_label=caps_dataclass.inventory_entity_label,
                supports_custom_targeting=caps_dataclass.supports_custom_targeting,
                supports_geo_targeting=caps_dataclass.supports_geo_targeting,
                supports_dynamic_products=caps_dataclass.supports_dynamic_products,
                supported_pricing_models=list(caps_dataclass.supported_pricing_models or []),
                supports_webhooks=caps_dataclass.supports_webhooks,
                supports_realtime_reporting=caps_dataclass.supports_realtime_reporting,
            )
            if caps_dataclass is not None
            else AdapterCapabilitiesSummary()
        )

        typed_config = _ADAPTER_CONFIG_TYPED.get(registry_key)
        connection_schema = typed_config.model_json_schema() if typed_config else {}

        entries.append(
            AdapterCatalogEntry(
                type=registry_key,
                name=metadata["name"],
                description=metadata["description"],
                tier=tier,
                default_channels=list(getattr(adapter_class, "default_channels", []) or []),
                capabilities=capabilities_summary,
                connection_schema=connection_schema,
            )
        )

    entries.sort(key=lambda e: e.type)
    return jsonify(ListAdaptersResponse(adapters=entries, count=len(entries)).model_dump())


@tenant_management_api.route("/tenants", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=ListTenantsResponse, HTTP_500=ApiError))
def list_tenants():
    """List tenants. Optional query params: ``is_embedded`` (or deprecated ``managed_externally``), ``is_active``, ``external_source``."""
    # ``managed_externally`` query-param kept as deprecated alias for Storefront.
    embedded_filter = request.args.get("is_embedded") or request.args.get("managed_externally")
    active_filter = request.args.get("is_active")
    source_filter = request.args.get("external_source")

    def _to_bool(value: str | None) -> bool | None:
        if value is None:
            return None
        return value.lower() in ("true", "1", "yes")

    with get_db_session() as db_session:
        stmt = select(Tenant).order_by(Tenant.created_at.desc())
        embedded_bool = _to_bool(embedded_filter)
        if embedded_bool is not None:
            stmt = stmt.filter(Tenant.is_embedded.is_(embedded_bool))
        active_bool = _to_bool(active_filter)
        if active_bool is not None:
            stmt = stmt.filter(Tenant.is_active.is_(active_bool))
        if source_filter:
            stmt = stmt.filter(Tenant.external_source == source_filter)
        tenants = db_session.scalars(stmt).all()

        # Adapter-configured probe via a separate cheap query keeps the main filter simple.
        configured_ids = set(db_session.scalars(select(AdapterConfig.tenant_id)).all())

        summaries = [_tenant_to_summary(t, t.tenant_id in configured_ids) for t in tenants]
        return jsonify({"tenants": summaries, "count": len(summaries)})


@tenant_management_api.route("/tenants", methods=["POST"])
@require_tenant_management_api_key
def create_tenant():
    """Create a new tenant."""

    from src.core.database.models import AdapterConfig

    with get_db_session() as db_session:
        try:
            from src.core.webhook_validator import WebhookURLValidator

            data = request.get_json()

            # Validate required fields
            required_fields = ["name", "subdomain", "ad_server"]
            for field in required_fields:
                if field not in data:
                    return jsonify({"error": f"Missing required field: {field}"}), 400

            # Reject any ad_server not in ADAPTER_REGISTRY so deregistered
            # adapters (currently Triton — APIs not production-ready) can't
            # create tenants via the legacy non-spectree path. The spectree
            # POST /tenants/provision is already gated by the typed
            # AdapterConfig discriminated union.
            from src.adapters import ADAPTER_REGISTRY

            if data["ad_server"] not in ADAPTER_REGISTRY:
                return (
                    jsonify(
                        {
                            "error": f"Unsupported ad_server: {data['ad_server']!r}",
                            "supported": sorted(k for k in ADAPTER_REGISTRY if k != "creative_engine"),
                        }
                    ),
                    400,
                )

            # Validate webhook URLs for SSRF protection
            webhook_fields = {
                "slack_webhook_url": "Slack webhook URL",
                "slack_audit_webhook_url": "Slack audit webhook URL",
                "hitl_webhook_url": "HITL webhook URL",
            }
            for field_name, field_label in webhook_fields.items():
                url = data.get(field_name)
                if url:
                    is_valid, error_msg = WebhookURLValidator.validate_webhook_url(url)
                    if not is_valid:
                        return jsonify({"error": f"Invalid {field_label}: {error_msg}"}), 400

            # Generate tenant ID
            tenant_id = f"tenant_{uuid.uuid4().hex[:8]}"
            admin_token = secrets.token_urlsafe(32)

            # Handle authorized emails - automatically add creator's email
            email_list = data.get("authorized_emails", [])
            creator_email = data.get("creator_email")
            if creator_email and creator_email not in email_list:
                email_list.append(creator_email)

            domain_list = data.get("authorized_domains", [])

            # Validate access control - prevent tenant lockout
            if not email_list and not domain_list:
                if creator_email:
                    # Auto-add creator as fallback with warning
                    email_list.append(creator_email)
                    logger.warning(
                        f"No access control specified for tenant {data['name']}, auto-adding creator {creator_email}"
                    )
                else:
                    return (
                        jsonify(
                            {
                                "error": "Must specify at least one authorized email or domain. "
                                "Provide 'authorized_emails', 'authorized_domains', or 'creator_email'."
                            }
                        ),
                        400,
                    )

            # Create tenant
            new_tenant = Tenant(
                tenant_id=tenant_id,
                name=data["name"],
                subdomain=data["subdomain"],
                ad_server=data["ad_server"],
                is_active=data.get("is_active", True),
                billing_plan=data.get("billing_plan", "standard"),
                billing_contact=data.get("billing_contact"),
                # Note: max_daily_budget moved to currency_limits table (per models.py line 55)
                enable_axe_signals=data.get("enable_axe_signals", True),
                authorized_emails=json.dumps(email_list),
                authorized_domains=json.dumps(domain_list),
                slack_webhook_url=data.get("slack_webhook_url"),
                slack_audit_webhook_url=data.get("slack_audit_webhook_url"),
                hitl_webhook_url=data.get("hitl_webhook_url"),
                admin_token=admin_token,
                auto_approve_format_ids=json.dumps(data.get("auto_approve_format_ids", ["display_300x250"])),
                human_review_required=data.get("human_review_required", True),
                policy_settings=json.dumps(data.get("policy_settings", {})),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                # Set default measurement provider (Publisher Ad Server)
                measurement_providers={"providers": ["Publisher Ad Server"], "default": "Publisher Ad Server"},
            )
            db_session.add(new_tenant)

            # Create adapter config
            adapter_type = data["ad_server"]

            # Insert adapter config with appropriate fields based on type
            if adapter_type == "google_ad_manager":
                new_adapter = AdapterConfig(
                    tenant_id=tenant_id,
                    adapter_type=adapter_type,
                    gam_network_code=data.get("gam_network_code"),
                    gam_refresh_token=data.get("gam_refresh_token"),
                    gam_trafficker_id=data.get("gam_trafficker_id"),
                    gam_manual_approval_required=data.get("gam_manual_approval_required", False),
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
                # NOTE: gam_company_id removed - advertiser_id is per-principal in platform_mappings
            elif adapter_type == "freewheel":
                # Validate FreeWheel credentials through FreeWheelConnectionConfig.
                # Reject submitted ciphertext on any secret field
                # (cross-tenant smuggling defence).
                from src.adapters.freewheel import FreeWheelConnectionConfig
                from src.core.utils.encryption import is_encrypted

                for secret_field in ("password", "api_token"):
                    if data.get(secret_field) and is_encrypted(data[secret_field]):
                        return (
                            jsonify({"error": f"{secret_field} must be plaintext (encrypted-token replay rejected)"}),
                            400,
                        )
                fw_payload = {
                    k: data[k]
                    for k in (
                        "username",
                        "password",
                        "api_token",
                        "environment",
                        "default_advertiser_id",
                    )
                    if k in data
                }
                validated = FreeWheelConnectionConfig(**fw_payload)
                new_adapter = AdapterConfig(
                    tenant_id=tenant_id,
                    adapter_type=adapter_type,
                    config_json=validated.model_dump(),
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            else:  # mock or other
                new_adapter = AdapterConfig(
                    tenant_id=tenant_id,
                    adapter_type=adapter_type,
                    mock_dry_run=data.get("mock_dry_run", False),
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )

            db_session.add(new_adapter)

            # Create default principal if requested
            principal_token = None
            if data.get("create_default_principal", True):
                principal_id = f"principal_{uuid.uuid4().hex[:8]}"
                principal_token = secrets.token_urlsafe(32)

                # Add a default platform mapping based on the adapter type
                default_mappings = {}
                if adapter_type == "google_ad_manager":
                    # For GAM, add a placeholder advertiser ID
                    default_mappings = {"google_ad_manager": {"advertiser_id": "placeholder"}}
                elif adapter_type == "freewheel":
                    default_mappings = {"freewheel": {"advertiser_id": "placeholder"}}
                else:
                    # For mock and others
                    default_mappings = {"mock": {"advertiser_id": "default"}}

                new_principal = Principal(
                    tenant_id=tenant_id,
                    principal_id=principal_id,
                    name=f"{data['name']} Default Principal",
                    platform_mappings=json.dumps(default_mappings),
                    access_token=principal_token,
                    created_at=datetime.now(UTC),
                )
                db_session.add(new_principal)

            db_session.commit()

            result = {
                "tenant_id": tenant_id,
                "name": data["name"],
                "subdomain": data["subdomain"],
                "admin_token": admin_token,
                "admin_ui_url": (
                    f"http://{data['subdomain']}.localhost:{os.environ.get('ADCP_SALES_PORT', '8080')}"
                    f"/admin/tenant/{tenant_id}"
                ),
            }

            if principal_token:
                result["default_principal_token"] = principal_token

            return jsonify(result), 201

        except Exception as e:
            db_session.rollback()
            if "UNIQUE constraint failed: tenants.subdomain" in str(e):
                return jsonify({"error": "Subdomain already exists"}), 409
            logger.error(f"Error creating tenant: {str(e)}")
            return jsonify({"error": "Failed to create tenant"}), 500


@tenant_management_api.route("/tenants/<tenant_id>", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=TenantDetail, HTTP_404=ApiError))
def get_tenant(tenant_id):
    """Return :class:`TenantDetail` for a tenant. 404 if the tenant doesn't exist."""
    with get_db_session() as db_session:
        stmt = select(Tenant).filter_by(tenant_id=tenant_id)
        tenant = db_session.scalars(stmt).first()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        adapter_stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id)
        adapter = db_session.scalars(adapter_stmt).first()
        return jsonify(_tenant_to_detail(tenant, adapter is not None))


@tenant_management_api.route("/tenants/<tenant_id>", methods=["PUT"])
@require_tenant_management_api_key
def update_tenant(tenant_id):
    """Update a tenant."""
    with get_db_session() as db_session:
        try:
            # Check if tenant exists
            stmt = select(Tenant).filter_by(tenant_id=tenant_id)
            tenant = db_session.scalars(stmt).first()
            if not tenant:
                return jsonify({"error": "Tenant not found"}), 404

            from src.core.webhook_validator import WebhookURLValidator

            data = request.get_json()

            # Validate webhook URLs before updating for SSRF protection
            webhook_fields = {
                "slack_webhook_url": "Slack webhook URL",
                "slack_audit_webhook_url": "Slack audit webhook URL",
                "hitl_webhook_url": "HITL webhook URL",
            }
            for field_name, field_label in webhook_fields.items():
                if field_name in data and data[field_name]:
                    is_valid, error_msg = WebhookURLValidator.validate_webhook_url(data[field_name])
                    if not is_valid:
                        return jsonify({"error": f"Invalid {field_label}: {error_msg}"}), 400

            # Update fields based on provided data
            if "name" in data:
                tenant.name = data["name"]
            if "is_active" in data:
                tenant.is_active = data["is_active"]
            if "billing_plan" in data:
                tenant.billing_plan = data["billing_plan"]
            if "billing_contact" in data:
                tenant.billing_contact = data["billing_contact"]
            # Note: max_daily_budget moved to currency_limits table (per models.py line 55)
            if "enable_axe_signals" in data:
                tenant.enable_axe_signals = data["enable_axe_signals"]
            if "authorized_emails" in data:
                tenant.authorized_emails = json.dumps(data["authorized_emails"])
            if "authorized_domains" in data:
                tenant.authorized_domains = json.dumps(data["authorized_domains"])
            if "slack_webhook_url" in data:
                tenant.slack_webhook_url = data["slack_webhook_url"]
            if "slack_audit_webhook_url" in data:
                tenant.slack_audit_webhook_url = data["slack_audit_webhook_url"]
            if "hitl_webhook_url" in data:
                tenant.hitl_webhook_url = data["hitl_webhook_url"]
            if "auto_approve_format_ids" in data:
                tenant.auto_approve_format_ids = json.dumps(data["auto_approve_format_ids"])
            if "human_review_required" in data:
                tenant.human_review_required = data["human_review_required"]
            if "policy_settings" in data:
                tenant.policy_settings = json.dumps(data["policy_settings"])

            # Always update the updated_at timestamp
            tenant.updated_at = datetime.now(UTC)

            # Update adapter config if provided
            if "adapter_config" in data:
                adapter_data = data["adapter_config"]

                # Get current adapter
                stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id)
                adapter = db_session.scalars(stmt).first()

                if adapter:
                    if adapter.adapter_type == "google_ad_manager":
                        if "gam_network_code" in adapter_data:
                            adapter.gam_network_code = adapter_data["gam_network_code"]
                        if "gam_refresh_token" in adapter_data:
                            adapter.gam_refresh_token = adapter_data["gam_refresh_token"]
                        # NOTE: gam_company_id removed - advertiser_id is per-principal in platform_mappings
                        if "gam_trafficker_id" in adapter_data:
                            adapter.gam_trafficker_id = adapter_data["gam_trafficker_id"]
                        if "gam_manual_approval_required" in adapter_data:
                            adapter.gam_manual_approval_required = adapter_data["gam_manual_approval_required"]

                    elif adapter.adapter_type in {"triton", "triton_digital"}:
                        # Reject submitted ciphertext (M1/S7: cross-tenant smuggling).
                        from src.adapters.triton import TritonConnectionConfig
                        from src.core.utils.encryption import is_encrypted

                        if adapter_data.get("password") and is_encrypted(adapter_data["password"]):
                            return (
                                jsonify({"error": "password must be plaintext (encrypted-token replay rejected)"}),
                                400,
                            )
                        merged = dict(adapter.config_json or {})
                        for field_name in (
                            "auth_type",
                            "username",
                            "password",
                            "base_url",
                            "login_url",
                            "default_advertiser_id",
                        ):
                            if field_name in adapter_data:
                                merged[field_name] = adapter_data[field_name]
                        validated = TritonConnectionConfig(**merged)
                        adapter.config_json = validated.model_dump()
                        attributes.flag_modified(adapter, "config_json")

                    elif adapter.adapter_type == "freewheel":
                        # Reject submitted ciphertext on any secret field
                        # (M1/S7: cross-tenant smuggling defence).
                        from src.adapters.freewheel import FreeWheelConnectionConfig
                        from src.core.utils.encryption import is_encrypted

                        for secret_field in ("password", "api_token"):
                            if adapter_data.get(secret_field) and is_encrypted(adapter_data[secret_field]):
                                return (
                                    jsonify(
                                        {"error": f"{secret_field} must be plaintext (encrypted-token replay rejected)"}
                                    ),
                                    400,
                                )
                        merged = dict(adapter.config_json or {})
                        for field_name in (
                            "username",
                            "password",
                            "api_token",
                            "environment",
                            "default_advertiser_id",
                        ):
                            if field_name in adapter_data:
                                merged[field_name] = adapter_data[field_name]
                        validated = FreeWheelConnectionConfig(**merged)
                        adapter.config_json = validated.model_dump()
                        attributes.flag_modified(adapter, "config_json")

                    elif adapter.adapter_type == "mock":
                        if "mock_dry_run" in adapter_data:
                            adapter.mock_dry_run = adapter_data["mock_dry_run"]

                    adapter.updated_at = datetime.now(UTC)

            db_session.commit()

            return jsonify(
                {
                    "tenant_id": tenant_id,
                    "name": tenant.name,
                    "updated_at": tenant.updated_at.isoformat() if tenant.updated_at else None,
                }
            )

        except Exception as e:
            db_session.rollback()
            logger.error(f"Error updating tenant {tenant_id}: {str(e)}")
            return jsonify({"error": f"Failed to update tenant: {str(e)}"}), 500


@tenant_management_api.route("/tenants/<tenant_id>", methods=["DELETE"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=TenantDetail, HTTP_404=ApiError, HTTP_409=ApiError, HTTP_400=ApiError))
def delete_tenant(tenant_id):
    """Soft-delete a tenant by default. Hard-delete requires ``?hard=true`` and ``X-Confirm-Delete: yes``.

    Returns 409 ``tenant_has_active_resources`` if the tenant has any active media buys.
    """
    hard = request.args.get("hard", "false").lower() in ("true", "1", "yes")

    with get_db_session() as db_session:
        db_session.info["management_api_caller"] = True

        stmt = select(Tenant).filter_by(tenant_id=tenant_id)
        tenant = db_session.scalars(stmt).first()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        # Active-resources guard fires for both soft and hard delete: a tenant with live buys
        # should not be flipped inactive without an explicit policy decision upstream.
        active_count = db_session.scalar(
            select(func.count())
            .select_from(MediaBuy)
            .where(MediaBuy.tenant_id == tenant_id, MediaBuy.status.in_(("active", "live", "running")))
        )
        if active_count and active_count > 0:
            return _api_error(
                "tenant_has_active_resources",
                f"Tenant {tenant_id!r} has {active_count} active media buys",
                409,
                details={"active_media_buys": int(active_count)},
            )

        if hard:
            confirm = request.headers.get("X-Confirm-Delete", "").lower()
            if confirm != "yes":
                return _api_error(
                    "confirmation_required",
                    "Hard delete requires X-Confirm-Delete: yes header",
                    400,
                )
            tenant_detail = _tenant_to_detail(tenant, adapter_configured=False)
            # Hard delete relies on Tenant's ``cascade="all, delete-orphan"`` relationships
            # for most child tables. PropertyTag uses a backref without a delete cascade,
            # so wipe its rows first via the FK ON DELETE rule. Issuing the bulk delete
            # explicitly avoids the unit-of-work attempting to NULL composite-PK columns.
            db_session.execute(delete(PropertyTag).where(PropertyTag.tenant_id == tenant_id))
            db_session.delete(tenant)
            db_session.commit()
            return jsonify(tenant_detail)

        tenant.is_active = False
        tenant.updated_at = datetime.now(UTC)
        adapter_present = db_session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant_id)).first() is not None
        try:
            db_session.commit()
        except EmbeddedTenantWriteError as exc:
            db_session.rollback()
            return _api_error("managed_tenant_write_blocked", str(exc), 403)
        return jsonify(_tenant_to_detail(tenant, adapter_present))


# ---------------------------------------------------------------------------
# Sprint 1 endpoints (managed-tenant mode)
# ---------------------------------------------------------------------------


@tenant_management_api.route("/tenants/provision", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(
    json=ProvisionTenantRequest,
    resp=Response(
        HTTP_201=ProvisionTenantResponse,
        HTTP_400=ApiError,
        HTTP_409=ApiError,
        HTTP_500=ApiError,
    ),
)
def provision_tenant():
    """Provision a managed tenant (one-shot create + adapter + currency + property tag + optional principal)."""
    req: ProvisionTenantRequest = request.context.json

    # Step 1: external_org_id uniqueness check (informational — not unique at DB level today).
    with get_db_session() as preflight:
        existing = preflight.scalars(
            select(Tenant).filter_by(external_org_id=req.external_org_id, external_source=req.external_source)
        ).first()
        if existing is not None:
            return _api_error(
                "external_org_id_conflict",
                f"external_org_id {req.external_org_id!r} already maps to tenant {existing.tenant_id!r}",
                409,
                details={"tenant_id": existing.tenant_id},
            )

    # Step 2a: validate public_agent_url's hostname is a platform-managed
    # serving host. Embedded provisions all live under the platform's shared
    # host (interchange.io by default, configurable via
    # ``EMBEDDED_PLATFORM_AGENT_HOSTS``). Fail closed BEFORE we touch the DB
    # so a bad URL never ends up persisted.
    from src.services.aao_lookup_service import (
        PublicAgentUrlMismatch,
        validate_public_agent_url_hostname,
    )

    try:
        validate_public_agent_url_hostname(
            req.public_agent_url,
            is_embedded=True,
            virtual_host=None,
            subdomain=None,
            sales_agent_domain=None,
        )
    except PublicAgentUrlMismatch as exc:
        return _api_error("public_agent_url_mismatch", str(exc), 422)

    # Step 2b: probe the adapter BEFORE writing anything. A failure here means we never
    # touch the DB at all — keeps the table free of half-configured tenants.
    adapter_dict = _adapter_config_to_dict(req.adapter)
    probe = probe_adapter_connection(adapter_dict["type"], adapter_dict)
    if not probe.success:
        return _adapter_probe_error(adapter_dict["type"], probe)

    # Step 3: open a transaction; create everything in one commit.
    tenant_id = f"tenant_{uuid.uuid4().hex[:8]}"
    subdomain_seed = req.external_org_id.lower().replace("_", "-")
    subdomain = f"{subdomain_seed}-{tenant_id[-8:]}"

    initial_principal_id: str | None = None
    initial_principal_name: str | None = None
    initial_principal_token: str | None = None

    with get_db_session() as session:
        session.info["management_api_caller"] = True

        new_tenant = Tenant(
            tenant_id=tenant_id,
            name=req.name,
            subdomain=subdomain,
            ad_server=adapter_dict["type"],
            is_active=True,
            billing_plan=req.billing_plan,
            billing_contact=req.contact_email,
            is_embedded=True,
            external_org_id=req.external_org_id,
            external_source=req.external_source,
            public_agent_url=req.public_agent_url,
            default_gam_advertiser_id=req.default_gam_advertiser_id,
            embed_breadcrumb_root=(
                req.embed_breadcrumb_root.model_dump() if req.embed_breadcrumb_root is not None else None
            ),
            authorized_emails=[req.contact_email],
            authorized_domains=[],
            human_review_required=True,
            auto_approve_format_ids=[],
            measurement_providers={"providers": ["Publisher Ad Server"], "default": "Publisher Ad Server"},
        )
        session.add(new_tenant)
        session.flush()

        _persist_adapter_config(session, tenant_id, req.adapter)

        # Default CurrencyLimit (USD or override).
        session.add(
            CurrencyLimit(
                tenant_id=tenant_id,
                currency_code=req.default_currency,
                min_package_budget=None,
                max_daily_package_spend=None,
            )
        )

        # Default PropertyTag — products that don't pin specific properties default to all_inventory.
        session.add(
            PropertyTag(
                tenant_id=tenant_id,
                tag_id="all_inventory",
                name="All Inventory",
                description="Default property tag for all inventory",
            )
        )

        if req.initial_principal is not None:
            initial_principal_id = f"principal_{uuid.uuid4().hex[:8]}"
            initial_principal_name = req.initial_principal.name
            platform_mappings: dict[str, dict] = {}
            if adapter_dict["type"] == "google_ad_manager":
                advertiser = req.initial_principal.external_advertiser_id or "placeholder"
                platform_mappings = {"google_ad_manager": {"advertiser_id": advertiser}}
            elif adapter_dict["type"] == "mock":
                platform_mappings = {
                    "mock": {"advertiser_id": req.initial_principal.external_advertiser_id or "default"}
                }

            # Mint the principal's access_token here and return it in the
            # provision response. ``Principal.access_token`` is what the
            # buyer-protocol auth chain looks up on ``x-adcp-auth`` — a host
            # product that holds this token can authenticate buyer-protocol
            # calls without depending on the (still-pending) sprint 2
            # identity-propagation middleware. The ``embedded-mode-no-token:``
            # prefix is retained so any code path that prefix-checks the token
            # to distinguish embedded principals from open-instance principals
            # continues to work; the prefix is informational, not a guard —
            # the salesagent's auth lookup is pure string equality.
            initial_principal_token = f"embedded-mode-no-token:{secrets.token_urlsafe(8)}"
            session.add(
                Principal(
                    tenant_id=tenant_id,
                    principal_id=initial_principal_id,
                    name=initial_principal_name,
                    platform_mappings=platform_mappings,
                    access_token=initial_principal_token,
                )
            )

        try:
            session.commit()
        except EmbeddedTenantWriteError as exc:
            session.rollback()
            return _api_error("managed_tenant_write_blocked", str(exc), 403)
        except Exception as exc:
            session.rollback()
            logger.exception("Provision failed")
            return _api_error("internal_error", f"Provision failed: {exc}", 500)

        # Pull updated_at/created_at after commit so the response is accurate.
        session.refresh(new_tenant)
        created_at = new_tenant.created_at

    # Emit principal.created so the host product can pick up the initial
    # advertiser without polling. Fires only when an initial_principal was
    # part of the provision request — open-instance flows that defer
    # principal creation will fire from the standalone create endpoint.
    if initial_principal_id and initial_principal_name:
        from src.admin.services.webhook_publisher import emit_event

        emit_event(
            tenant_id,
            "principal.created",
            {"principal_id": initial_principal_id, "name": initial_principal_name},
        )

    # First inventory sync runs as a side effect of provisioning, not a
    # gate on it. Provision is binary: credentials validated upstream
    # (Step 2b), tenant rows committed, response returns. Inventory sync
    # state lives in the salesagent UI from this point on — the publisher
    # logs in and sees current sync progress on the dashboard. No handles
    # are surfaced to the caller; we are not inviting polling.
    try:
        _create_and_spawn_refresh(
            tenant_id=tenant_id,
            triggered_by_id="tenant_management_api:provision",
        )
    except Exception:
        logger.exception(
            "[provision] first-sync kickoff failed for tenant=%s — "
            "tenant is still provisioned; next /refresh or cron tick will sync",
            tenant_id,
        )

    mcp_url, a2a_url, admin_url_path = _surface_urls(tenant_id)
    response = ProvisionTenantResponse(
        tenant_id=tenant_id,
        name=req.name,
        external_org_id=req.external_org_id,
        external_source=req.external_source,
        # ``managed_externally`` retained as deprecated alias for Storefront.
        is_embedded=True,
        managed_externally=True,
        created_at=created_at,
        mcp_url=mcp_url,
        a2a_url=a2a_url,
        admin_url_path=admin_url_path,
        adapter=AdapterStatusResponse(
            type=adapter_dict["type"],
            configured=True,
            connection_test_passed=True,
            connection_test_error=None,
        ),
        initial_principal=(
            ProvisionedPrincipalResponse(
                principal_id=initial_principal_id,
                name=initial_principal_name,
                access_token=initial_principal_token,
            )
            if initial_principal_id and initial_principal_name and initial_principal_token
            else None
        ),
    )
    return jsonify(response.model_dump(mode="json")), 201


@tenant_management_api.route("/tenants/preview-adapter", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(
    json=PreviewAdapterRequest,
    resp=Response(HTTP_200=PreviewAdapterResponse, HTTP_500=ApiError),
)
def preview_adapter_endpoint():
    """Probe an adapter and return network metadata — no persistence.

    Lets the Storefront UI confirm an adapter grant + auto-fill currency
    and timezone before committing to a tenant. Bad creds return 200 with
    ``ok=false`` (renders inline) — only malformed bodies / missing API key
    surface as 4xx via the normal middleware path.
    """
    req: PreviewAdapterRequest = request.context.json
    adapter_dict = _adapter_config_to_dict(req.adapter)
    preview = preview_adapter(adapter_dict["type"], adapter_dict)
    response = PreviewAdapterResponse(
        ok=preview.ok,
        network_name=preview.network_name,
        network_code=preview.network_code,
        currency_code=preview.currency_code,
        time_zone=preview.time_zone,
        inventory_reachable=preview.inventory_reachable,
        error=preview.error,
        error_code=preview.error_code,
        remediation=preview.remediation,
        details=preview.details or None,
    )
    return jsonify(response.model_dump(mode="json"))


@tenant_management_api.route("/tenants/<tenant_id>", methods=["PATCH"])
@require_tenant_management_api_key
@spec.validate(
    json=UpdateTenantRequest,
    resp=Response(HTTP_200=TenantDetail, HTTP_404=ApiError, HTTP_400=ApiError),
)
def patch_tenant(tenant_id: str):
    """Update platform-managed fields on a tenant (PATCH semantics — only listed fields are touched)."""
    req: UpdateTenantRequest = request.context.json  # type: ignore[attr-defined]

    with get_db_session() as session:
        session.info["management_api_caller"] = True

        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        if req.name is not None:
            tenant.name = req.name
        if req.contact_email is not None:
            tenant.billing_contact = req.contact_email
        if req.billing_plan is not None:
            tenant.billing_plan = req.billing_plan
        if req.public_agent_url is not None:
            from src.core.domain_config import get_sales_agent_domain
            from src.services.aao_lookup_service import (
                PublicAgentUrlMismatch,
                validate_public_agent_url_hostname,
            )

            try:
                validate_public_agent_url_hostname(
                    req.public_agent_url,
                    is_embedded=bool(tenant.is_embedded),
                    virtual_host=tenant.virtual_host,
                    subdomain=tenant.subdomain,
                    sales_agent_domain=get_sales_agent_domain(),
                )
            except PublicAgentUrlMismatch as exc:
                session.rollback()
                return _api_error("public_agent_url_mismatch", str(exc), 422)
            tenant.public_agent_url = req.public_agent_url
        if req.default_gam_advertiser_id is not None:
            tenant.default_gam_advertiser_id = req.default_gam_advertiser_id
        if req.embed_breadcrumb_root is not None:
            tenant.embed_breadcrumb_root = req.embed_breadcrumb_root.model_dump()
        tenant.updated_at = datetime.now(UTC)

        try:
            session.commit()
        except EmbeddedTenantWriteError as exc:
            session.rollback()
            return _api_error("managed_tenant_write_blocked", str(exc), 403)

        adapter_present = session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant_id)).first() is not None
        invalidate_status_cache(tenant_id)
        return jsonify(_tenant_to_detail(tenant, adapter_present))


@tenant_management_api.route("/tenants/<tenant_id>/deactivate", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=TenantDetail, HTTP_404=ApiError))
def deactivate_tenant(tenant_id: str):
    """Idempotently deactivate a tenant. Calling on an already-inactive tenant is a no-op."""
    with get_db_session() as session:
        session.info["management_api_caller"] = True

        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        if tenant.is_active:
            tenant.is_active = False
            tenant.updated_at = datetime.now(UTC)
            try:
                session.commit()
            except EmbeddedTenantWriteError as exc:
                session.rollback()
                return _api_error("managed_tenant_write_blocked", str(exc), 403)
            invalidate_status_cache(tenant_id)

        adapter_present = session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant_id)).first() is not None
        return jsonify(_tenant_to_detail(tenant, adapter_present))


@tenant_management_api.route("/tenants/<tenant_id>/reactivate", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=TenantDetail, HTTP_404=ApiError))
def reactivate_tenant(tenant_id: str):
    """Idempotently reactivate a tenant."""
    with get_db_session() as session:
        session.info["management_api_caller"] = True

        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        if not tenant.is_active:
            tenant.is_active = True
            tenant.updated_at = datetime.now(UTC)
            try:
                session.commit()
            except EmbeddedTenantWriteError as exc:
                session.rollback()
                return _api_error("managed_tenant_write_blocked", str(exc), 403)
            invalidate_status_cache(tenant_id)

        adapter_present = session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant_id)).first() is not None
        return jsonify(_tenant_to_detail(tenant, adapter_present))


@tenant_management_api.route("/tenants/<tenant_id>/adapter-config", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=AdapterConfigResponse, HTTP_404=ApiError))
def get_adapter_config(tenant_id: str):
    """Return the tenant's adapter config with secrets redacted."""
    with get_db_session() as session:
        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        adapter = session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant_id)).first()
        return jsonify(_build_adapter_config_response(adapter).model_dump(mode="json"))


def _adapter_request_schema():
    """Adapter-config PUT body uses the same discriminated union as provision."""
    # Wrapper class so spectree can attach the discriminator on the JSON root.
    from pydantic import RootModel

    class AdapterConfigEnvelope(RootModel[AdapterConfigSchema]):
        model_config = {"arbitrary_types_allowed": True}

    return AdapterConfigEnvelope


_ADAPTER_PUT_SCHEMA = _adapter_request_schema()


@tenant_management_api.route("/tenants/<tenant_id>/adapter-config", methods=["PUT"])
@require_tenant_management_api_key
@spec.validate(
    json=_ADAPTER_PUT_SCHEMA,
    resp=Response(HTTP_200=AdapterConfigResponse, HTTP_400=ApiError, HTTP_404=ApiError),
)
def put_adapter_config(tenant_id: str):
    """Replace the tenant's adapter config. Tests the connection before commit."""
    body = request.context.json  # type: ignore[attr-defined]
    adapter_schema: AdapterConfigSchema = body.root
    adapter_dict = _adapter_config_to_dict(adapter_schema)

    probe = probe_adapter_connection(adapter_dict["type"], adapter_dict)
    if not probe.success:
        return _adapter_probe_error(adapter_dict["type"], probe)

    with get_db_session() as session:
        session.info["management_api_caller"] = True

        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        new_adapter = _persist_adapter_config(session, tenant_id, adapter_schema)
        try:
            session.commit()
        except EmbeddedTenantWriteError as exc:
            session.rollback()
            return _api_error("managed_tenant_write_blocked", str(exc), 403)
        session.refresh(new_adapter)
        invalidate_status_cache(tenant_id)
        return jsonify(_build_adapter_config_response(new_adapter).model_dump(mode="json"))


@tenant_management_api.route("/tenants/<tenant_id>/adapter-config/test-connection", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=TestConnectionResponse, HTTP_404=ApiError))
def adapter_test_connection(tenant_id: str):
    """Probe the saved adapter config without modifying state."""
    with get_db_session() as session:
        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        adapter = session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant_id)).first()
        if adapter is None:
            return jsonify(
                TestConnectionResponse(
                    success=False, error="No adapter configured", tested_at=datetime.now(UTC)
                ).model_dump(mode="json")
            )

        config: dict = {}
        if adapter.adapter_type == "google_ad_manager":
            config = {
                "network_code": adapter.gam_network_code,
                "service_account_json": adapter.gam_service_account_json,
                "refresh_token": adapter.gam_refresh_token,
            }
        elif adapter.adapter_type == "mock":
            config = {"dry_run": bool(adapter.mock_dry_run)}

        probe = probe_adapter_connection(adapter.adapter_type, config)
        invalidate_status_cache(tenant_id)
        return jsonify(
            TestConnectionResponse(
                success=probe.success,
                error=probe.error_message,
                error_code=probe.error_code,
                remediation=probe.remediation,
                details=probe.details or None,
                tested_at=datetime.now(UTC),
            ).model_dump(mode="json")
        )


# ---------------------------------------------------------------------------
# Sprint 1.6 — pre-map advertisers
# ---------------------------------------------------------------------------


_ACCOUNT_GAM_KEY = "google_ad_manager"


def _account_advertiser_id(account: Account) -> str | None:
    """Extract the GAM advertiser id from ``platform_mappings``, or None."""
    mappings = account.platform_mappings or {}
    return (mappings.get(_ACCOUNT_GAM_KEY) or {}).get("advertiser_id")


def _account_advertiser_name(account: Account) -> str | None:
    mappings = account.platform_mappings or {}
    return (mappings.get(_ACCOUNT_GAM_KEY) or {}).get("advertiser_name")


def _set_account_advertiser(
    account: Account,
    advertiser_id: str,
    advertiser_name: str | None,
) -> None:
    """Set GAM advertiser id/name on ``Account.platform_mappings``.

    Preserves any other adapter blocks (triton, freewheel) and other GAM fields
    we don't manage from this endpoint. Re-assigns the dict so SQLAlchemy
    sees the JSONType column as dirty even with mutation-tracking off.
    """
    mappings = dict(account.platform_mappings or {})
    gam_block = dict(mappings.get(_ACCOUNT_GAM_KEY) or {})
    gam_block["advertiser_id"] = advertiser_id
    if advertiser_name is not None:
        gam_block["advertiser_name"] = advertiser_name
    gam_block.setdefault("provisioned_by", "manual:tenant-management-api")
    gam_block.setdefault("provisioned_at", datetime.now(UTC).isoformat())
    mappings[_ACCOUNT_GAM_KEY] = gam_block
    account.platform_mappings = mappings


def _account_to_summary(account: Account) -> AccountSummary:
    """Project an :class:`Account` ORM row to the API summary shape."""
    advertiser_id = _account_advertiser_id(account)
    if account.brand is None:
        brand_dict: dict | None = None
    elif isinstance(account.brand, dict):
        brand_dict = account.brand
    elif hasattr(account.brand, "model_dump"):
        brand_dict = account.brand.model_dump(exclude_none=True)
    else:
        brand_dict = dict(account.brand)
    return AccountSummary(
        account_id=account.account_id,
        name=account.name,
        status=account.status,
        operator=account.operator,
        brand=brand_dict,
        billing=account.billing,
        sandbox=account.sandbox,
        buyer_agent_principal_id=account.principal_id if account.billing == "agent" else None,
        gam_advertiser_id=advertiser_id,
        gam_advertiser_name=_account_advertiser_name(account),
        advertiser_mapped=advertiser_id is not None,
    )


def _account_to_detail(account: Account) -> AccountDetail:
    summary = _account_to_summary(account)
    return AccountDetail(
        **summary.model_dump(),
        payment_terms=account.payment_terms,
        rate_card=account.rate_card,
        created_at=account.created_at,
        updated_at=account.updated_at,
    )


def _generate_pre_mapped_account_name(req: CreateAccountRequest) -> str:
    """Default Account.name when the caller didn't pass one.

    Mirrors the template hinted at in the design doc — operator × brand,
    plus the buyer agent for billing=agent so multi-agent rows are
    distinguishable in the Admin UI without inspecting platform_mappings.
    """
    base = f"{req.operator} × {req.brand.domain}"
    if req.sandbox:
        return f"{base} (sandbox)"
    if req.billing == "agent" and req.buyer_agent_principal_id:
        return f"{base} ({req.buyer_agent_principal_id})"
    return base


def _find_account_by_natural_key(session, tenant_id: str, req: CreateAccountRequest) -> Account | None:
    """Match the existing _sync_accounts_impl natural-key behavior, with the
    agent extension for billing=agent."""
    stmt = select(Account).where(
        Account.tenant_id == tenant_id,
        Account.operator == req.operator,
        Account.brand["domain"].as_string() == req.brand.domain,
        Account.sandbox.is_(req.sandbox),
    )
    if req.brand.brand_id is not None:
        stmt = stmt.where(Account.brand["brand_id"].as_string() == req.brand.brand_id)
    if req.billing == "agent" and req.buyer_agent_principal_id:
        stmt = stmt.where(Account.principal_id == req.buyer_agent_principal_id)
    return session.scalars(stmt).first()


@tenant_management_api.route("/tenants/<tenant_id>/accounts", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(
    json=CreateAccountRequest,
    resp=Response(HTTP_200=AccountDetail, HTTP_201=AccountDetail, HTTP_400=ApiError, HTTP_404=ApiError),
)
def upsert_account(tenant_id: str):
    """Pre-map a GAM advertiser to a billing key.

    Upserts by the same natural key ``_sync_accounts_impl`` uses so a later
    ``sync_accounts`` call from a buyer agent finds the row already wired
    and skips the ``pending_provision`` round trip. Returns 201 on create,
    200 on update.
    """
    req: CreateAccountRequest = request.context.json  # type: ignore[attr-defined]

    # Validation that's awkward in Pydantic alone (cross-field).
    if req.billing == "agent" and not req.buyer_agent_principal_id:
        return _api_error(
            "buyer_agent_required",
            "billing='agent' requires buyer_agent_principal_id — that's the principal in the agent's billing relationship.",
            400,
        )
    if req.sandbox and req.gam_advertiser_id:
        return _api_error(
            "sandbox_advertiser_managed",
            "sandbox accounts route to the per-tenant sandbox advertiser — do not pass gam_advertiser_id.",
            400,
        )

    with get_db_session() as session:
        session.info["management_api_caller"] = True

        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        existing = _find_account_by_natural_key(session, tenant_id, req)

        if existing is None:
            account = Account(
                tenant_id=tenant_id,
                account_id=f"acct_{uuid.uuid4().hex[:12]}",
                name=req.name or _generate_pre_mapped_account_name(req),
                status="active",
                operator=req.operator,
                brand={
                    "domain": req.brand.domain,
                    **({"brand_id": req.brand.brand_id} if req.brand.brand_id else {}),
                },
                billing=req.billing,
                sandbox=req.sandbox,
                principal_id=req.buyer_agent_principal_id if req.billing == "agent" else None,
                payment_terms=req.payment_terms,
                rate_card=req.rate_card,
                platform_mappings={},
            )
            if req.gam_advertiser_id:
                _set_account_advertiser(account, req.gam_advertiser_id, req.gam_advertiser_name)
            session.add(account)
            try:
                session.commit()
            except EmbeddedTenantWriteError as exc:
                session.rollback()
                return _api_error("managed_tenant_write_blocked", str(exc), 403)
            session.refresh(account)
            invalidate_status_cache(tenant_id)
            return jsonify(_account_to_detail(account).model_dump(mode="json")), 201

        # Update path — preserve account_id, refresh advertiser mapping +
        # status, and let the caller bump display fields if they want.
        if req.gam_advertiser_id:
            _set_account_advertiser(existing, req.gam_advertiser_id, req.gam_advertiser_name)
            if existing.status == "pending_provision":
                existing.status = "active"
        if req.name is not None:
            existing.name = req.name
        if req.payment_terms is not None:
            existing.payment_terms = req.payment_terms
        if req.rate_card is not None:
            existing.rate_card = req.rate_card
        existing.updated_at = datetime.now(UTC)

        try:
            session.commit()
        except EmbeddedTenantWriteError as exc:
            session.rollback()
            return _api_error("managed_tenant_write_blocked", str(exc), 403)
        session.refresh(existing)
        invalidate_status_cache(tenant_id)
        return jsonify(_account_to_detail(existing).model_dump(mode="json"))


@tenant_management_api.route("/tenants/<tenant_id>/accounts", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=ListAccountsManagedResponse, HTTP_404=ApiError))
def list_managed_accounts(tenant_id: str):
    """List Accounts for a tenant. Filters: ``operator``, ``billing``,
    ``status``, ``sandbox``, ``advertiser_mapped``."""
    operator = request.args.get("operator")
    billing = request.args.get("billing")
    status_filter = request.args.get("status")
    sandbox_arg = request.args.get("sandbox")
    advertiser_mapped_arg = request.args.get("advertiser_mapped")

    def _to_bool(value: str | None) -> bool | None:
        if value is None:
            return None
        return value.lower() in ("true", "1", "yes")

    sandbox_bool = _to_bool(sandbox_arg)
    advertiser_mapped_bool = _to_bool(advertiser_mapped_arg)

    with get_db_session() as session:
        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        stmt = select(Account).where(Account.tenant_id == tenant_id).order_by(Account.created_at.desc())
        if operator:
            stmt = stmt.where(Account.operator == operator)
        if billing in ("operator", "agent"):
            stmt = stmt.where(Account.billing == billing)
        if status_filter:
            stmt = stmt.where(Account.status == status_filter)
        if sandbox_bool is not None:
            stmt = stmt.where(Account.sandbox.is_(sandbox_bool))

        accounts = list(session.scalars(stmt).all())

    summaries = [_account_to_summary(a) for a in accounts]
    if advertiser_mapped_bool is not None:
        summaries = [s for s in summaries if s.advertiser_mapped == advertiser_mapped_bool]
    return jsonify(ListAccountsManagedResponse(accounts=summaries, count=len(summaries)).model_dump(mode="json"))


@tenant_management_api.route("/tenants/<tenant_id>/status", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=TenantStatusResponse, HTTP_404=ApiError))
def tenant_status(tenant_id: str):
    """Consolidated operational snapshot for a tenant.

    One round-trip, one cache lifetime — covers adapter health, sync runs,
    open workflows, media-buy/package counters, and creative state. The
    response is computed (not stored) and cached in-memory for ~5s.
    """
    snapshot = get_tenant_status(tenant_id)
    if snapshot is None:
        return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)
    return jsonify(snapshot.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Sprint 1.8 — buyer-advertiser routing rules CRUD
# ---------------------------------------------------------------------------


def _routing_rule_to_mapping(rule: AdvertiserRoutingRule) -> BuyerAdvertiserMapping:
    """Project an AdvertiserRoutingRule ORM row onto the wire schema."""
    return BuyerAdvertiserMapping(
        id=rule.id,
        principal_id=rule.principal_id,
        operator_domain=rule.operator_domain,
        brand_house=rule.brand_house,
        brand_id=rule.brand_id,
        gam_advertiser_id=rule.gam_advertiser_id,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


def _is_routing_rule_unique_violation(exc: IntegrityError) -> bool:
    """Detect the COALESCE-unique-index violation on advertiser_routing_rules.

    Postgres reports the index name in the diagnostic; we check both that and
    the table to be resilient to local SQLite (test) variations even though
    production is Postgres-only.
    """
    s = str(exc.orig).lower() if exc.orig else str(exc).lower()
    return "uq_routing_rule_natural_key" in s or "advertiser_routing_rules" in s


def _validate_gam_advertiser_id(session, tenant_id: str, gam_advertiser_id: str) -> bool:
    """Sprint 5 piece D — confirm ``gam_advertiser_id`` is in the synced cache.

    Graceful degradation: when the cache is empty (sync hasn't run yet) we
    return True so rule creation isn't blocked during onboarding. This is the
    "(a) graceful degradation" branch from the sprint spec — the alternative
    (seed cache rows in every test fixture) would couple unrelated test
    setup to this validator.
    """
    # FIXME(embedded-mode-sprint-5-piece-D): GamAdvertiserRepository TBD
    cache_total = session.scalar(
        select(func.count()).select_from(GamAdvertiser).where(GamAdvertiser.tenant_id == tenant_id)
    )
    if not cache_total:
        return True
    exists = session.scalar(
        select(func.count())
        .select_from(GamAdvertiser)
        .where(GamAdvertiser.tenant_id == tenant_id, GamAdvertiser.advertiser_id == gam_advertiser_id)
    )
    return bool(exists)


@tenant_management_api.route("/tenants/<tenant_id>/buyer-advertiser-mappings", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=ListBuyerAdvertiserMappingsResponse, HTTP_404=ApiError))
def list_buyer_advertiser_mappings(tenant_id: str):
    """List routing rules for a tenant. Ordered by ``created_at`` ASC so the
    UI renders them in the same order they were authored.

    Filters: ``operator_domain`` (exact match) — the per-operator detail
    pane uses this to scope the rules grid without re-pulling the full set.
    """
    operator_filter = request.args.get("operator_domain")

    with get_db_session() as session:
        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        stmt = (
            select(AdvertiserRoutingRule)
            .where(AdvertiserRoutingRule.tenant_id == tenant_id)
            .order_by(AdvertiserRoutingRule.created_at.asc())
        )
        if operator_filter:
            stmt = stmt.where(AdvertiserRoutingRule.operator_domain == operator_filter)
        rules = list(session.scalars(stmt).all())

    mappings = [_routing_rule_to_mapping(r) for r in rules]
    return jsonify(ListBuyerAdvertiserMappingsResponse(mappings=mappings, count=len(mappings)).model_dump(mode="json"))


@tenant_management_api.route("/tenants/<tenant_id>/buyer-advertiser-mappings", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(
    json=CreateBuyerAdvertiserMappingRequest,
    resp=Response(
        HTTP_201=BuyerAdvertiserMapping,
        HTTP_400=ApiError,
        HTTP_404=ApiError,
        HTTP_409=ApiError,
    ),
)
def create_buyer_advertiser_mapping(tenant_id: str):
    """Create a routing rule.

    Validation:
    - ``brand_id`` cannot be set without ``brand_house`` (sprint 1.8 doc §2:
      a brand-level rule must be scoped to a parent house).
    - 409 on duplicate ``(operator_domain, brand_house, brand_id)`` tuple
      (NULLs participate in uniqueness via COALESCE in the unique index).

    Validation: ``gam_advertiser_id`` must reference a row in this
    tenant's synced ``gam_advertisers`` cache (Sprint 5 piece D — the
    deferred Sprint 1.8 validator finally lands here).

    Graceful degradation: if the cache is empty (sync hasn't run yet —
    new tenant, GAM not connected, etc.) we skip the check and accept
    the id. This avoids breaking the rule-creation flow during
    onboarding before the first sync completes.
    """
    req: CreateBuyerAdvertiserMappingRequest = request.context.json  # type: ignore[attr-defined]

    if req.brand_id is not None and req.brand_house is None:
        return _api_error(
            "brand_house_required",
            "brand_id requires brand_house — a brand-level rule must be scoped to a parent house.",
            400,
        )

    with get_db_session() as session:
        session.info["management_api_caller"] = True

        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        if not _validate_gam_advertiser_id(session, tenant_id, req.gam_advertiser_id):
            return _api_error(
                "invalid_advertiser_id",
                f"gam_advertiser_id {req.gam_advertiser_id!r} is not in the synced advertisers cache "
                f"for this tenant. Refresh the GAM advertisers cache or pick an existing advertiser.",
                400,
                details={"gam_advertiser_id": req.gam_advertiser_id},
            )

        rule = AdvertiserRoutingRule(
            id=f"rule_{uuid.uuid4().hex[:12]}",
            tenant_id=tenant_id,
            principal_id=req.principal_id,
            operator_domain=req.operator_domain,
            brand_house=req.brand_house,
            brand_id=req.brand_id,
            gam_advertiser_id=req.gam_advertiser_id,
        )
        session.add(rule)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            if _is_routing_rule_unique_violation(exc):
                return _api_error(
                    "routing_rule_duplicate",
                    "A routing rule with this (principal_id, operator_domain, brand_house, brand_id) tuple already exists.",
                    409,
                    details={
                        "principal_id": req.principal_id,
                        "operator_domain": req.operator_domain,
                        "brand_house": req.brand_house,
                        "brand_id": req.brand_id,
                    },
                )
            raise
        except EmbeddedTenantWriteError as exc:
            session.rollback()
            return _api_error("managed_tenant_write_blocked", str(exc), 403)
        session.refresh(rule)

    return jsonify(_routing_rule_to_mapping(rule).model_dump(mode="json")), 201


@tenant_management_api.route("/tenants/<tenant_id>/buyer-advertiser-mappings/<mapping_id>", methods=["PATCH"])
@require_tenant_management_api_key
@spec.validate(
    json=UpdateBuyerAdvertiserMappingRequest,
    resp=Response(
        HTTP_200=BuyerAdvertiserMapping,
        HTTP_400=ApiError,
        HTTP_404=ApiError,
        HTTP_409=ApiError,
    ),
)
def patch_buyer_advertiser_mapping(tenant_id: str, mapping_id: str):
    """PATCH a routing rule.

    ``operator_domain`` is intentionally not patchable (see schema docstring
    — natural-key changes go DELETE+POST so collisions surface explicitly).
    Patching ``brand_house`` / ``brand_id`` can collide with another rule;
    409 on natural-key conflict, same shape as POST.
    """
    req: UpdateBuyerAdvertiserMappingRequest = request.context.json  # type: ignore[attr-defined]

    with get_db_session() as session:
        session.info["management_api_caller"] = True

        rule = session.scalars(select(AdvertiserRoutingRule).filter_by(id=mapping_id, tenant_id=tenant_id)).first()
        if not rule:
            return _api_error(
                "routing_rule_not_found",
                f"Routing rule {mapping_id!r} not found for tenant {tenant_id!r}",
                404,
            )

        if req.principal_id is not None:
            rule.principal_id = req.principal_id
        if req.brand_house is not None:
            rule.brand_house = req.brand_house
        if req.brand_id is not None:
            rule.brand_id = req.brand_id
        if req.gam_advertiser_id is not None:
            rule.gam_advertiser_id = req.gam_advertiser_id

        # Re-validate the brand_id-without-brand_house invariant against
        # the post-merge state, not the request alone — patching only
        # brand_id while a previously-set brand_house is unchanged is
        # still valid; clearing brand_house while brand_id remains set
        # is not (and isn't reachable today since PATCH can't NULL out
        # brand_house, but the guard is cheap and future-proofs the rule).
        if rule.brand_id is not None and rule.brand_house is None:
            session.rollback()
            return _api_error(
                "brand_house_required",
                "brand_id requires brand_house — a brand-level rule must be scoped to a parent house.",
                400,
            )

        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            if _is_routing_rule_unique_violation(exc):
                return _api_error(
                    "routing_rule_duplicate",
                    "A routing rule with this (principal_id, operator_domain, brand_house, brand_id) tuple already exists.",
                    409,
                    details={
                        "principal_id": rule.principal_id,
                        "operator_domain": rule.operator_domain,
                        "brand_house": rule.brand_house,
                        "brand_id": rule.brand_id,
                    },
                )
            raise
        except EmbeddedTenantWriteError as exc:
            session.rollback()
            return _api_error("managed_tenant_write_blocked", str(exc), 403)
        session.refresh(rule)

    return jsonify(_routing_rule_to_mapping(rule).model_dump(mode="json"))


@tenant_management_api.route("/tenants/<tenant_id>/buyer-advertiser-mappings/<mapping_id>", methods=["DELETE"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_204=None, HTTP_404=ApiError))
def delete_buyer_advertiser_mapping(tenant_id: str, mapping_id: str):
    """Delete a routing rule. 204 on success, 404 if not found.

    Idempotency: DELETE on an already-deleted id returns 404 (not 204) —
    the caller asked us to delete a specific row by id, and a 404 is the
    truthful answer that the row isn't there. Callers driving a UI delete
    button should treat 404 as a benign race (someone else deleted it).
    """
    with get_db_session() as session:
        session.info["management_api_caller"] = True

        rule = session.scalars(select(AdvertiserRoutingRule).filter_by(id=mapping_id, tenant_id=tenant_id)).first()
        if not rule:
            return _api_error(
                "routing_rule_not_found",
                f"Routing rule {mapping_id!r} not found for tenant {tenant_id!r}",
                404,
            )

        session.delete(rule)
        try:
            session.commit()
        except EmbeddedTenantWriteError as exc:
            session.rollback()
            return _api_error("managed_tenant_write_blocked", str(exc), 403)

    return "", 204


# ---------------------------------------------------------------------------
# Sprint 1.8 §4 — recent-buyers rollup
# ---------------------------------------------------------------------------


@tenant_management_api.route("/tenants/<tenant_id>/recent-buyers", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=ListRecentBuyersResponse, HTTP_404=ApiError))
def list_recent_buyers(tenant_id: str):
    """Distinct (operator, brand_house, brand_id) triples seen recently.

    Source data: ``Account`` rows joined to ``MediaBuy`` for activity
    counts. Each Account already carries its (operator, brand) natural
    key + the resolved ``platform_mappings.google_ad_manager.advertiser_id``
    + ``resolved_via`` (sprint 1.8 stamp).

    Query params:
    - ``days`` (int, default 30, max 365) — window for last_seen_at filter
    - ``limit`` (int, default 100, max 1000) — paginate by ordered last_seen_at desc

    Returns ``{"buyers": [...]}``. Empty buyers list is the "no recent
    activity" case — never 404 unless the tenant itself doesn't exist.
    """
    try:
        days = max(1, min(365, int(request.args.get("days", "30"))))
    except ValueError:
        days = 30
    try:
        limit = max(1, min(1000, int(request.args.get("limit", "100"))))
    except ValueError:
        limit = 100

    with get_db_session() as session:
        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

    rows = compute_recent_buyers(tenant_id, days=days, limit=limit)
    buyers = [
        RecentBuyer(
            operator_domain=row.operator_domain,
            brand_house=row.brand_house,
            brand_id=row.brand_id,
            last_seen_at=row.last_seen_at,
            request_count=row.request_count,
            resolved_gam_advertiser_id=row.resolved_gam_advertiser_id,
            resolved_via=row.resolved_via,
        )
        for row in rows
    ]
    return jsonify(ListRecentBuyersResponse(buyers=buyers).model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Sprint 1.8 §8 — collapsed refresh endpoint
# ---------------------------------------------------------------------------


# Sync types that ``POST /refresh`` fans out to. The status endpoint
# (sprint 1.5) reports state per type; Storefront's UI hides per-sync
# trigger buttons in embedded mode and surfaces a single "Refresh tenant"
# action that calls this endpoint.
_REFRESH_SYNC_TYPES: tuple[str, ...] = ("inventory", "custom_targeting", "advertisers")

# Idempotency window: re-POST within this window returns the existing
# SyncJob ids instead of creating duplicates. Caps GAM API hammering when
# a publisher mashes the button or Storefront retries on a slow response.
_REFRESH_IDEMPOTENCY_SECONDS = 60


def _mark_sync_failed_on_spawn(
    tenant_id: str,
    sync_ids: list[str],
    exc: BaseException,
    *,
    spawn_label: str,
) -> None:
    """Transition pending SyncJob rows to ``failed`` when worker spawn raised.

    Without this, a spawn-time exception leaves rows in ``pending`` forever
    — the publisher's dashboard shows "never run" with no error surfaced.
    Marking the row ``failed`` makes the failure visible in the salesagent
    UI and lets the next /refresh tick re-attempt cleanly.

    Captures structured error context (class, message, brief traceback) on
    the row so a publisher can self-diagnose common issues (e.g., a missing
    GAM scope shows up as a recognizable exception name) without escalating
    to an engineer for routine failures.
    """
    if not sync_ids:
        return
    import traceback

    from src.core.database.repositories import SyncJobRepository

    # Three frames is usually enough to identify the spawn site without
    # ballooning the Text column. Real traceback lives in the logger.exception
    # call at the catch site for full engineer debugging.
    tb_lines = traceback.format_tb(exc.__traceback__, limit=3) if exc.__traceback__ else []
    tb_summary = "".join(tb_lines).strip()
    error_message = f"Worker spawn failed ({spawn_label}): {type(exc).__name__}: {exc}"
    if tb_summary:
        error_message = f"{error_message}\n\nTraceback (most recent calls):\n{tb_summary}"

    try:
        with get_db_session() as session:
            repo = SyncJobRepository(session, tenant_id)
            transitioned = repo.mark_pending_as_failed(sync_ids, error_message)
            session.commit()
            if transitioned == 0:
                # The rows we tried to mark failed weren't in 'pending' —
                # most likely a worker that started before the spawn-time
                # exception fired already promoted them to 'running'.
                # That worker now owns the lifecycle; surface a warning so
                # this race is visible if it ever happens in practice.
                logger.warning(
                    "Spawn failure but no SyncJob rows transitioned to failed for tenant=%s "
                    "sync_ids=%s — rows already moved past 'pending'; worker owns the lifecycle",
                    tenant_id,
                    sync_ids,
                )
    except Exception:
        logger.exception("Failed to mark SyncJob rows failed after spawn error: %s", sync_ids)


def _spawn_refresh_workers(tenant_id: str, sync_run_ids: dict[str, str]) -> None:
    """Spawn background workers for any pending SyncJob rows /refresh
    just created.

    Per ``_REFRESH_SYNC_TYPES``:
    - ``inventory`` + ``custom_targeting`` are bundled. The inventory
      worker covers targeting internally; pass the targeting sync_id
      so the companion row's lifecycle mirrors inventory's.
    - ``advertisers`` runs in its own thread via ``sync_advertisers``.

    Rows already in 'running' state (idempotency reuse) are skipped —
    a worker is already on it. Spawn failures transition the row to
    ``failed`` so the publisher sees the error in the salesagent UI
    instead of an eternally pending row.
    """
    import threading

    from src.services.background_sync_service import start_inventory_sync_background

    inventory_id = sync_run_ids.get("inventory")
    targeting_id = sync_run_ids.get("custom_targeting")
    advertisers_id = sync_run_ids.get("advertisers")

    # Determine which rows are still pending (vs reused-running rows that
    # already have a worker). Cheap single query.
    pending_ids: set[str] = set()
    candidate_ids = [sid for sid in (inventory_id, targeting_id, advertisers_id) if sid]
    if candidate_ids:
        with get_db_session() as session:
            rows = session.scalars(
                select(SyncJob).where(SyncJob.sync_id.in_(candidate_ids), SyncJob.status == "pending")
            ).all()
            pending_ids = {r.sync_id for r in rows}

    # Inventory + targeting (bundled): kick off only if inventory is
    # pending. Targeting tracks inventory's lifecycle.
    if inventory_id and inventory_id in pending_ids:
        try:
            start_inventory_sync_background(
                tenant_id=tenant_id,
                pending_sync_id=inventory_id,
                targeting_sync_id=targeting_id if targeting_id in pending_ids else None,
            )
        except Exception as exc:
            logger.exception(
                "[refresh] failed to spawn inventory worker for tenant=%s sync_id=%s",
                tenant_id,
                inventory_id,
            )
            # Both inventory and the bundled targeting row need to be
            # transitioned — the targeting worker is the inventory worker.
            failed_ids = [inventory_id]
            if targeting_id and targeting_id in pending_ids:
                failed_ids.append(targeting_id)
            _mark_sync_failed_on_spawn(tenant_id, failed_ids, exc, spawn_label="inventory")
    elif targeting_id and targeting_id in pending_ids:
        # Edge case: inventory row was reused (running) but targeting is
        # fresh-pending. Mark targeting as bundled with the live inventory
        # run so it doesn't sit pending forever.
        with get_db_session() as session:
            targeting_row = session.scalars(select(SyncJob).filter_by(sync_id=targeting_id)).first()
            if targeting_row is not None:
                targeting_row.status = "running"
                # Restamp so /refresh's 60s idempotency window reflects
                # when the bundled targeting work actually started, not
                # when the row was queued at provision/refresh time.
                targeting_row.started_at = datetime.now(UTC)
                targeting_row.progress = {"phase": "Bundled with concurrent inventory sync"}
                session.commit()

    # Advertisers: independent thread.
    if advertisers_id and advertisers_id in pending_ids:
        try:
            from src.services.gam_advertisers_sync import sync_advertisers

            # ``advertisers_id`` is narrowed to ``str`` by the
            # ``if advertisers_id`` guard above, but mypy doesn't propagate the
            # narrow into the closure default. ``str`` here matches the
            # narrowed-truthy type at the call site.
            def _run_advertisers_in_thread(
                tenant_id: str = tenant_id,
                sync_id: str = advertisers_id,  # type: ignore[assignment]
            ) -> None:
                """Wrap sync_advertisers so its re-raise (intentional for
                direct callers + cron pickup) doesn't escape the daemon
                thread. The worker has already marked the SyncJob row as
                'failed' before re-raising — the row is the source of
                truth, not the thread's stack."""
                try:
                    sync_advertisers(tenant_id=tenant_id, sync_id=sync_id)
                except Exception:
                    logger.exception(
                        "[refresh] advertisers worker thread failed for tenant=%s sync_id=%s "
                        "(SyncJob row already marked failed)",
                        tenant_id,
                        sync_id,
                    )

            thread = threading.Thread(
                target=_run_advertisers_in_thread,
                daemon=True,
                name=f"sync-advertisers-{advertisers_id}",
            )
            thread.start()
        except Exception as exc:
            logger.exception(
                "[refresh] failed to spawn advertisers worker for tenant=%s sync_id=%s",
                tenant_id,
                advertisers_id,
            )
            _mark_sync_failed_on_spawn(tenant_id, [advertisers_id], exc, spawn_label="advertisers")


def _create_and_spawn_refresh(
    tenant_id: str,
    *,
    triggered_by_id: str,
    now: datetime | None = None,
) -> tuple[dict[str, str], datetime, dict[str, str]]:
    """Create pending SyncJob rows for all enabled sync types and spawn
    their workers. Returns ``(sync_run_ids, started_at, running_conflicts)``.

    Single source of truth for the row-create-then-spawn pattern shared
    by ``refresh_tenant`` and ``provision_tenant`` (Sprint 1.8 §8
    first-sync-on-provision). Idempotent under rapid re-entry: an
    existing SyncJob within the 60s window is reused instead of
    queuing a duplicate.

    ``running_conflicts`` is a ``{sync_type: existing_sync_id}`` map
    naming the sync_types that already had a ``status=running`` row
    started *outside* the 60s idempotency window — i.e. a long-running
    in-flight sync. The 60s reuse-on-recent-start path is treated as
    part of the same caller action and does NOT populate this dict.
    Callers turn a non-empty dict into HTTP 409 (issue #463) so the
    storefront's "Retry" button gets a clear signal that the click
    triggered nothing new. Provision-time first-sync ignores this
    dict — a fresh tenant cannot have a running sync.

    The caller already validated the tenant exists.
    """
    now = now or datetime.now(UTC)

    with get_db_session() as session:
        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if tenant is None:
            # Caller is expected to validate, but guard against races where
            # the tenant was deleted between validation and helper call.
            raise ValueError(f"Tenant {tenant_id!r} does not exist")

        sync_run_ids: dict[str, str] = {}
        running_conflicts: dict[str, str] = {}
        idempotency_cutoff = now - timedelta(seconds=_REFRESH_IDEMPOTENCY_SECONDS)
        adapter_type = tenant.ad_server or "mock"

        for sync_type in _REFRESH_SYNC_TYPES:
            # Reuse an existing SyncJob if one is running OR started within
            # the idempotency window. ``started_at desc`` so the most
            # recent eligible row wins.
            existing = session.scalars(
                select(SyncJob)
                .where(
                    SyncJob.tenant_id == tenant_id,
                    SyncJob.sync_type == sync_type,
                    or_(
                        SyncJob.status == "running",
                        SyncJob.started_at >= idempotency_cutoff,
                    ),
                )
                .order_by(SyncJob.started_at.desc())
                .limit(1)
            ).first()

            if existing is not None:
                sync_run_ids[sync_type] = existing.sync_id
                # A running row that started outside the 60s window is a
                # genuine conflict — not the rapid-double-click case the
                # idempotency window covers. ``started_at`` is timezone-
                # aware on DateTime(timezone=True) columns; ``cutoff`` is
                # UTC. Naive-aware mismatches surface as TypeError, not
                # silent wrong answer.
                existing_started = existing.started_at
                if existing.status == "running" and existing_started < idempotency_cutoff:
                    running_conflicts[sync_type] = existing.sync_id
                continue

            sync_id = f"sync_{tenant_id}_{sync_type}_{int(now.timestamp())}"
            session.add(
                SyncJob(
                    sync_id=sync_id,
                    tenant_id=tenant_id,
                    adapter_type=adapter_type,
                    sync_type=sync_type,
                    status="pending",
                    started_at=now,
                    triggered_by="api",
                    triggered_by_id=triggered_by_id,
                )
            )
            sync_run_ids[sync_type] = sync_id

        session.commit()

    # Kick off workers for any rows that are still in 'pending' state.
    # Existing-reused rows skip — they're already running. Each worker
    # transitions its row pending → running on entry and completed/failed
    # on exit. The custom_targeting row is bundled with inventory (the
    # inventory worker covers targeting internally, so the companion row
    # tracks the same lifecycle).
    _spawn_refresh_workers(tenant_id=tenant_id, sync_run_ids=sync_run_ids)

    return sync_run_ids, now, running_conflicts


@tenant_management_api.route("/tenants/<tenant_id>/refresh", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(
    resp=Response(
        HTTP_202=RefreshResponse,
        HTTP_404=ApiError,
        HTTP_409=RefreshConflictResponse,
    )
)
def refresh_tenant(tenant_id: str):
    """Fan out a refresh across all sync types — collapses N per-sync
    triggers into one call.

    For each enabled sync type, either reuse the existing SyncJob if one
    started in the last 60 seconds (or is currently running), or create
    a new pending SyncJob. The actual sync work is picked up by the
    existing background sync infrastructure.

    Returns 202 Accepted with ``sync_run_ids`` mapping sync_type → sync_id.
    Storefront reads ``GET /tenants/{tid}/status`` (``syncs`` block) for
    per-type progress.

    Returns 409 ``sync_already_running`` when at least one sync_type has
    a ``running`` row that started *before* the 60s idempotency window
    — i.e. a long-running in-flight sync that the caller's retry would
    just shadow. The 409 body shape mirrors the 202 body
    (``sync_run_ids`` + ``started_at`` at the top level) plus the
    ``error``, ``message``, and ``running_sync_types`` fields — so
    receivers don't need a second parse path. Issue #463: a UI "Retry"
    button needs a clear signal that the click triggered nothing new
    instead of an indistinguishable 202.
    """
    # Validate tenant exists before delegating to the helper. Cheap query
    # with the same shape the helper uses internally.
    with get_db_session() as session:
        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

    sync_run_ids, started_at, running_conflicts = _create_and_spawn_refresh(
        tenant_id=tenant_id,
        triggered_by_id="tenant_management_api:refresh",
    )

    invalidate_status_cache(tenant_id)

    if running_conflicts:
        # Shape mirrors the 202 body (sync_run_ids + started_at at the
        # top level) so receivers don't need a second parse path. The
        # in-flight run's id is the correlation handle — the caller
        # can read GET /tenants/{tid}/status (syncs block) keyed by it
        # without re-issuing a refresh.
        conflict = RefreshConflictResponse(
            message=(
                f"Sync already running for sync_types: {', '.join(sorted(running_conflicts.keys()))}. "
                "Existing sync_run_ids returned for correlation."
            ),
            sync_run_ids=sync_run_ids,
            started_at=started_at,
            running_sync_types=sorted(running_conflicts.keys()),
        )
        return jsonify(conflict.model_dump(mode="json")), 409

    response = RefreshResponse(sync_run_ids=sync_run_ids, started_at=started_at)
    return jsonify(response.model_dump(mode="json")), 202


# ---------------------------------------------------------------------------
# Sprint 5 piece D — GAM advertisers cache lookup
# ---------------------------------------------------------------------------


_GAM_ADVERTISERS_DEFAULT_LIMIT = 50
_GAM_ADVERTISERS_MAX_LIMIT = 500


def _decode_advertisers_cursor(raw: str | None) -> int:
    """Decode the opaque base64 ``{"offset": N}`` cursor.

    Invalid / empty cursors yield offset 0 — never raise on bad client
    input here because the cursor is supposed to be sealed but we don't
    want one stale bookmark to break the picker.
    """
    if not raw:
        return 0
    import base64

    try:
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
        offset = int(payload.get("offset", 0))
        return max(0, offset)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return 0


def _encode_advertisers_cursor(offset: int) -> str:
    """Encode ``{"offset": N}`` as the opaque base64 cursor."""
    import base64

    return base64.urlsafe_b64encode(json.dumps({"offset": int(offset)}).encode()).decode()


@tenant_management_api.route("/tenants/<tenant_id>/gam/advertisers", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=ListGamAdvertisersResponse, HTTP_404=ApiError))
def list_gam_advertisers(tenant_id: str):
    """Searchable, paginated read over the synced ``gam_advertisers`` cache.

    Reads from the local cache, never from live GAM (10k+ advertiser
    networks make per-keystroke round trips prohibitive). Sync is
    triggered separately via ``POST /refresh`` or the cron worker.

    Query params:
    - ``q`` (str, optional) — case-insensitive substring on ``name`` OR
      exact match on ``id`` if numeric. ``q`` < 2 chars returns the
      first page unfiltered (avoids expensive scan from typing first
      character).
    - ``limit`` (int, default 50, max 500) — page size.
    - ``cursor`` (opaque base64, optional) — page bookmark.

    ``synced_at`` reports the most-recent ``gam_advertisers.synced_at``
    for the tenant so the picker can show "Last synced 5 minutes ago".
    """
    q_raw = (request.args.get("q") or "").strip()
    try:
        limit = int(request.args.get("limit", _GAM_ADVERTISERS_DEFAULT_LIMIT))
    except ValueError:
        limit = _GAM_ADVERTISERS_DEFAULT_LIMIT
    limit = max(1, min(_GAM_ADVERTISERS_MAX_LIMIT, limit))
    offset = _decode_advertisers_cursor(request.args.get("cursor"))

    with get_db_session() as session:
        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        # FIXME(embedded-mode-sprint-5-piece-D): GamAdvertiserRepository TBD
        base = select(GamAdvertiser).where(GamAdvertiser.tenant_id == tenant_id)

        # ``q`` shape decides the filter:
        #   numeric → exact id match (single-result path)
        #   >= 2 chars → case-insensitive name substring
        #   else → unfiltered (avoids the expensive scan from a
        #   single-character keystroke; also the empty / no-input case)
        if q_raw and q_raw.isdigit():
            base = base.where(GamAdvertiser.advertiser_id == q_raw)
        elif len(q_raw) >= 2:
            base = base.where(func.lower(GamAdvertiser.name).contains(q_raw.lower()))

        ordered = base.order_by(GamAdvertiser.name.asc(), GamAdvertiser.advertiser_id.asc())
        # Fetch one extra row to know whether next_cursor should be set
        # without a separate count query.
        rows = list(session.scalars(ordered.limit(limit + 1).offset(offset)).all())
        has_more = len(rows) > limit
        rows = rows[:limit]

        synced_at = session.scalar(
            select(func.max(GamAdvertiser.synced_at)).where(GamAdvertiser.tenant_id == tenant_id)
        )

    advertisers = [
        GamAdvertiserSchema(
            id=row.advertiser_id,
            name=row.name,
            currency_code=row.currency_code,
            status=row.status,
        )
        for row in rows
    ]
    response = ListGamAdvertisersResponse(
        advertisers=advertisers,
        next_cursor=_encode_advertisers_cursor(offset + limit) if has_more else None,
        synced_at=synced_at,
    )
    return jsonify(response.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Sprint 3 — workflow approve/reject + read drill-downs
# ---------------------------------------------------------------------------


_DEFAULT_PAGE_LIMIT = 50
_MAX_PAGE_LIMIT = 500
_DEFAULT_SYNC_HISTORY_LIMIT = 20


def _parse_limit(raw: str | None, *, default: int = _DEFAULT_PAGE_LIMIT, maximum: int = _MAX_PAGE_LIMIT) -> int:
    """Clamp ``?limit=`` to ``[1, maximum]``; bad input falls back to the default."""
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(1, min(maximum, value))


def _parse_iso_date_arg(raw: str | None) -> datetime | None:
    """Parse an ISO-8601 date(time) query arg; return None if absent or invalid."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _identity_from_request() -> tuple[str | None, str]:
    """Resolve ``(decided_by_email, decided_by_source)`` for a workflow decision.

    When ``X-Identity-Email`` is present (UI-proxied call), use the
    propagated identity headers — ``X-Identity-Source`` carries the host
    product label (e.g. ``scope3_storefront``). Absent → control-plane
    raw API call, recorded as ``management_api`` with no email.
    """
    from src.admin.middleware.identity_propagation import (
        InvalidPropagatedIdentity,
        read_identity_from_request,
    )

    try:
        identity = read_identity_from_request(request)
    except InvalidPropagatedIdentity:
        # Headers were present but malformed — fail-open to management_api so
        # the decision still gets recorded; the audit trail captures the
        # decision regardless of the broken header.
        return None, "management_api"
    if identity is None:
        return None, "management_api"
    return identity.email, identity.source


# ---------------------------------------------------------------------------
# Workflow endpoints
# ---------------------------------------------------------------------------


@tenant_management_api.route("/tenants/<tenant_id>/workflows", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=ListWorkflowsResponse, HTTP_404=ApiError))
def list_workflows(tenant_id: str):
    """List workflow steps for a tenant, sorted with pending first.

    Query params:
    - ``status`` (repeatable): filter by wire-side status. Multiple values
      OR together. Defaults to all statuses.
    - ``workflow_type``: exact match against ``tool_name`` or ``step_type``.
    - ``limit`` (int, default 50, max 500)
    - ``cursor`` (opaque base64): bookmark from a previous response.
    """
    from src.admin.services.tenant_management_sprint3 import (
        decode_cursor,
        encode_cursor,
        is_workflow_decided,
        map_workflow_status,
        parse_cursor_datetime,
        workflow_to_summary,
    )
    from src.core.database.repositories import WorkflowRepository

    # Translate wire-side status filters to DB-side filters. ``pending``
    # maps to the open WorkflowStep statuses; the others map 1:1.
    wire_statuses = request.args.getlist("status")
    db_statuses: list[str] | None = None
    if wire_statuses:
        db_statuses = []
        for s in wire_statuses:
            if s == "pending":
                db_statuses.extend(["pending", "in_progress", "requires_approval"])
            elif s == "approved":
                db_statuses.append("completed")
            elif s == "rejected":
                db_statuses.append("failed")
            else:
                db_statuses.append(s)

    workflow_type_filter = request.args.get("workflow_type")
    limit = _parse_limit(request.args.get("limit"))
    cursor_payload = decode_cursor(request.args.get("cursor"))
    cursor_created_at = parse_cursor_datetime(cursor_payload.get("ts"))
    cursor_id = cursor_payload.get("id") if isinstance(cursor_payload.get("id"), str) else None

    from src.core.database.repositories import TenantConfigRepository

    with get_db_session() as session:
        if TenantConfigRepository(session, tenant_id).get_tenant() is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        repo = WorkflowRepository(session, tenant_id)
        # Fetch limit + 1 to determine whether next_cursor should be set
        # without a separate count query.
        rows = repo.list_filtered_with_cursor(
            statuses=db_statuses,
            workflow_type=workflow_type_filter,
            cursor_created_at=cursor_created_at,
            cursor_id=cursor_id,
            limit=limit + 1,
        )
        has_more = len(rows) > limit
        rows = rows[:limit]

        summaries = []
        for step in rows:
            principal_id, principal_name = repo.get_context_principal(step)
            summaries.append(workflow_to_summary(step, principal_id, principal_name))
        # After projection: post-filter on wire-side status. Required when
        # the caller asked for a status that maps to multiple DB states
        # (e.g., "approved" subset of "completed") — the response_data
        # decision determines the final mapping.
        if wire_statuses:
            wanted = set(wire_statuses)
            summaries = [s for s in summaries if s.status in wanted]

        next_cursor: str | None = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor({"ts": last.created_at, "id": last.step_id})

    response = ListWorkflowsResponse(workflows=summaries, count=len(summaries), next_cursor=next_cursor)
    # Use the unused-import shim so flake8/ruff don't complain about
    # imports added for type-only purposes elsewhere.
    _ = map_workflow_status, is_workflow_decided
    return jsonify(response.model_dump(mode="json"))


@tenant_management_api.route("/tenants/<tenant_id>/workflows/<workflow_id>", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=WorkflowDetail, HTTP_404=ApiError))
def get_workflow(tenant_id: str, workflow_id: str):
    """Return :class:`WorkflowDetail` for one workflow."""
    from src.admin.services.tenant_management_sprint3 import workflow_to_detail
    from src.core.database.repositories import TenantConfigRepository, WorkflowRepository

    with get_db_session() as session:
        if TenantConfigRepository(session, tenant_id).get_tenant() is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        repo = WorkflowRepository(session, tenant_id)
        step = repo.get_by_step_id(workflow_id)
        if step is None:
            return _api_error(
                "workflow_not_found",
                f"Workflow {workflow_id!r} does not exist for tenant {tenant_id!r}",
                404,
            )
        principal_id, principal_name = repo.get_context_principal(step)
        detail = workflow_to_detail(step, principal_id, principal_name)

    return jsonify(detail.model_dump(mode="json"))


def _decide_workflow(
    tenant_id: str,
    workflow_id: str,
    *,
    decision: str,
    notes: str | None,
):
    """Shared implementation for approve and reject endpoints.

    Idempotent on re-decide:
    - Same decision a second time → 200 with existing state.
    - Conflicting decision → 409 ``workflow_already_decided``.
    - Decided after expiry → 409 ``workflow_expired``.
    """
    from src.admin.services.tenant_management_sprint3 import (
        is_workflow_expired,
        map_workflow_status,
        record_workflow_decision,
        workflow_to_detail,
    )
    from src.admin.services.tenant_status_service import invalidate_status_cache
    from src.core.database.repositories import AuditLogRepository, TenantConfigRepository, WorkflowRepository

    decided_by_email, decided_by_source = _identity_from_request()
    actor_type = "user" if decided_by_email else "management_api"

    with get_db_session() as session:
        session.info["management_api_caller"] = True
        if TenantConfigRepository(session, tenant_id).get_tenant() is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        repo = WorkflowRepository(session, tenant_id)
        step = repo.get_by_step_id(workflow_id)
        if step is None:
            return _api_error(
                "workflow_not_found",
                f"Workflow {workflow_id!r} does not exist for tenant {tenant_id!r}",
                404,
            )

        current_status = map_workflow_status(step)
        already_decided = current_status != "pending"

        if already_decided:
            # Re-decide path. Same decision → 200 idempotent. Different
            # decision → 409 conflict. Expired → 409 (independent of the
            # original decision; an expired workflow can't be re-decided
            # at all).
            if is_workflow_expired(step):
                return _api_error(
                    "workflow_expired",
                    f"Workflow {workflow_id!r} expired at {(step.request_data or {}).get('expires_at')!r}",
                    409,
                    details={"workflow_id": workflow_id, "current_status": current_status},
                )
            wanted_status = "approved" if decision == "approve" else "rejected"
            if current_status == wanted_status:
                # Idempotent — return the existing state, no new decision row.
                principal_id, principal_name = repo.get_context_principal(step)
                detail = workflow_to_detail(step, principal_id, principal_name)
                return jsonify(detail.model_dump(mode="json"))
            return _api_error(
                "workflow_already_decided",
                f"Workflow {workflow_id!r} is already {current_status!r}; cannot {decision} it.",
                409,
                details={"workflow_id": workflow_id, "current_status": current_status},
            )

        if is_workflow_expired(step):
            return _api_error(
                "workflow_expired",
                f"Workflow {workflow_id!r} expired before decision",
                409,
                details={"workflow_id": workflow_id},
            )

        # Apply the decision.
        record_workflow_decision(
            step,
            decision=decision,
            notes=notes,
            decided_by_email=decided_by_email,
            decided_by_source=decided_by_source,
        )
        principal_id, principal_name = repo.get_context_principal(step)

        # Audit log row. Subject is the object the workflow gates (e.g.
        # media_buy/mb_xxx); falls back to the workflow itself when the
        # mapping is missing.
        from src.admin.services.tenant_management_sprint3 import workflow_subject

        subject_type, subject_id = workflow_subject(step)
        audit_repo = AuditLogRepository(session, tenant_id)
        propagated_user_id = None
        propagated_org_id = None
        propagated_source = decided_by_source if decided_by_source != "management_api" else None
        from src.admin.middleware.identity_propagation import (
            InvalidPropagatedIdentity,
            read_identity_from_request,
        )

        try:
            propagated = read_identity_from_request(request)
        except InvalidPropagatedIdentity:
            logger.debug("propagated identity headers malformed; recording without them", exc_info=True)
            propagated = None
        if propagated is not None:
            propagated_user_id = propagated.user_id
            propagated_org_id = propagated.org_id

        audit_repo.record(
            operation=f"workflow.{decision}",
            subject_type=subject_type,
            subject_id=subject_id,
            actor_type=actor_type,
            principal_id=principal_id,
            principal_name=principal_name,
            external_user_email=decided_by_email,
            external_user_id=propagated_user_id,
            external_org_id=propagated_org_id,
            external_source=propagated_source,
            details={"workflow_id": workflow_id, "notes": notes, "decided_by_source": decided_by_source},
        )

        try:
            session.commit()
        except EmbeddedTenantWriteError as exc:
            session.rollback()
            return _api_error("managed_tenant_write_blocked", str(exc), 403)

        session.refresh(step)
        detail = workflow_to_detail(step, principal_id, principal_name)

    invalidate_status_cache(tenant_id)

    # Fire workflow.decided to subscribed webhooks. emit_event is
    # non-raising — webhook delivery is observability, never critical-path.
    from src.admin.services.webhook_publisher import emit_event

    emit_event(tenant_id, "workflow.decided", {"workflow": detail.model_dump(mode="json")})

    return jsonify(detail.model_dump(mode="json"))


@tenant_management_api.route("/tenants/<tenant_id>/workflows/<workflow_id>/approve", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(
    json=ApproveWorkflowRequest,
    resp=Response(HTTP_200=WorkflowDetail, HTTP_404=ApiError, HTTP_409=ApiError),
)
def approve_workflow(tenant_id: str, workflow_id: str):
    """Approve a workflow. Idempotent: re-approving returns 200 with the
    existing state. Conflicting re-decide (approve after reject) returns
    409. Expired workflows can't be approved."""
    req: ApproveWorkflowRequest = request.context.json  # type: ignore[attr-defined]
    return _decide_workflow(tenant_id, workflow_id, decision="approve", notes=req.notes)


@tenant_management_api.route("/tenants/<tenant_id>/workflows/<workflow_id>/reject", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(
    json=RejectWorkflowRequest,
    resp=Response(HTTP_200=WorkflowDetail, HTTP_400=ApiError, HTTP_404=ApiError, HTTP_409=ApiError),
)
def reject_workflow(tenant_id: str, workflow_id: str):
    """Reject a workflow. Notes are required. Idempotent re-rejection
    returns 200 with existing state; conflicting decision returns 409."""
    req: RejectWorkflowRequest = request.context.json  # type: ignore[attr-defined]
    return _decide_workflow(tenant_id, workflow_id, decision="reject", notes=req.notes)


# ---------------------------------------------------------------------------
# Media-buy endpoints (read-only)
# ---------------------------------------------------------------------------


@tenant_management_api.route("/tenants/<tenant_id>/media-buys", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=ListMediaBuysResponse, HTTP_404=ApiError))
def list_media_buys(tenant_id: str):
    """List media buys for a tenant.

    Query params: ``status``, ``principal_id``, ``from_date``, ``to_date``,
    ``limit``, ``cursor``. Date filters apply to ``flight_start_date``.
    """
    from src.admin.services.tenant_management_sprint3 import (
        decode_cursor,
        encode_cursor,
        media_buy_to_summary,
        parse_cursor_datetime,
    )
    from src.core.database.repositories import MediaBuyRepository, TenantConfigRepository

    status_filter = request.args.get("status")
    principal_id_filter = request.args.get("principal_id")
    from_dt = _parse_iso_date_arg(request.args.get("from_date"))
    to_dt = _parse_iso_date_arg(request.args.get("to_date"))
    limit = _parse_limit(request.args.get("limit"))
    cursor_payload = decode_cursor(request.args.get("cursor"))
    cursor_created_at = parse_cursor_datetime(cursor_payload.get("ts"))
    cursor_id = cursor_payload.get("id") if isinstance(cursor_payload.get("id"), str) else None

    with get_db_session() as session:
        config_repo = TenantConfigRepository(session, tenant_id)
        if config_repo.get_tenant() is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        repo = MediaBuyRepository(session, tenant_id)
        rows = repo.list_filtered_with_cursor(
            status=status_filter,
            principal_id=principal_id_filter,
            from_date=from_dt.date() if from_dt else None,
            to_date=to_dt.date() if to_dt else None,
            cursor_created_at=cursor_created_at,
            cursor_id=cursor_id,
            limit=limit + 1,
        )
        has_more = len(rows) > limit
        rows = rows[:limit]

        # Bulk-load principal names so we don't N+1 the principals table.
        principal_names = config_repo.get_principal_names(list({b.principal_id for b in rows}))

        summaries = [media_buy_to_summary(b, principal_names.get(b.principal_id, b.principal_id)) for b in rows]

        next_cursor: str | None = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor({"ts": last.created_at, "id": last.media_buy_id})

    response = ListMediaBuysResponse(media_buys=summaries, count=len(summaries), next_cursor=next_cursor)
    return jsonify(response.model_dump(mode="json"))


@tenant_management_api.route("/tenants/<tenant_id>/media-buys/<media_buy_id>", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=MediaBuyDetail, HTTP_404=ApiError))
def get_media_buy(tenant_id: str, media_buy_id: str):
    """Return :class:`MediaBuyDetail` for one media buy."""
    from src.admin.services.tenant_management_sprint3 import media_buy_to_detail
    from src.core.database.repositories import MediaBuyRepository, TenantConfigRepository

    with get_db_session() as session:
        config_repo = TenantConfigRepository(session, tenant_id)
        if config_repo.get_tenant() is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        repo = MediaBuyRepository(session, tenant_id)
        buy = repo.get_by_id(media_buy_id)
        if buy is None:
            return _api_error(
                "media_buy_not_found",
                f"Media buy {media_buy_id!r} does not exist for tenant {tenant_id!r}",
                404,
            )

        principal = config_repo.get_principal(buy.principal_id)
        principal_name = principal.name if principal else buy.principal_id

        detail = media_buy_to_detail(buy, principal_name)

    return jsonify(detail.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


@tenant_management_api.route("/tenants/<tenant_id>/audit-log", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=ListAuditLogResponse, HTTP_404=ApiError))
def list_audit_log(tenant_id: str):
    """List audit log entries for a tenant.

    Query params: ``action_prefix``, ``subject_type``, ``subject_id``,
    ``actor_type``, ``external_source``, ``from_date``, ``to_date``,
    ``limit``, ``cursor``. Default sort: ``occurred_at desc``.
    """
    from src.admin.services.tenant_management_sprint3 import (
        audit_to_entry,
        decode_cursor,
        encode_cursor,
        parse_cursor_datetime,
    )
    from src.core.database.repositories import AuditLogRepository, TenantConfigRepository

    action_prefix = request.args.get("action_prefix")
    subject_type = request.args.get("subject_type")
    subject_id = request.args.get("subject_id")
    actor_type = request.args.get("actor_type")
    external_source = request.args.get("external_source")
    from_dt = _parse_iso_date_arg(request.args.get("from_date"))
    to_dt = _parse_iso_date_arg(request.args.get("to_date"))
    limit = _parse_limit(request.args.get("limit"))
    cursor_payload = decode_cursor(request.args.get("cursor"))
    cursor_ts = parse_cursor_datetime(cursor_payload.get("ts"))
    cursor_id_raw = cursor_payload.get("id")
    cursor_id: int | None = None
    if isinstance(cursor_id_raw, int):
        cursor_id = cursor_id_raw
    elif isinstance(cursor_id_raw, str):
        try:
            cursor_id = int(cursor_id_raw)
        except ValueError:
            cursor_id = None

    with get_db_session() as session:
        if TenantConfigRepository(session, tenant_id).get_tenant() is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        repo = AuditLogRepository(session, tenant_id)
        rows = repo.list_filtered(
            action_prefix=action_prefix,
            subject_type=subject_type,
            subject_id=subject_id,
            actor_type=actor_type,
            external_source=external_source,
            from_date=from_dt,
            to_date=to_dt,
            cursor_timestamp=cursor_ts,
            cursor_id=cursor_id,
            limit=limit + 1,
        )
        has_more = len(rows) > limit
        rows = rows[:limit]

        entries = [audit_to_entry(r) for r in rows]
        next_cursor: str | None = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor({"ts": last.timestamp, "id": last.log_id})

    response = ListAuditLogResponse(entries=entries, count=len(entries), next_cursor=next_cursor)
    return jsonify(response.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Sync history
# ---------------------------------------------------------------------------


@tenant_management_api.route("/tenants/<tenant_id>/sync-history", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=ListSyncHistoryResponse, HTTP_404=ApiError))
def list_sync_history(tenant_id: str):
    """List historical sync runs for a tenant.

    Query params: ``sync_type`` (``inventory`` / ``custom_targeting`` /
    ``advertisers``), ``status``, ``limit`` (default 20, max 500),
    ``cursor``. Default sort: ``started_at desc``.

    Current sync state is in ``GET /tenants/{tid}/status`` — this endpoint
    is the timeline drill-down.
    """
    from src.admin.services.tenant_management_sprint3 import (
        decode_cursor,
        encode_cursor,
        parse_cursor_datetime,
        sync_to_run_info,
    )
    from src.core.database.repositories import SyncJobRepository, TenantConfigRepository

    sync_type = request.args.get("sync_type")
    status_filter = request.args.get("status")
    limit = _parse_limit(request.args.get("limit"), default=_DEFAULT_SYNC_HISTORY_LIMIT)
    cursor_payload = decode_cursor(request.args.get("cursor"))
    cursor_ts = parse_cursor_datetime(cursor_payload.get("ts"))
    cursor_id = cursor_payload.get("id") if isinstance(cursor_payload.get("id"), str) else None

    with get_db_session() as session:
        if TenantConfigRepository(session, tenant_id).get_tenant() is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        repo = SyncJobRepository(session, tenant_id)
        rows = repo.list_history(
            sync_type=sync_type,
            status=status_filter,
            cursor_started_at=cursor_ts,
            cursor_id=cursor_id,
            limit=limit + 1,
        )
        has_more = len(rows) > limit
        rows = rows[:limit]

        runs = [sync_to_run_info(r) for r in rows]
        next_cursor: str | None = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor({"ts": last.started_at, "id": last.sync_id})

    response = ListSyncHistoryResponse(runs=runs, count=len(runs), next_cursor=next_cursor)
    return jsonify(response.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Sprint 6 — outbound webhook subscription endpoints
# ---------------------------------------------------------------------------


def _webhook_to_summary(sub) -> dict:
    """Project a :class:`WebhookSubscription` ORM row to the summary wire shape."""
    return WebhookSubscriptionSummary(
        webhook_id=sub.webhook_id,
        url=sub.url,
        event_types=list(sub.event_types or []),
        description=sub.description,
        extra_headers=dict(sub.extra_headers) if sub.extra_headers else None,
        is_active=sub.is_active,
        consecutive_failures=sub.consecutive_failures or 0,
        last_delivery_at=sub.last_delivery_at,
        last_delivery_status=sub.last_delivery_status,
        created_at=sub.created_at,
    ).model_dump(mode="json")


@tenant_management_api.route("/tenants/<tenant_id>/webhooks", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=ListWebhooksResponse, HTTP_404=ApiError))
def list_webhooks(tenant_id: str):
    """List active webhook subscriptions for a tenant.

    Secrets are NEVER returned — they were surfaced exactly once at create
    time. To rotate, delete the subscription and create a new one.
    """
    from src.core.database.repositories import TenantConfigRepository, WebhookSubscriptionRepository

    with get_db_session() as session:
        if TenantConfigRepository(session, tenant_id).get_tenant() is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        repo = WebhookSubscriptionRepository(session, tenant_id)
        rows = repo.list_active()
        webhooks = [_webhook_to_summary(s) for s in rows]

    return jsonify({"webhooks": webhooks, "count": len(webhooks)})


@tenant_management_api.route("/tenants/<tenant_id>/webhooks", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(
    json=CreateWebhookSubscriptionRequest,
    resp=Response(
        HTTP_201=WebhookSubscriptionCreatedResponse,
        HTTP_400=ApiError,
        HTTP_404=ApiError,
    ),
)
def create_webhook(tenant_id: str):
    """Register a new outbound webhook subscription.

    Returns the plaintext ``secret`` exactly once (in this response). It is
    not retrievable later — the caller MUST persist it. Lost secrets require
    re-registering. Receivers verify HMAC-SHA256 signatures using the secret.
    """
    from src.admin.services.webhook_delivery import WebhookUrlError, validate_webhook_url
    from src.admin.services.webhook_publisher import remember_webhook_secret
    from src.core.database.repositories import TenantConfigRepository, WebhookSubscriptionRepository
    from src.core.database.repositories.webhook_subscription import generate_secret

    req: CreateWebhookSubscriptionRequest = request.context.json  # type: ignore[attr-defined]

    try:
        validated_url = validate_webhook_url(req.url)
    except WebhookUrlError as exc:
        return _api_error(exc.code, str(exc), 400)

    # Validate event_types against the supported taxonomy. Pydantic Literal
    # already filters but we re-check for clearer error codes when the
    # Literal layer admits an unknown value through schema-extra=ignore.
    unknown = [e for e in req.event_types if e not in WEBHOOK_EVENT_TYPES]
    if unknown:
        return _api_error(
            "webhook_event_types_unknown",
            f"unknown event types: {unknown}; supported: {list(WEBHOOK_EVENT_TYPES)}",
            400,
            details={"unknown_event_types": unknown},
        )

    secret_plaintext = req.secret or generate_secret()
    if len(secret_plaintext) < 32:
        return _api_error(
            "webhook_secret_too_short",
            "secret must be at least 32 characters",
            400,
        )

    with get_db_session() as session:
        if TenantConfigRepository(session, tenant_id).get_tenant() is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        repo = WebhookSubscriptionRepository(session, tenant_id)
        webhook_id = f"wh_{uuid.uuid4().hex}"
        sub = repo.create(
            webhook_id=webhook_id,
            url=validated_url,
            event_types=list(req.event_types),
            secret=secret_plaintext,
            description=req.description,
            extra_headers=req.extra_headers,
        )
        session.commit()
        session.refresh(sub)
        summary = _webhook_to_summary(sub)

    # Cache the plaintext secret so the publisher can sign outbound deliveries.
    # See ``webhook_publisher._SecretCache`` for the v1 limitation.
    remember_webhook_secret(webhook_id, secret_plaintext)

    payload = {**summary, "secret": secret_plaintext}
    return jsonify(payload), 201


@tenant_management_api.route("/tenants/<tenant_id>/webhooks/<webhook_id>", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=WebhookSubscriptionSummary, HTTP_404=ApiError))
def get_webhook(tenant_id: str, webhook_id: str):
    """Return a single subscription record. Secret is omitted."""
    from src.core.database.repositories import TenantConfigRepository, WebhookSubscriptionRepository

    with get_db_session() as session:
        if TenantConfigRepository(session, tenant_id).get_tenant() is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        repo = WebhookSubscriptionRepository(session, tenant_id)
        sub = repo.get_by_id(webhook_id)
        if sub is None:
            return _api_error(
                "webhook_not_found",
                f"Webhook {webhook_id!r} does not exist for tenant {tenant_id!r}",
                404,
            )
        summary = _webhook_to_summary(sub)

    return jsonify(summary)


@tenant_management_api.route("/tenants/<tenant_id>/webhooks/<webhook_id>", methods=["DELETE"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_204=None, HTTP_404=ApiError))
def delete_webhook(tenant_id: str, webhook_id: str):
    """Soft-delete a subscription.

    Sets ``is_active=false`` so the row stays around for audit-log
    references but the publisher stops dispatching. The plaintext secret
    is dropped from the in-process cache so future re-registrations don't
    accidentally reuse it.
    """
    from src.admin.services.webhook_publisher import forget_webhook_secret
    from src.core.database.repositories import TenantConfigRepository, WebhookSubscriptionRepository

    with get_db_session() as session:
        if TenantConfigRepository(session, tenant_id).get_tenant() is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        repo = WebhookSubscriptionRepository(session, tenant_id)
        sub = repo.get_by_id(webhook_id)
        if sub is None:
            return _api_error(
                "webhook_not_found",
                f"Webhook {webhook_id!r} does not exist for tenant {tenant_id!r}",
                404,
            )
        repo.deactivate(sub)
        session.commit()

    forget_webhook_secret(webhook_id)
    return ("", 204)


@tenant_management_api.route(
    "/tenants/<tenant_id>/webhooks/<webhook_id>/test",
    methods=["POST"],
)
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=WebhookTestResponse, HTTP_404=ApiError))
def test_webhook(tenant_id: str, webhook_id: str):
    """Synchronously fire a synthetic event of every registered type.

    Returns one delivery result per registered event type. ``delivered``
    on the response is the AND of all per-event ``delivered`` flags.
    Used by host products to verify the receiver is wired up correctly.

    Failures here do NOT auto-disable the subscription — the consecutive-
    failures counter is incremented just like a real delivery, so flapping
    test runs eventually trip the disablement threshold.
    """
    import asyncio

    from src.admin.services.webhook_delivery import build_envelope, deliver_event_sync
    from src.admin.services.webhook_publisher import get_webhook_secret
    from src.core.database.repositories import TenantConfigRepository, WebhookSubscriptionRepository

    with get_db_session() as session:
        if TenantConfigRepository(session, tenant_id).get_tenant() is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        repo = WebhookSubscriptionRepository(session, tenant_id)
        sub = repo.get_by_id(webhook_id)
        if sub is None:
            return _api_error(
                "webhook_not_found",
                f"Webhook {webhook_id!r} does not exist for tenant {tenant_id!r}",
                404,
            )
        # Snapshot fields we need; the session closes after the lookup.
        sub_url = sub.url
        sub_event_types = list(sub.event_types or [])
        sub_extra_headers = dict(sub.extra_headers) if sub.extra_headers else None
        sub_webhook_id = sub.webhook_id
        sub_tenant_id = sub.tenant_id

    secret = get_webhook_secret(webhook_id)
    if secret is None:
        return _api_error(
            "webhook_secret_lost",
            "plaintext secret not in cache; delete and re-register the webhook",
            409,
        )

    # Iterate the events the subscription cares about (or all events if it
    # subscribed to "everything"). One delivery per event type.
    if sub_event_types:
        targets = [e for e in sub_event_types if e in WEBHOOK_EVENT_TYPES]
    else:
        targets = list(WEBHOOK_EVENT_TYPES)

    results: list[dict] = []
    overall_ok = True

    # Reuse a single subscription-like object reference for bookkeeping. The
    # delivery service refreshes its DB state each time anyway.
    class _SubProxy:
        webhook_id = sub_webhook_id
        tenant_id = sub_tenant_id
        url = sub_url
        extra_headers = sub_extra_headers

    for event_type in targets:
        envelope = build_envelope(
            event_type=event_type,
            tenant_id=tenant_id,
            data={"test": True, "subject_type": "tenant", "subject_id": tenant_id},
        )
        # _SubProxy is a duck-typed stand-in for WebhookSubscription that
        # carries just the fields deliver_event_sync reads. Real subscription
        # row would be overkill for a connectivity test.
        status_code, latency_ms, error = asyncio.run(
            deliver_event_sync(_SubProxy, secret, envelope)  # type: ignore[arg-type]
        )
        delivered = status_code is not None and 200 <= status_code < 300
        if not delivered:
            overall_ok = False
        results.append(
            WebhookTestDeliveryResult(
                event_type=event_type,
                event_id=envelope["event_id"],
                delivered=delivered,
                response_status=status_code,
                latency_ms=latency_ms,
                error=error,
            ).model_dump(mode="json")
        )

    return jsonify({"delivered": overall_ok, "results": results})


# Register all spectree-validated routes with the OpenAPI generator.
# This is a no-op for non-validated handlers; only routes with @spec.validate participate.
spec.register(tenant_management_api)
