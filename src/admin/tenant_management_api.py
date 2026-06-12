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
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

from flask import Blueprint, has_request_context, jsonify, request
from pydantic import ValidationError as PydanticValidationError
from spectree import Response, SpecTree
from spectree.models import InType, SecureType, SecurityScheme, SecuritySchemeData
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import attributes

from src.admin.api_schemas.publisher_properties import (
    coerce_stored_publisher_property_selectors,
    dump_publisher_property_selectors,
)
from src.admin.api_schemas.tenant_management import (
    BROADSTREET_CAMPAIGN_NAME_MACROS,
    GAM_LINE_ITEM_NAME_MACROS,
    GAM_ORDER_NAME_MACROS,
    WEBHOOK_EVENT_TYPES,
    AccountDetail,
    AccountSummary,
    AdapterCapabilitiesResponse,
    AdapterCapabilitiesSummary,
    AdapterCapabilityCheck,
    AdapterCatalogEntry,
    AdapterConfigResponse,
    AdapterSettingsSchemaResponse,
    AdapterSettingsValidationError,
    AdapterSettingsValidationResponse,
    AdapterStatusResponse,
    AdapterUnsupportedFeature,
    AllowedPublisherSelector,
    ApiError,
    ApproveWorkflowRequest,
    BroadstreetAdapterConfig,
    BroadstreetSettings,
    BuyerAdvertiserMapping,
    CreateAccountRequest,
    CreateBuyerAdvertiserMappingRequest,
    CreateWebhookSubscriptionRequest,
    CreativeBindingSchema,
    CreativeFormatSummary,
    DeleteSignalMappingResponse,
    DeleteWholesaleProductResponse,
    EnsureGamAdvertiserRequest,
    EnsureGamAdvertiserResponse,
    FormatIdRef,
    FreeWheelAdapterConfig,
    FreeWheelSettings,
    GAMAdapterConfig,
    GoogleAdManagerSettings,
    InventoryAdapterCapabilitiesResponse,
    InventoryExecutionSelector,
    InventorySelectorSummary,
    InventorySelectorTypeCapability,
    ListAccountsManagedResponse,
    ListAdaptersResponse,
    ListAuditLogResponse,
    ListBuyerAdvertiserMappingsResponse,
    ListCreativeFormatsForAuthoringQuery,
    ListCreativeFormatsForAuthoringResponse,
    ListGamAdvertisersResponse,
    ListInventorySelectorsResponse,
    ListMediaBuysResponse,
    ListRecentBuyersResponse,
    ListSignalCandidatesResponse,
    ListSignalMappingsResponse,
    ListSyncHistoryResponse,
    ListTenantsResponse,
    ListWebhooksResponse,
    ListWholesaleProductsResponse,
    ListWorkflowsResponse,
    LookupPublisherPropertiesRequest,
    MediaBuyDetail,
    MockAdapterConfig,
    PreviewAdapterRequest,
    PreviewAdapterResponse,
    ProvisionedPrincipalResponse,
    ProvisionTenantRequest,
    ProvisionTenantResponse,
    PublisherDomainSummary,
    PublisherPropertiesLookupResponse,
    PublisherPropertiesResponse,
    PublisherPropertySelector,
    PublisherPropertySummary,
    RecentBuyer,
    RefreshConflictResponse,
    RefreshResponse,
    RejectWorkflowRequest,
    SignalAdapterCapabilitiesResponse,
    SignalCandidateSummary,
    SignalCandidateTypeCapability,
    SignalMappingKindCapability,
    SignalMappingKindTargetingSemantics,
    SignalMappingRequest,
    SignalMappingResponse,
    SignalMappingValidationIssue,
    SignalMappingValidationResponse,
    SignalTargetingSemantics,
    SpringServeAdapterConfig,
    SpringServeSettings,
    TargetingValuesRefreshResponse,
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
    WholesaleCreativeFormat,
    WholesaleFormatBinding,
    WholesaleInventory,
    WholesaleInventoryExecution,
    WholesalePricingOptionResponse,
    WholesaleProductPreviewResponse,
    WholesaleProductRequest,
    WholesaleProductResponse,
    WholesaleProductValidationResponse,
    WholesaleValidationIssue,
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
    _classify_gam_message,
    _vendor_fault,
    preview_adapter,
    probe_adapter_connection,
)
from src.admin.services.catalog_webhook_events import (
    catalog_acl_notification_scope,
    publish_product_catalog_change,
    publish_product_record_catalog_change,
    publish_signal_catalog_change,
)
from src.admin.services.publisher_property_authorization import (
    seed_local_example_publisher_authorization_for_selectors,
    validate_publisher_property_selectors,
)
from src.admin.services.tenant_status_service import get_tenant_status, invalidate_status_cache
from src.core.database.database_session import get_db_session
from src.core.database.embedded_tenant_guard import EmbeddedTenantWriteError
from src.core.database.models import (
    PRODUCT_REPORTING_CAPABILITIES_DEFAULT,
    Account,
    AdapterConfig,
    AdvertiserRoutingRule,
    AuthorizedProperty,
    CurrencyLimit,
    FreeWheelInventory,
    GamAdvertiser,
    GAMInventory,
    InventoryProfile,
    MediaBuy,
    PricingOption,
    Principal,
    Product,
    PropertyTag,
    PublisherPartner,
    SpringServeInventory,
    SyncJob,
    Tenant,
    TenantSignal,
)
from src.core.database.repositories.account import AccountRepository
from src.core.database.repositories.adapter_config import AdapterConfigRepository
from src.core.database.repositories.currency_limit import CurrencyLimitRepository
from src.core.database.repositories.freewheel_inventory import FreeWheelInventoryRepository
from src.core.database.repositories.gam_sync import GAMSyncRepository
from src.core.database.repositories.inventory_profile import InventoryProfileRepository
from src.core.database.repositories.product import ProductRepository
from src.core.database.repositories.signal_usage import SignalUsageRepository
from src.core.database.repositories.springserve_inventory import SpringServeInventoryRepository
from src.core.database.repositories.tenant_config import TenantConfigRepository
from src.core.database.repositories.tenant_signal import TenantSignalRepository
from src.core.domain_config import get_tenant_url
from src.core.helpers.account_provisioning import gam_ensure_advertiser_companyservice
from src.core.inventory_profile_projection import (
    WHOLESALE_PROFILE_MANAGED_BY,
    default_wholesale_currency,
    inventory_profile_to_product_model,
    is_complete_inventory_profile,
    is_wholesale_owned_inventory_profile,
)
from src.core.security.url_validator import check_url_ssrf
from src.services.aao_lookup_service import get_publisher_partner_status
from src.services.agent_url_resolver import resolve_agent_url
from src.services.property_discovery_service import get_property_discovery_service
from src.services.protocol_change_webhooks import notify_account_status_changed
from src.services.recent_buyers_service import compute_recent_buyers
from src.services.targeting_values import (
    build_gam_inventory_discovery,
    sync_targeting_values_for_key,
    targeting_values_synced_empty,
)

logger = logging.getLogger(__name__)

# Create Blueprint
tenant_management_api = Blueprint("tenant_management_api", __name__, url_prefix="/api/v1/tenant-management")
_WHOLESALE_PROFILE_MANAGED_BY = WHOLESALE_PROFILE_MANAGED_BY

# OpenAPI spec is served by spectree under the blueprint's `path="docs"`:
#   spec:       {blueprint_prefix}/docs/openapi.json
#   Swagger UI: {blueprint_prefix}/docs/swagger
#   Redoc:      {blueprint_prefix}/docs/redoc
# In production the admin app is WSGI-mounted under /admin/, so the public URLs are
# /admin/api/v1/tenant-management/docs/{openapi.json,swagger,redoc}.
#
# Every endpoint below, including the generated docs/spec surface, is gated by
# ``require_tenant_management_api_key`` (``X-Tenant-Management-API-Key`` header).
# If you ever mount additional routes on this blueprint without that decorator,
# revisit this assumption.
spec = SpecTree(
    "flask",
    title="Sales Agent — Tenant Management API",
    version="v1",
    path="docs",
    openapi_url_prefix="",
    security_schemes=[
        SecurityScheme(
            name="TenantManagementApiKey",
            data=SecuritySchemeData(
                type=SecureType.API_KEY,
                name="X-Tenant-Management-API-Key",
                **{"in": InType.HEADER},
            ),
        )
    ],
    security={"TenantManagementApiKey": []},
)


require_tenant_management_api_key = require_api_key_auth(
    env_var="TENANT_MANAGEMENT_API_KEY",
    config_key="tenant_management_api_key",
    header="X-Tenant-Management-API-Key",
)


@tenant_management_api.before_request
def protect_tenant_management_docs():
    """Apply management API-key auth to the generated OpenAPI docs."""
    if "/docs/" not in request.path:
        return None

    @require_tenant_management_api_key
    def _authorized():
        return None

    return _authorized()


# ---------------------------------------------------------------------------
# Helpers shared by the new spectree endpoints
# ---------------------------------------------------------------------------


def _validated_json_payload() -> Any:
    """Return the Spectree-validated JSON payload attached to the Flask request."""
    return cast(Any, request).context.json


def _api_error(code: str, message: str, status: int, details: dict | None = None):
    """Build a (jsonified, status) tuple matching the :class:`ApiError` schema."""
    body = ApiError(error=code, message=message, details=details).model_dump(exclude_none=True)
    return jsonify(body), status


def _pydantic_error_details(exc: PydanticValidationError) -> list[dict[str, Any]]:
    """Return JSON-safe Pydantic error details for API responses."""

    safe_errors: list[dict[str, Any]] = []
    for error in exc.errors():
        safe_errors.append({key: value for key, value in error.items() if key in {"type", "loc", "msg", "url"}})
    return json.loads(json.dumps(safe_errors, default=str))


def _template_macro_names(macros: list[dict[str, str]]) -> set[str]:
    """Return supported macro names from the schema metadata."""

    return {macro["name"] for macro in macros}


def _unknown_template_macros(template: str | None, allowed: set[str]) -> list[str]:
    """Find unsupported ``{macro}`` names, including fallback alternatives."""

    if not template:
        return []

    unknown: set[str] = set()
    for match in re.finditer(r"\{([^}]+)\}", template):
        for macro_name in match.group(1).split("|"):
            stripped = macro_name.strip()
            if stripped and stripped not in allowed:
                unknown.add(stripped)
    return sorted(unknown)


def _adapter_settings_schema_response(
    adapter_type: str,
    model: type[GoogleAdManagerSettings]
    | type[FreeWheelSettings]
    | type[BroadstreetSettings]
    | type[SpringServeSettings],
    template_macros: dict[str, list[dict[str, str]]],
) -> AdapterSettingsSchemaResponse:
    """Return a JSON Schema plus explicit template macro metadata."""

    return AdapterSettingsSchemaResponse(
        type=adapter_type,
        **{"schema": model.model_json_schema()},
        template_macros=template_macros,
    )


def _settings_validation_response(
    field_templates: dict[str, str | None],
    field_macros: dict[str, list[dict[str, str]]],
) -> AdapterSettingsValidationResponse:
    """Validate naming-template fields and return preview strings."""

    from src.core.utils.naming import apply_naming_template

    sample_contexts = {
        "order_name_template": {
            "campaign_name": "Spring Launch",
            "brand_name": "example.com",
            "promoted_offering": "example.com",
            "auto_name": "Example Spring Launch",
            "date_range": "May 01-31, 2026",
            "month_year": "May 2026",
            "media_buy_id": "gam_ab12cd34",
            "buyer_ref": "gam_ab12cd34",
            "package_count": "2",
            "start_date": "2026-05-01",
            "end_date": "2026-05-31",
        },
        "line_item_name_template": {
            "order_name": "Example Spring Launch [mb_gam_ab12cd34]",
            "product_name": "Homepage Sports",
            "package_name": "Homepage Sports - May",
            "package_index": "1",
        },
        "campaign_name_template": {
            "po_number": "PO-12345",
            "product_name": "Homepage Display",
            "advertiser_name": "Example Advertiser",
            "timestamp": "20260525_120000",
        },
    }

    errors: list[AdapterSettingsValidationError] = []
    preview: dict[str, str] = {}
    for field_name, template in field_templates.items():
        allowed = _template_macro_names(field_macros[field_name])
        unknown = _unknown_template_macros(template, allowed)
        if unknown:
            errors.append(
                AdapterSettingsValidationError(
                    field=field_name,
                    message=f"Unsupported macro(s): {', '.join(unknown)}",
                )
            )
            continue
        if template:
            preview[field_name] = apply_naming_template(template, sample_contexts[field_name])

    return AdapterSettingsValidationResponse(valid=not errors, errors=errors, preview=preview)


def _canonical_agent_url(value: str | None) -> str | None:
    """Canonicalize an agent URL for effective-URL change detection."""
    if value is None:
        return None
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").lower()
    try:
        parsed_port = parsed.port
    except ValueError:
        parsed_port = None
    port = f":{parsed_port}" if parsed_port else ""
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), f"{host}{port}", path, parsed.query, ""))


def _agent_urls_match(left: str | None, right: str | None) -> bool:
    """Compare effective agent URLs using canonical host/path normalization."""
    return _canonical_agent_url(left) == _canonical_agent_url(right)


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


def _creative_approval_from_tenant(tenant: Tenant) -> str:
    return {
        "auto-approve": "auto",
        "require-human": "manual",
        "ai-powered": "ai",
    }.get(tenant.approval_mode or "require-human", "manual")


def _media_buy_approval_from_tenant(tenant: Tenant) -> str:
    return "manual" if tenant.human_review_required else "auto"


def _tenant_creative_approval_mode(value: str) -> str:
    return {
        "auto": "auto-approve",
        "manual": "require-human",
        "ai": "ai-powered",
    }[value]


def _tenant_media_buy_manual_approval_required(value: str | None, *, default: bool) -> bool:
    return default if value is None else value == "manual"


def _set_adapter_manual_approval_required(adapter: AdapterConfig, manual_approval_required: bool) -> None:
    if adapter.adapter_type == "google_ad_manager":
        adapter.gam_manual_approval_required = manual_approval_required
    elif adapter.adapter_type == "mock":
        adapter.mock_manual_approval_required = manual_approval_required
    elif _connection_config_model(adapter.adapter_type) is not None:
        config_json = dict(adapter.config_json or {})
        config_json["manual_approval_required"] = manual_approval_required
        adapter.config_json = config_json
        attributes.flag_modified(adapter, "config_json")


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
        creative_approval=_creative_approval_from_tenant(tenant),
        media_buy_approval=_media_buy_approval_from_tenant(tenant),
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
            "rate_currency": adapter.rate_currency,
            "demand_class": adapter.demand_class,
            "enable_key_value_targeting": adapter.enable_key_value_targeting,
        }
    raise ValueError(f"Unsupported adapter type: {type(adapter).__name__}")


def _persist_adapter_config(
    session,
    tenant_id: str,
    adapter: AdapterConfigSchema,
    manual_approval_required: bool | None = None,
) -> AdapterConfig:
    """Create or replace the AdapterConfig row for a tenant from a validated schema."""
    stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id)
    existing = session.scalars(stmt).first()
    if existing is not None:
        session.delete(existing)
        session.flush()

    adapter_manual_approval_required = manual_approval_required if manual_approval_required is not None else False
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
            gam_manual_approval_required=adapter_manual_approval_required,
        )
        # Encryption is wired via the property setter (see models.py:AdapterConfig).
        if sa_json is not None:
            ac.gam_service_account_json = sa_json
    elif isinstance(adapter, MockAdapterConfig):
        ac = AdapterConfig(
            tenant_id=tenant_id,
            adapter_type="mock",
            mock_dry_run=adapter.dry_run,
            mock_manual_approval_required=adapter_manual_approval_required,
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
            manual_approval_required=adapter_manual_approval_required,
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
            manual_approval_required=adapter_manual_approval_required,
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
            rate_currency=adapter.rate_currency,
            demand_class=adapter.demand_class,
            enable_key_value_targeting=adapter.enable_key_value_targeting,
            manual_approval_required=adapter_manual_approval_required,
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


def _adapter_probe_config(adapter: AdapterConfig) -> dict[str, Any]:
    """Build the adapter probe/config dict expected by adapter services."""
    if adapter.adapter_type == "google_ad_manager":
        return AdapterConfigRepository.get_gam_config(adapter)
    if adapter.adapter_type == "mock":
        return {"dry_run": bool(adapter.mock_dry_run)}
    model = _connection_config_model(adapter.adapter_type)
    if model is not None:
        validated = model(**dict(adapter.config_json or {}))
        return {field: getattr(validated, field) for field in model.model_fields}
    return dict(adapter.config_json or {})


def _adapter_capability_checks(adapter_type: str, probe: ProbeResult) -> list[AdapterCapabilityCheck]:
    """Return explicit capability check results for ``test-connection``.

    Successful authentication is not proof that a write capability exists.
    For GAM advertiser creation, the proof is a successful
    ``POST /gam/advertisers:ensure`` call that actually creates an advertiser.
    """
    checks: list[AdapterCapabilityCheck] = [
        AdapterCapabilityCheck(
            capability="connect",
            status="passed" if probe.success else "failed",
            message=probe.error_message,
            error_code=probe.error_code,
            remediation=probe.remediation,
            details=probe.details or None,
        )
    ]
    if adapter_type == "google_ad_manager":
        checks.append(
            AdapterCapabilityCheck(
                capability="create_gam_advertiser",
                status="not_checked",
                message=(
                    "test-connection is non-mutating. Use POST "
                    "/tenants/{tenant_id}/gam/advertisers:ensure with a missing advertiser "
                    "to prove create permission."
                ),
            )
        )
    return checks


def _surface_urls(tenant_id: str, tenant_subdomain: str, request_base_url: str | None = None) -> tuple[str, str, str]:
    """Return ``(mcp_url, a2a_url, admin_url_path)`` for a tenant.

    Prefer tenant-subdomain URLs so callers can persist a buyer-protocol
    endpoint without duplicating salesagent routing rules.
    """
    base = os.environ.get("ADCP_BASE_URL", "").rstrip("/")
    if not base and os.environ.get("SALES_AGENT_DOMAIN"):
        base = get_tenant_url(tenant_subdomain, protocol=None) or ""
    if not base:
        base = (request_base_url or "").rstrip("/")
    admin_path = f"/tenant/{tenant_id}"
    return f"{base}/mcp/", f"{base}/a2a", admin_path


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

_ADAPTER_CONTRACT_VERSION = "2026-05-01"

_ADAPTER_CONTRACT_PROFILES: dict[str, dict[str, Any]] = {
    "google_ad_manager": {
        "sync_streams": ["inventory", "custom_targeting", "advertisers"],
        "supported_object_types": [
            "ad_unit",
            "placement",
            "creative_format",
            "targeting_key",
            "targeting_value",
            "advertiser",
        ],
        "supported_signal_types": ["custom_targeting", "audience_segment"],
        "supports_forecasting": True,
        "supports_pricing_recommendations": False,
        "supported_pricing_models": ["cpm", "vcpm", "cpc", "flat_rate"],
        "candidate_generation": "hierarchy_rollup_recent_delivery",
        "search_limits": {"default_page_size": 50, "max_page_size": 100},
        "normalization_notes": [
            "GAM 1x1 and fluid size declarations are evidence for classification, not final creative formats.",
            "Large networks should use on-demand search and curated candidates instead of full raw ad-unit setup flows.",
        ],
    },
    "freewheel": {
        "sync_streams": ["inventory", "reporting"],
        "supported_object_types": ["placement", "package", "network", "creative_profile", "advertiser"],
        "supported_signal_types": ["custom_targeting", "audience_segment"],
        "supports_forecasting": False,
        "supports_pricing_recommendations": False,
        "candidate_generation": "placement_package_rollup",
        "search_limits": {"default_page_size": 50, "max_page_size": 100},
        "normalization_notes": ["FreeWheel inventory setup is normalized around placement/package concepts."],
    },
    "springserve": {
        "sync_streams": ["inventory", "reporting"],
        "supported_object_types": ["supply_tag", "zone", "demand_partner", "creative_format"],
        "supported_signal_types": ["key_value", "audience_segment"],
        "supports_forecasting": False,
        "supports_pricing_recommendations": False,
        "candidate_generation": "supply_tag_rollup",
        "search_limits": {"default_page_size": 50, "max_page_size": 100},
        "normalization_notes": [
            "SpringServe setup is normalized around supply tags/zones and demand-partner mappings."
        ],
    },
    "mock": {
        "sync_streams": [],
        "supported_object_types": ["placement", "package", "creative_format", "audience_segment", "custom_targeting"],
        "supported_signal_types": ["custom_targeting", "audience_segment"],
        "supports_forecasting": True,
        "supports_pricing_recommendations": True,
        "candidate_generation": "deterministic_sample_objects",
        "search_limits": {"default_page_size": 50, "max_page_size": 100},
        "normalization_notes": ["Mock contracts return deterministic sample objects for local and CI setup flows."],
    },
    "broadstreet": {
        "sync_streams": [],
        "supported_object_types": ["zone", "advertisement", "campaign", "creative_size"],
        "supported_signal_types": [],
        "supports_forecasting": False,
        "supports_pricing_recommendations": False,
        "candidate_generation": "zone_creative_size_rollup",
        "search_limits": {"default_page_size": 50, "max_page_size": 100},
        "normalization_notes": ["Broadstreet setup is normalized around zones and supported creative sizes."],
    },
}

_ADAPTER_FEATURE_CONTRACTS: dict[str, dict[str, str]] = {
    "inventory_sync": {
        "reason": "Sales Agent cannot import inventory objects from this adapter.",
        "remediation": "Use manually configured products until inventory sync support is added for this adapter.",
    },
    "reporting": {
        "reason": "Sales Agent cannot import reporting data from this adapter.",
        "remediation": "Use external reporting exports until scheduled reporting sync support is added.",
    },
    "realtime_reporting": {
        "reason": "Sales Agent does not expose low-latency reporting reads for this adapter.",
        "remediation": "Use scheduled reporting sync or the ad server's native reporting UI.",
    },
    "forecasting": {
        "reason": "Sales Agent does not expose an adapter-backed forecast endpoint for this adapter.",
        "remediation": "Use historical delivery summaries or the ad server's native forecasting tools.",
    },
    "pricing_recommendations": {
        "reason": "Sales Agent does not compute adapter-specific pricing recommendations for this adapter.",
        "remediation": "Configure pricing manually or use an external pricing workflow.",
    },
    "webhooks": {
        "reason": "Sales Agent cannot subscribe to adapter-native change events for this adapter.",
        "remediation": "Poll status and sync-history endpoints until webhook support is available.",
    },
    "custom_targeting": {
        "reason": "Sales Agent cannot import custom targeting keys or values for this adapter.",
        "remediation": "Create buyer-facing signals manually or use broad inventory targeting only.",
    },
    "audiences": {
        "reason": "Sales Agent cannot import audience segments for this adapter.",
        "remediation": "Use contextual signals or configure audience mappings outside Sales Agent.",
    },
}


def _tenant_management_url(path: str) -> str:
    """Return a script-root aware tenant-management API URL path."""
    script_root = request.script_root.rstrip("/") if has_request_context() else ""
    return f"{script_root}/api/v1/tenant-management{path}"


def _canonical_catalog_adapter_type(adapter_type: str) -> str | None:
    """Resolve public adapter aliases to catalog keys."""
    normalized = adapter_type.lower()
    if normalized == "gam":
        normalized = "google_ad_manager"
    if normalized not in _ADAPTER_CATALOG_METADATA or normalized not in _ADAPTER_CONFIG_TYPED:
        return None
    return normalized


def _adapter_capabilities_summary(caps_dataclass: Any | None) -> AdapterCapabilitiesSummary:
    if caps_dataclass is None:
        return AdapterCapabilitiesSummary()
    return AdapterCapabilitiesSummary(
        supports_inventory_sync=caps_dataclass.supports_inventory_sync,
        supports_inventory_profiles=caps_dataclass.supports_inventory_profiles,
        inventory_entity_label=caps_dataclass.inventory_entity_label,
        supports_custom_targeting=caps_dataclass.supports_custom_targeting,
        supports_geo_targeting=caps_dataclass.supports_geo_targeting,
        supports_dynamic_products=caps_dataclass.supports_dynamic_products,
        supported_pricing_models=list(caps_dataclass.supported_pricing_models or []),
        supports_webhooks=caps_dataclass.supports_webhooks,
        supports_realtime_reporting=caps_dataclass.supports_realtime_reporting,
        supports_reporting_sync=caps_dataclass.supports_reporting_sync,
        reporting_bundled_with_inventory=caps_dataclass.reporting_bundled_with_inventory,
    )


def _unsupported_features(
    profile: dict[str, Any], summary: AdapterCapabilitiesSummary
) -> list[AdapterUnsupportedFeature]:
    supported_signal_types = set(_supported_signal_types(profile, summary))
    feature_checks = {
        "inventory_sync": summary.supports_inventory_sync,
        "reporting": summary.supports_reporting_sync
        or summary.supports_realtime_reporting
        or summary.reporting_bundled_with_inventory,
        "realtime_reporting": summary.supports_realtime_reporting,
        "forecasting": bool(profile.get("supports_forecasting")),
        "pricing_recommendations": bool(profile.get("supports_pricing_recommendations")),
        "webhooks": summary.supports_webhooks,
        "custom_targeting": summary.supports_custom_targeting,
        "audiences": "audience_segment" in supported_signal_types,
    }
    unsupported_features: list[AdapterUnsupportedFeature] = []
    for feature, supported in feature_checks.items():
        if supported:
            continue
        contract = _ADAPTER_FEATURE_CONTRACTS[feature]
        unsupported_features.append(
            AdapterUnsupportedFeature(
                feature=feature,
                reason=contract["reason"],
                remediation=contract["remediation"],
            )
        )
    return unsupported_features


def _supported_signal_types(profile: dict[str, Any], summary: AdapterCapabilitiesSummary) -> list[str]:
    """Return signal types only when the runtime adapter capability supports them."""
    if not summary.supports_custom_targeting:
        return []
    return list(profile.get("supported_signal_types", []))


def _build_adapter_capabilities(adapter_type: str, adapter_class: Any) -> AdapterCapabilitiesResponse:
    caps = _adapter_capabilities_summary(getattr(adapter_class, "capabilities", None))
    profile = _ADAPTER_CONTRACT_PROFILES.get(adapter_type, {})
    caps_data = caps.model_dump()
    caps_data["supported_pricing_models"] = list(
        caps.supported_pricing_models or profile.get("supported_pricing_models", [])
    )
    supported_signal_types = _supported_signal_types(profile, caps)
    return AdapterCapabilitiesResponse(
        **caps_data,
        type=adapter_type,
        contract_version=_ADAPTER_CONTRACT_VERSION,
        supports_audiences="audience_segment" in supported_signal_types,
        supports_forecasting=bool(profile.get("supports_forecasting")),
        supports_reporting=caps.supports_reporting_sync
        or caps.supports_realtime_reporting
        or caps.reporting_bundled_with_inventory,
        supports_pricing_recommendations=bool(profile.get("supports_pricing_recommendations")),
        sync_streams=list(profile.get("sync_streams", [])),
        supported_object_types=list(profile.get("supported_object_types", [])),
        supported_signal_types=supported_signal_types,
        unsupported_features=_unsupported_features(profile, caps),
    )


_WHOLESALE_SELECTOR_CAPABILITIES: dict[str, list[InventorySelectorTypeCapability]] = {
    "google_ad_manager": [
        InventorySelectorTypeCapability(
            selector_type="placement",
            label="GAM Placement",
            description="Google Ad Manager placement ID.",
            option_schema={"type": "object", "properties": {}},
        ),
        InventorySelectorTypeCapability(
            selector_type="ad_unit",
            label="GAM Ad Unit",
            description="Google Ad Manager ad unit ID.",
            supports_parent_filter=True,
            option_schema={
                "type": "object",
                "properties": {"include_descendants": {"type": "boolean", "default": True}},
            },
        ),
    ],
    "freewheel": [
        InventorySelectorTypeCapability(selector_type="site", label="FreeWheel Site"),
        InventorySelectorTypeCapability(
            selector_type="site_section",
            label="FreeWheel Site Section",
            supports_parent_filter=True,
        ),
        InventorySelectorTypeCapability(selector_type="site_group", label="FreeWheel Site Group"),
        InventorySelectorTypeCapability(selector_type="series", label="FreeWheel Series"),
        InventorySelectorTypeCapability(selector_type="video_group", label="FreeWheel Video Group"),
        InventorySelectorTypeCapability(selector_type="ad_unit_package", label="FreeWheel Ad Unit Package"),
        InventorySelectorTypeCapability(
            selector_type="ad_unit_node",
            label="FreeWheel Ad Unit Node",
            supports_parent_filter=True,
        ),
        InventorySelectorTypeCapability(selector_type="standard_attribute", label="FreeWheel Standard Attribute"),
    ],
    "springserve": [
        InventorySelectorTypeCapability(selector_type="supply_partner", label="SpringServe Supply Partner"),
        InventorySelectorTypeCapability(
            selector_type="supply_router",
            label="SpringServe Supply Router",
            supports_parent_filter=True,
        ),
        InventorySelectorTypeCapability(
            selector_type="supply_tag",
            label="SpringServe Supply Tag",
            supports_parent_filter=True,
        ),
        InventorySelectorTypeCapability(selector_type="key", label="SpringServe Key"),
        InventorySelectorTypeCapability(
            selector_type="value_list",
            label="SpringServe Value List",
            supports_parent_filter=True,
        ),
    ],
    "broadstreet": [
        InventorySelectorTypeCapability(selector_type="zone", label="Broadstreet Zone"),
    ],
    "mock": [
        InventorySelectorTypeCapability(selector_type="mock_inventory", label="Mock Inventory"),
    ],
}


_SIGNAL_MAPPING_CAPABILITIES: dict[str, list[SignalMappingKindCapability]] = {
    "google_ad_manager": [
        SignalMappingKindCapability(
            mapping_kind="audience_segment",
            label="GAM Audience Segment",
            description="Google Ad Manager audience segment exposed as a binary buyer-facing signal.",
            candidate_type="audience_segment",
            adapter_config_schema={
                "type": "object",
                "required": ["kind", "segment_id"],
                "properties": {
                    "type": {"const": "passthrough"},
                    "kind": {"const": "audience_segment"},
                    "segment_id": {"type": "string"},
                    "mode": {"enum": ["include", "exclude"]},
                },
            },
        ),
        SignalMappingKindCapability(
            mapping_kind="custom_key_value",
            label="GAM Custom Targeting Value",
            description="Google Ad Manager custom targeting key/value exposed as a binary buyer-facing signal.",
            candidate_type="custom_targeting_value",
            supports_parent_filter=True,
            adapter_config_schema={
                "type": "object",
                "required": ["kind", "key_id", "value_id"],
                "properties": {
                    "type": {"const": "passthrough"},
                    "kind": {"const": "custom_key_value"},
                    "key_id": {"type": "string"},
                    "value_id": {"type": "string"},
                    "mode": {"enum": ["include", "exclude"]},
                },
            },
        ),
        SignalMappingKindCapability(
            mapping_kind="gam_targeting_groups",
            label="GAM Targeting Groups",
            description="Advanced GAM targeting widget groups exposed as one exclusive signal.",
            candidate_type=None,
            supports_search=False,
            adapter_config_schema={
                "type": "object",
                "required": ["kind", "groups"],
                "properties": {
                    "type": {"const": "passthrough"},
                    "kind": {"const": "gam_targeting_groups"},
                    "groups": {
                        "type": "array",
                        "minItems": 1,
                        "description": "OR-ed targeting groups. Criteria within one group are AND-ed.",
                        "items": {
                            "type": "object",
                            "required": ["criteria"],
                            "properties": {
                                "criteria": {
                                    "type": "array",
                                    "minItems": 1,
                                    "description": "AND-ed criteria for one group.",
                                    "items": {
                                        "type": "object",
                                        "required": ["keyId", "values"],
                                        "properties": {
                                            "keyId": {
                                                "type": "string",
                                                "description": (
                                                    "GAM custom targeting key ID. Browse keys with "
                                                    "candidate_type=custom_targeting_key."
                                                ),
                                            },
                                            "values": {
                                                "type": "array",
                                                "minItems": 1,
                                                "description": (
                                                    "OR-ed GAM custom targeting value IDs or names under keyId. "
                                                    "Browse values with candidate_type=custom_targeting_value "
                                                    "and parent_id={keyId}."
                                                ),
                                                "items": {"type": "string"},
                                            },
                                            "exclude": {
                                                "type": "boolean",
                                                "default": False,
                                                "description": "When true, negates this criterion in GAM targeting.",
                                            },
                                        },
                                        "additionalProperties": False,
                                    },
                                }
                            },
                            "additionalProperties": False,
                        },
                    },
                    "mode": {"enum": ["include", "exclude"]},
                },
                "x-authoring": {
                    "composition_model": "groups",
                    "canonical_criterion_casing": "camelCase",
                    "operators": {
                        "groups": "OR",
                        "criteria": "AND",
                        "values": "OR",
                    },
                    "candidate_browser": {
                        "key_candidate_type": "custom_targeting_key",
                        "value_candidate_type": "custom_targeting_value",
                        "value_parent_filter": "keyId",
                    },
                },
            },
        ),
    ],
    "freewheel": [
        SignalMappingKindCapability(
            mapping_kind="freewheel_viewership_profile",
            label="FreeWheel Viewership Profile",
            description="FreeWheel viewership profile ID exposed as a binary buyer-facing signal.",
            candidate_type="viewership_profile",
            adapter_config_schema={
                "type": "object",
                "required": ["kind", "profile_id"],
                "properties": {
                    "type": {"const": "passthrough"},
                    "kind": {"const": "freewheel_viewership_profile"},
                    "profile_id": {"type": "string"},
                    "mode": {"enum": ["include"]},
                },
            },
        ),
        SignalMappingKindCapability(
            mapping_kind="freewheel_audience_item",
            label="FreeWheel Audience Item",
            description="FreeWheel Data Suite audience item ID exposed as a binary buyer-facing signal.",
            candidate_type=None,
            supports_search=False,
            adapter_config_schema={
                "type": "object",
                "required": ["kind", "item_id"],
                "properties": {
                    "type": {"const": "passthrough"},
                    "kind": {"const": "freewheel_audience_item"},
                    "item_id": {"type": "string"},
                    "mode": {"enum": ["include"]},
                },
            },
        ),
        SignalMappingKindCapability(
            mapping_kind="freewheel_custom_kv",
            label="FreeWheel Custom Criterion",
            description="FreeWheel custom key/value exposed as a binary buyer-facing signal.",
            candidate_type=None,
            supports_search=False,
            adapter_config_schema={
                "type": "object",
                "required": ["kind", "key", "value_id"],
                "properties": {
                    "type": {"const": "passthrough"},
                    "kind": {"const": "freewheel_custom_kv"},
                    "key": {"type": "string"},
                    "value_id": {"type": "string"},
                    "mode": {"enum": ["include"]},
                },
            },
        ),
    ],
    "springserve": [
        SignalMappingKindCapability(
            mapping_kind="springserve_value_list",
            label="SpringServe Value List",
            description="SpringServe key/value-list pair exposed as a binary buyer-facing signal.",
            candidate_type="value_list",
            supports_parent_filter=True,
            adapter_config_schema={
                "type": "object",
                "required": ["kind", "key_id", "value_list_id"],
                "properties": {
                    "type": {"const": "passthrough"},
                    "kind": {"const": "springserve_value_list"},
                    "key_id": {"type": "string"},
                    "key_name": {"type": "string"},
                    "value_list_id": {"type": "string"},
                },
            },
        ),
    ],
    "broadstreet": [],
    "mock": [],
}


def _candidate_type_capability(
    *,
    candidate_type: str,
    label: str,
    description: str | None = None,
    mapping_kind: str | None = None,
    parent_candidate_type: str | None = None,
    child_candidate_types: list[str] | None = None,
    supports_search: bool = True,
    supports_pagination: bool = True,
    supports_parent_filter: bool = False,
) -> SignalCandidateTypeCapability:
    directly_mappable = mapping_kind is not None
    return SignalCandidateTypeCapability(
        candidate_type=candidate_type,
        label=label,
        description=description,
        mapping_kind=mapping_kind,
        directly_mappable=directly_mappable,
        browse_only=not directly_mappable,
        parent_candidate_type=parent_candidate_type,
        child_candidate_types=list(child_candidate_types or []),
        supports_search=supports_search,
        supports_pagination=supports_pagination,
        supports_parent_filter=supports_parent_filter,
    )


_SIGNAL_CANDIDATE_TYPE_CAPABILITIES: dict[str, list[SignalCandidateTypeCapability]] = {
    "google_ad_manager": [
        _candidate_type_capability(
            candidate_type="audience_segment",
            label="GAM Audience Segment",
            description="Directly mappable Google Ad Manager audience segment.",
            mapping_kind="audience_segment",
        ),
        _candidate_type_capability(
            candidate_type="custom_targeting_key",
            label="GAM Custom Targeting Key",
            description="Browse-only parent key used to discover custom targeting values.",
            child_candidate_types=["custom_targeting_value"],
        ),
        _candidate_type_capability(
            candidate_type="custom_targeting_value",
            label="GAM Custom Targeting Value",
            description="Directly mappable value under a custom targeting key.",
            mapping_kind="custom_key_value",
            parent_candidate_type="custom_targeting_key",
            supports_parent_filter=True,
        ),
    ],
    "springserve": [
        _candidate_type_capability(
            candidate_type="key",
            label="SpringServe Key",
            description="Browse-only parent key used to discover value lists.",
            child_candidate_types=["value_list"],
        ),
        _candidate_type_capability(
            candidate_type="value_list",
            label="SpringServe Value List",
            description="Directly mappable value list under a SpringServe key.",
            mapping_kind="springserve_value_list",
            parent_candidate_type="key",
            supports_parent_filter=True,
        ),
    ],
    "freewheel": [
        _candidate_type_capability(
            candidate_type="viewership_profile",
            label="FreeWheel Viewership Profile",
            description="Directly mappable FreeWheel viewership profile candidate.",
            mapping_kind="freewheel_viewership_profile",
        ),
    ],
    "broadstreet": [],
    "mock": [],
}


def _mapping_targeting_semantics(
    *,
    mapping_kind: str,
    supports_composed: bool,
    composition_models: list[str],
    supported_modes: list[str],
    buyer_targeting_fields: list[str],
    participates_in_composed_authoring: bool,
    exclusive_with_other_signals: bool = False,
    notes: str | None = None,
) -> SignalMappingKindTargetingSemantics:
    return SignalMappingKindTargetingSemantics(
        mapping_kind=mapping_kind,
        supports_composed=supports_composed,
        composition_models=composition_models,
        supported_modes=supported_modes,
        buyer_targeting_fields=buyer_targeting_fields,
        participates_in_composed_authoring=participates_in_composed_authoring,
        exclusive_with_other_signals=exclusive_with_other_signals,
        notes=notes,
    )


_AUDIENCE_TARGETING_FIELDS = ["audience_include", "audience_exclude"]

_SIGNAL_TARGETING_SEMANTICS: dict[str, SignalTargetingSemantics] = {
    "google_ad_manager": SignalTargetingSemantics(
        supports_composed=True,
        composition_models=["flat_criteria", "groups"],
        supported_modes=["include", "exclude"],
        buyer_targeting_fields=_AUDIENCE_TARGETING_FIELDS,
        mapping_kinds=[
            _mapping_targeting_semantics(
                mapping_kind="audience_segment",
                supports_composed=True,
                composition_models=["passthrough", "flat_criteria"],
                supported_modes=["include", "exclude"],
                buyer_targeting_fields=_AUDIENCE_TARGETING_FIELDS,
                participates_in_composed_authoring=True,
            ),
            _mapping_targeting_semantics(
                mapping_kind="custom_key_value",
                supports_composed=True,
                composition_models=["passthrough", "flat_criteria"],
                supported_modes=["include", "exclude"],
                buyer_targeting_fields=_AUDIENCE_TARGETING_FIELDS,
                participates_in_composed_authoring=True,
            ),
            _mapping_targeting_semantics(
                mapping_kind="gam_targeting_groups",
                supports_composed=True,
                composition_models=["groups"],
                supported_modes=["include", "exclude"],
                buyer_targeting_fields=_AUDIENCE_TARGETING_FIELDS,
                participates_in_composed_authoring=True,
                exclusive_with_other_signals=True,
                notes="Group-based GAM targeting signals must be selected alone in a media-buy audience list.",
            ),
        ],
    ),
    "springserve": SignalTargetingSemantics(
        supports_composed=False,
        composition_models=["passthrough"],
        supported_modes=["include", "exclude"],
        buyer_targeting_fields=_AUDIENCE_TARGETING_FIELDS,
        mapping_kinds=[
            _mapping_targeting_semantics(
                mapping_kind="springserve_value_list",
                supports_composed=False,
                composition_models=["passthrough"],
                supported_modes=["include", "exclude"],
                buyer_targeting_fields=_AUDIENCE_TARGETING_FIELDS,
                participates_in_composed_authoring=False,
                notes="SpringServe resolves one publisher-curated value list per signal; composed signals are not supported.",
            ),
        ],
    ),
    "freewheel": SignalTargetingSemantics(
        supports_composed=True,
        composition_models=["flat_criteria"],
        supported_modes=["include"],
        buyer_targeting_fields=["audience_include"],
        mapping_kinds=[
            _mapping_targeting_semantics(
                mapping_kind="freewheel_viewership_profile",
                supports_composed=True,
                composition_models=["passthrough", "flat_criteria"],
                supported_modes=["include"],
                buyer_targeting_fields=["audience_include"],
                participates_in_composed_authoring=True,
            ),
            _mapping_targeting_semantics(
                mapping_kind="freewheel_audience_item",
                supports_composed=True,
                composition_models=["passthrough", "flat_criteria"],
                supported_modes=["include"],
                buyer_targeting_fields=["audience_include"],
                participates_in_composed_authoring=True,
            ),
            _mapping_targeting_semantics(
                mapping_kind="freewheel_custom_kv",
                supports_composed=True,
                composition_models=["passthrough", "flat_criteria"],
                supported_modes=["include"],
                buyer_targeting_fields=["audience_include"],
                participates_in_composed_authoring=True,
                notes="FreeWheel has no native signal exclusion; audience_exclude references are rejected.",
            ),
        ],
    ),
    "broadstreet": SignalTargetingSemantics(),
    "mock": SignalTargetingSemantics(),
}

_DEFAULT_SIGNAL_CANDIDATE_TYPE: dict[str, str] = {
    "google_ad_manager": "audience_segment",
    "springserve": "key",
    "freewheel": "viewership_profile",
}


def _tenant_adapter_type(tenant: Tenant, adapter: AdapterConfig | None = None) -> str:
    """Return the canonical adapter type for a tenant."""
    configured = adapter.adapter_type if adapter is not None else tenant.ad_server
    return _canonical_catalog_adapter_type(configured or "mock") or configured or "mock"


def _require_tenant_for_authoring(session, tenant_id: str) -> tuple[Tenant | None, AdapterConfig | None, Any]:
    """Load a tenant plus adapter row for wholesale-product authoring."""
    tenant = TenantConfigRepository(session, tenant_id).get_tenant()
    if tenant is None:
        return None, None, _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)
    adapter = AdapterConfigRepository(session, tenant_id).find_by_tenant()
    return tenant, adapter, None


def _supported_selector_types(adapter_type: str) -> set[str]:
    return {cap.selector_type for cap in _WHOLESALE_SELECTOR_CAPABILITIES.get(adapter_type, [])}


def _supported_signal_mapping_kinds(adapter_type: str) -> set[str]:
    return {cap.mapping_kind for cap in _SIGNAL_MAPPING_CAPABILITIES.get(adapter_type, [])}


def _supported_signal_candidate_types(adapter_type: str) -> set[str]:
    return {cap.candidate_type for cap in _SIGNAL_CANDIDATE_TYPE_CAPABILITIES.get(adapter_type, [])}


def _format_id_dict(format_id: FormatIdRef | dict[str, Any]) -> dict[str, Any]:
    from src.core.canonical_formats import canonicalize_format_ref

    canonical = canonicalize_format_ref(format_id)
    result: dict[str, Any] = {"agent_url": str(canonical["agent_url"]), "id": str(canonical["id"])}
    for key in ("width", "height", "duration_ms"):
        if canonical.get(key) is not None:
            result[key] = canonical[key]
    return result


def _creative_format_id_dicts(creative_formats: list[WholesaleCreativeFormat]) -> list[dict[str, Any]]:
    return [_format_id_dict(fmt.format_id) for fmt in creative_formats]


def _wholesale_creative_format_dict(creative_format: WholesaleCreativeFormat) -> dict[str, Any]:
    data = creative_format.model_dump(mode="json")
    data["format_id"] = _format_id_dict(creative_format.format_id)
    return data


def _wholesale_format_binding_dict(binding: WholesaleFormatBinding) -> dict[str, Any]:
    data = binding.model_dump(mode="json")
    data["format_id"] = _format_id_dict(binding.format_id)
    return data


def _publisher_property_dicts(publisher_properties: list[PublisherPropertySelector]) -> list[dict[str, Any]]:
    return dump_publisher_property_selectors(publisher_properties)


def _execution_inventory_config(execution: WholesaleInventoryExecution) -> dict[str, Any]:
    """Persist execution selectors in legacy GAM keys plus generic selector form."""
    selectors = [selector.model_dump(mode="json") for selector in execution.selectors]
    config: dict[str, Any] = {
        "adapter": execution.adapter,
        "selectors": selectors,
        "format_bindings": [_wholesale_format_binding_dict(binding) for binding in execution.format_bindings],
    }
    if execution.adapter in {"google_ad_manager", "gam"}:
        ad_units = [selector.external_id for selector in execution.selectors if selector.selector_type == "ad_unit"]
        placements = [selector.external_id for selector in execution.selectors if selector.selector_type == "placement"]
        if ad_units:
            config["ad_units"] = ad_units
        if placements:
            config["placements"] = placements
        if any(selector.options.get("include_descendants") for selector in execution.selectors):
            config["include_descendants"] = True
    return config


def _wholesale_implementation_config(req: WholesaleProductRequest, adapter_type: str) -> dict[str, Any]:
    config = _execution_inventory_config(req.inventory.execution)
    config["adapter"] = adapter_type
    config["status"] = req.status
    config["creative_formats"] = [_wholesale_creative_format_dict(fmt) for fmt in req.inventory.creative_formats]
    config["targeting_capabilities"] = req.targeting_capabilities
    config["optimization_capabilities"] = req.optimization_capabilities
    return config


def _product_status(product: Product) -> str:
    if product.archived_at is not None:
        return "archived"
    return str((product.implementation_config or {}).get("status") or "active")


def _pricing_option_schema(option: PricingOption) -> WholesalePricingOptionResponse:
    pricing_model = option.pricing_model.lower()
    pricing_option_id = f"{pricing_model}_{option.currency.lower()}_{'fixed' if option.is_fixed else 'auction'}"
    return WholesalePricingOptionResponse(
        pricing_option_id=pricing_option_id,
        pricing_model=option.pricing_model,
        rate=option.rate,
        currency=option.currency,
        is_fixed=option.is_fixed,
        price_guidance=option.price_guidance,
        parameters=option.parameters,
        min_spend_per_package=option.min_spend_per_package,
    )


def _creative_format_schema(format_id: dict[str, Any], product: Product) -> WholesaleCreativeFormat:
    creative_formats = (product.implementation_config or {}).get("creative_formats") or []
    format_bindings = (product.implementation_config or {}).get("format_bindings") or []
    return _creative_format_schema_from_stored(format_id, creative_formats, format_bindings)


def _creative_format_schema_from_stored(
    format_id: dict[str, Any],
    creative_formats: list[dict[str, Any]],
    format_bindings: list[dict[str, Any]],
) -> WholesaleCreativeFormat:
    from src.core.canonical_formats import canonicalize_format_ref

    canonical_format_id = canonicalize_format_ref(format_id)
    for fmt in creative_formats:
        raw_format_id = canonicalize_format_ref(fmt.get("format_id") or {})
        if raw_format_id == canonical_format_id:
            return WholesaleCreativeFormat(**{**fmt, "format_id": raw_format_id})

    slot_requirements: list[dict[str, Any]] = []
    for binding in format_bindings:
        binding_format = canonicalize_format_ref(binding.get("format_id") or {})
        if binding_format == canonical_format_id:
            slot_requirements = list(binding.get("slot_requirements") or [])
            break
    return WholesaleCreativeFormat(
        format_id=FormatIdRef.model_validate(canonical_format_id),
        slot_requirements=slot_requirements,
    )


def _execution_from_product(product: Product, adapter_type: str) -> WholesaleInventoryExecution:
    config = dict(product.effective_implementation_config or {})
    return _execution_from_config(config, adapter_type)


def _execution_from_config(config: dict[str, Any], adapter_type: str) -> WholesaleInventoryExecution:
    raw_selectors = config.get("selectors")
    if raw_selectors is None:
        raw_selectors = []
        for ad_unit_id in config.get("targeted_ad_unit_ids") or []:
            raw_selectors.append(
                {
                    "selector_type": "ad_unit",
                    "external_id": str(ad_unit_id),
                    "options": {"include_descendants": bool(config.get("include_descendants", True))},
                }
            )
        for placement_id in config.get("targeted_placement_ids") or []:
            raw_selectors.append({"selector_type": "placement", "external_id": str(placement_id), "options": {}})

    selectors = [InventoryExecutionSelector(**selector) for selector in raw_selectors]
    bindings = [
        WholesaleFormatBinding(**{**binding, "format_id": _format_id_dict(binding.get("format_id") or {})})
        for binding in config.get("format_bindings") or []
    ]
    return WholesaleInventoryExecution(adapter=adapter_type, selectors=selectors, format_bindings=bindings)


def _wholesale_response_from_product(product: Product, adapter_type: str | None = None) -> WholesaleProductResponse:
    config = dict(product.implementation_config or {})
    resolved_adapter = adapter_type or config.get("adapter") or "mock"
    format_ids = product.effective_format_ids or []
    publisher_properties = product.effective_properties or []
    return WholesaleProductResponse(
        wholesale_product_id=product.product_id,
        product_id=product.product_id,
        inventory_profile_id=product.inventory_profile.profile_id if product.inventory_profile else None,
        name=product.name,
        description=product.description,
        status=_product_status(product),
        delivery_type=product.delivery_type,
        channels=product.channels,
        pricing_options=[_pricing_option_schema(option) for option in product.pricing_options or []],
        forecast=product.forecast,
        inventory=WholesaleInventory(
            publisher_properties=coerce_stored_publisher_property_selectors(publisher_properties),
            creative_formats=[_creative_format_schema(format_id, product) for format_id in format_ids],
            execution=_execution_from_product(product, resolved_adapter),
        ),
        targeting_capabilities=config.get("targeting_capabilities") or {},
        optimization_capabilities=config.get("optimization_capabilities") or {},
        allowed_actions=product.allowed_actions,
        format_options=product.format_options,
        video_placement_types=product.video_placement_types,
        vendor_metric_optimization=product.vendor_metric_optimization,
        allowed_principal_ids=product.allowed_principal_ids,
    )


def _profile_constraints(profile: InventoryProfile) -> dict[str, Any]:
    return profile.constraints if isinstance(profile.constraints, dict) else {}


def _wholesale_status_from_profile(profile: InventoryProfile) -> str:
    status = str(_profile_constraints(profile).get("status") or "active")
    return status if status in {"draft", "active", "archived"} else "active"


def _wholesale_response_from_profile(
    profile: InventoryProfile,
    adapter_type: str,
    default_currency: str,
) -> WholesaleProductResponse:
    constraints = _profile_constraints(profile)
    config = profile.inventory_config if isinstance(profile.inventory_config, dict) else {}
    product_projection = inventory_profile_to_product_model(profile, default_currency=default_currency)
    creative_formats = list(constraints.get("creative_formats") or [])
    format_bindings = list(config.get("format_bindings") or [])
    format_ids = profile.format_ids or []
    return WholesaleProductResponse(
        wholesale_product_id=profile.profile_id,
        product_id=profile.profile_id,
        inventory_profile_id=profile.profile_id,
        name=profile.name,
        description=profile.description,
        status=_wholesale_status_from_profile(profile),
        delivery_type="non_guaranteed",
        channels=constraints.get("channels") or None,
        pricing_options=[_pricing_option_schema(option) for option in product_projection.pricing_options or []],
        forecast=product_projection.forecast,
        inventory=WholesaleInventory(
            publisher_properties=coerce_stored_publisher_property_selectors(profile.publisher_properties or []),
            creative_formats=[
                _creative_format_schema_from_stored(format_id, creative_formats, format_bindings)
                for format_id in format_ids
            ],
            execution=_execution_from_config(config, adapter_type),
        ),
        targeting_capabilities=constraints.get("targeting_capabilities") or profile.targeting_template or {},
        optimization_capabilities=constraints.get("optimization_capabilities") or {},
        allowed_actions=constraints.get("allowed_actions") or None,
        format_options=constraints.get("format_options") or None,
        vendor_metric_optimization=constraints.get("vendor_metric_optimization") or None,
        allowed_principal_ids=constraints.get("allowed_principal_ids") or None,
    )


def _default_wholesale_currency_for_authoring(
    session,
    tenant_id: str,
    adapter: AdapterConfig | None,
) -> str:
    preferred_currency = (
        adapter.gam_network_currency
        if adapter is not None and adapter.adapter_type == "google_ad_manager" and adapter.gam_network_currency
        else None
    )
    return default_wholesale_currency(
        CurrencyLimitRepository(session, tenant_id).list_all(),
        preferred=preferred_currency,
    )


def _validation_issues_for_wholesale_product(
    req: WholesaleProductRequest,
    adapter_type: str,
) -> list[WholesaleValidationIssue]:
    issues: list[WholesaleValidationIssue] = []
    if not req.inventory.publisher_properties:
        issues.append(
            WholesaleValidationIssue(
                code="missing_publisher_properties",
                field="inventory.publisher_properties",
                message="At least one publisher property selector is required.",
            )
        )
    if not req.inventory.creative_formats:
        issues.append(
            WholesaleValidationIssue(
                code="missing_creative_formats",
                field="inventory.creative_formats",
                message="At least one creative format is required.",
            )
        )

    requested_adapter = (
        _canonical_catalog_adapter_type(req.inventory.execution.adapter) or req.inventory.execution.adapter
    )
    if requested_adapter != adapter_type:
        issues.append(
            WholesaleValidationIssue(
                code="adapter_mismatch",
                field="inventory.execution.adapter",
                message=f"Execution adapter {requested_adapter!r} does not match tenant adapter {adapter_type!r}.",
            )
        )

    supported = _supported_selector_types(adapter_type)
    for idx, selector in enumerate(req.inventory.execution.selectors):
        if selector.selector_type not in supported:
            issues.append(
                WholesaleValidationIssue(
                    code="unsupported_selector_type",
                    field=f"inventory.execution.selectors.{idx}.selector_type",
                    message=f"Selector type {selector.selector_type!r} is not supported for adapter {adapter_type!r}.",
                )
            )
    return issues


def _publisher_property_validation_issues(
    session,
    tenant_id: str,
    req: WholesaleProductRequest,
) -> list[WholesaleValidationIssue]:
    seed_local_example_publisher_authorization_for_selectors(
        session=session,
        tenant_id=tenant_id,
        selectors=req.inventory.publisher_properties,
    )
    return [
        WholesaleValidationIssue(**issue)
        for issue in validate_publisher_property_selectors(
            session=session,
            tenant_id=tenant_id,
            selectors=req.inventory.publisher_properties,
            field_prefix="inventory.publisher_properties",
        )
    ]


def _creative_format_identity(format_id: FormatIdRef | dict[str, Any]):
    from src.core.format_cache import canonical_format_identity

    return canonical_format_identity(_format_id_dict(format_id))


def _catalog_creative_format_refs(tenant_id: str) -> set[tuple[str, str, int | None, int | None, int | None]] | None:
    from src.admin.blueprints.products import get_creative_formats
    from src.core.format_cache import canonical_format_identity

    try:
        formats = get_creative_formats(tenant_id=tenant_id)
    except Exception:
        logger.warning("Unable to validate wholesale creative format IDs for tenant %s", tenant_id, exc_info=True)
        return None

    refs: set[tuple[str, str, int | None, int | None, int | None]] = set()
    for fmt in formats:
        raw_format_id = fmt.get("format_id") or {}
        if not raw_format_id and fmt.get("agent_url") and fmt.get("id"):
            raw_format_id = {"agent_url": fmt["agent_url"], "id": fmt["id"]}
        agent_url = raw_format_id.get("agent_url")
        format_id = raw_format_id.get("id")
        if agent_url and format_id:
            refs.add(canonical_format_identity(raw_format_id))
    return refs


def _creative_format_validation_issues(
    tenant_id: str,
    req: WholesaleProductRequest,
) -> list[WholesaleValidationIssue]:
    if not req.inventory.creative_formats:
        return []

    catalog_refs = _catalog_creative_format_refs(tenant_id)
    if not catalog_refs:
        return [
            WholesaleValidationIssue(
                code="creative_format_catalog_unavailable",
                field="inventory.creative_formats",
                message="Creative format catalog is unavailable, so format IDs could not be verified.",
                severity="warning",
            )
        ]

    issues: list[WholesaleValidationIssue] = []
    for idx, creative_format in enumerate(req.inventory.creative_formats):
        raw_format_id = _format_id_dict(creative_format.format_id)
        if _creative_format_identity(creative_format.format_id) not in catalog_refs:
            issues.append(
                WholesaleValidationIssue(
                    code="creative_format_not_found",
                    field=f"inventory.creative_formats.{idx}.format_id",
                    message=(
                        f"Creative format {raw_format_id['agent_url']}#{raw_format_id['id']} "
                        "was not found in the discovered creative format catalog."
                    ),
                )
            )
    return issues


def _selector_exists(session, tenant_id: str, adapter_type: str, selector: InventoryExecutionSelector) -> bool | None:
    """Return True/False when cache existence is checkable, or None when not cached."""
    if adapter_type == "google_ad_manager":
        if GAMSyncRepository(session, tenant_id).count_inventory(selector.selector_type) == 0:
            return None
        row = GAMSyncRepository(session, tenant_id).find_inventory_item(selector.selector_type, selector.external_id)
        return row is not None
    if adapter_type == "freewheel":
        freewheel_cache_rows = FreeWheelInventoryRepository(session, tenant_id).search(selector.selector_type, limit=1)
        if not freewheel_cache_rows:
            return None
        freewheel_rows = FreeWheelInventoryRepository(session, tenant_id).search(
            selector.selector_type,
            q=selector.external_id,
            limit=2,
        )
        return any(row.entity_id == selector.external_id for row in freewheel_rows)
    if adapter_type == "springserve":
        springserve_cache_rows = SpringServeInventoryRepository(session, tenant_id).search(
            selector.selector_type, limit=1
        )
        if not springserve_cache_rows:
            return None
        springserve_rows = SpringServeInventoryRepository(session, tenant_id).search(
            selector.selector_type,
            q=selector.external_id,
            limit=2,
        )
        return any(row.entity_id == selector.external_id for row in springserve_rows)
    return None


def _validate_wholesale_product(
    session,
    tenant_id: str,
    req: WholesaleProductRequest,
    adapter_type: str,
    *,
    check_selector_cache: bool,
) -> WholesaleProductValidationResponse:
    issues = _validation_issues_for_wholesale_product(req, adapter_type)
    issues.extend(_publisher_property_validation_issues(session, tenant_id, req))
    issues.extend(_creative_format_validation_issues(tenant_id, req))
    if check_selector_cache:
        for idx, selector in enumerate(req.inventory.execution.selectors):
            exists = _selector_exists(session, tenant_id, adapter_type, selector)
            if exists is False:
                issues.append(
                    WholesaleValidationIssue(
                        code="selector_not_found",
                        field=f"inventory.execution.selectors.{idx}.external_id",
                        message=(
                            f"Selector {selector.selector_type!r}/{selector.external_id!r} "
                            "was not found in the synced ad-server cache."
                        ),
                    )
                )
    return WholesaleProductValidationResponse(
        valid=not any(issue.severity == "error" for issue in issues), issues=issues
    )


def _signal_mapping_response(signal: TenantSignal) -> SignalMappingResponse:
    range_body = None
    if signal.range_min is not None or signal.range_max is not None:
        range_body = {"min": signal.range_min, "max": signal.range_max}
    return SignalMappingResponse(
        signal_id=signal.signal_id,
        name=signal.name,
        description=signal.description,
        value_type=cast(Any, signal.value_type),
        categories=list(signal.categories or []),
        tags=list(signal.tags or []),
        range=range_body,
        adapter_config=dict(signal.adapter_config or {}),
        data_provider=signal.data_provider,
        targeting_dimension=signal.targeting_dimension,
        etag=signal.etag,
        created_at=signal.created_at,
        updated_at=signal.updated_at,
    )


def _refresh_signal_etag(signal: TenantSignal) -> None:
    signal.etag = uuid.uuid4().hex


def _notify_signal_mapping_changed(tenant_id: str, action: str, signal_id: str, signal_name: str) -> None:
    publish_signal_catalog_change(
        tenant_id=tenant_id,
        action=action,
        signal_id=signal_id,
        data={"name": signal_name},
    )


def _set_signal_fields(signal: TenantSignal, req: SignalMappingRequest) -> None:
    signal.name = req.name
    signal.description = req.description
    signal.value_type = req.value_type
    signal.categories = list(req.categories or [])
    signal.tags = list(req.tags or [])
    signal.range_min = req.range.min if req.range else None
    signal.range_max = req.range.max if req.range else None
    signal.adapter_config = dict(req.adapter_config or {})
    signal.data_provider = req.data_provider
    signal.targeting_dimension = req.targeting_dimension
    _refresh_signal_etag(signal)


def _slug_fragment(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug[:80] or "signal"


def _candidate_default_signal(
    *,
    signal_id_prefix: str,
    name: str,
    external_id: str,
    adapter_config: dict[str, Any],
    targeting_dimension: str = "audience",
) -> dict[str, Any]:
    return {
        "signal_id": f"{signal_id_prefix}_{_slug_fragment(name or external_id)}",
        "name": name or external_id,
        "value_type": "binary",
        "adapter_config": adapter_config,
        "targeting_dimension": targeting_dimension,
    }


def _candidate_path(raw_path: Any) -> list[str] | None:
    """Return a candidate path safe for the public schema."""
    if raw_path is None:
        return None
    path = raw_path if isinstance(raw_path, list) else [raw_path]
    clean_path = [str(part) for part in path if part is not None]
    return clean_path or None


def _gam_signal_candidate(row: GAMInventory) -> SignalCandidateSummary:
    metadata = dict(row.inventory_metadata or {})
    parent_id = metadata.get("parent_id") or metadata.get("custom_targeting_key_id")
    adapter_config: dict[str, Any] | None = None
    mapping_kind: str | None = None
    default_signal: dict[str, Any] | None = None
    if row.inventory_type == "audience_segment":
        mapping_kind = "audience_segment"
        adapter_config = {"type": "passthrough", "kind": "audience_segment", "segment_id": row.inventory_id}
        default_signal = _candidate_default_signal(
            signal_id_prefix="audience",
            name=row.name,
            external_id=row.inventory_id,
            adapter_config=adapter_config,
        )
    elif row.inventory_type == "custom_targeting_value" and parent_id:
        mapping_kind = "custom_key_value"
        adapter_config = {
            "type": "passthrough",
            "kind": "custom_key_value",
            "key_id": str(parent_id),
            "value_id": row.inventory_id,
        }
        default_signal = _candidate_default_signal(
            signal_id_prefix="kv",
            name=row.name,
            external_id=row.inventory_id,
            adapter_config=adapter_config,
        )
    return SignalCandidateSummary(
        candidate_type=row.inventory_type,
        external_id=row.inventory_id,
        name=row.name,
        parent_id=str(parent_id) if parent_id is not None else None,
        path=_candidate_path(row.path),
        mapping_kind=mapping_kind,
        adapter_config_template=adapter_config,
        default_signal=default_signal,
        metadata=metadata,
    )


def _springserve_signal_candidate(row: SpringServeInventory) -> SignalCandidateSummary:
    metadata = dict(row.raw_json or {})
    adapter_config: dict[str, Any] | None = None
    mapping_kind: str | None = None
    default_signal: dict[str, Any] | None = None
    if row.entity_type == "value_list" and row.key_id:
        mapping_kind = "springserve_value_list"
        adapter_config = {
            "type": "passthrough",
            "kind": "springserve_value_list",
            "key_id": row.key_id,
            "key_name": metadata.get("key_name") or metadata.get("keyName"),
            "value_list_id": row.entity_id,
        }
        adapter_config = {key: value for key, value in adapter_config.items() if value is not None}
        default_signal = _candidate_default_signal(
            signal_id_prefix="ss",
            name=row.name or row.entity_id,
            external_id=row.entity_id,
            adapter_config=adapter_config,
        )
    return SignalCandidateSummary(
        candidate_type=row.entity_type,
        external_id=row.entity_id,
        name=row.name,
        parent_id=row.key_id or row.supply_router_id or row.supply_partner_id,
        mapping_kind=mapping_kind,
        adapter_config_template=adapter_config,
        default_signal=default_signal,
        metadata=metadata,
    )


def _freewheel_signal_candidate(row: FreeWheelInventory, candidate_type: str | None = None) -> SignalCandidateSummary:
    metadata = dict(row.raw_json or {})
    raw_id = str(metadata.get("id") or row.entity_id.split(":")[-1])
    adapter_config: dict[str, Any] | None = None
    mapping_kind: str | None = None
    default_signal: dict[str, Any] | None = None
    if row.parent_id in {"viewership_profiles", "viewership_profile", "viewershipProfileIds"}:
        mapping_kind = "freewheel_viewership_profile"
        adapter_config = {"type": "passthrough", "kind": "freewheel_viewership_profile", "profile_id": raw_id}
        default_signal = _candidate_default_signal(
            signal_id_prefix="fw_viewership",
            name=row.name or raw_id,
            external_id=raw_id,
            adapter_config=adapter_config,
        )
    return SignalCandidateSummary(
        candidate_type=candidate_type or row.entity_type,
        external_id=row.entity_id,
        name=row.name,
        parent_id=row.parent_id,
        mapping_kind=mapping_kind,
        adapter_config_template=adapter_config,
        default_signal=default_signal,
        metadata=metadata,
    )


def _filter_candidates_by_query(rows: list[Any], q: str | None) -> list[Any]:
    if not q:
        return rows
    needle = q.lower()
    return [
        row
        for row in rows
        if needle in str(getattr(row, "name", "") or "").lower()
        or needle in str(getattr(row, "inventory_id", "") or getattr(row, "entity_id", "")).lower()
    ]


def _signal_candidate_rows(
    session,
    tenant_id: str,
    adapter_type: str,
    candidate_type: str,
    *,
    q: str | None,
    parent_id: str | None,
    offset: int,
    limit: int,
) -> list[SignalCandidateSummary]:
    if adapter_type == "google_ad_manager":
        repo = GAMSyncRepository(session, tenant_id)
        if candidate_type == "custom_targeting_value" and parent_id:
            cached_rows = repo.list_values_for_key(parent_id)
            if not cached_rows:
                _refresh_candidate_targeting_values_if_available(session, tenant_id, repo, parent_id)
                cached_rows = repo.list_values_for_key(parent_id)
            rows = _filter_candidates_by_query(cached_rows, q)
            page = rows[offset : offset + limit]
        else:
            page = repo.search_inventory(candidate_type, q=q, parent_id=parent_id, offset=offset, limit=limit)
        return [_gam_signal_candidate(row) for row in page]
    if adapter_type == "springserve":
        rows = SpringServeInventoryRepository(session, tenant_id).search(
            candidate_type,
            q=q,
            parent_id=parent_id,
            offset=offset,
            limit=limit,
        )
        return [_springserve_signal_candidate(row) for row in rows]
    if adapter_type == "freewheel":
        if candidate_type == "viewership_profile":
            rows = FreeWheelInventoryRepository(session, tenant_id).search(
                "standard_attribute",
                q=q,
                parent_id="viewership_profiles",
                offset=offset,
                limit=limit,
            )
            return [_freewheel_signal_candidate(row, candidate_type="viewership_profile") for row in rows]
        rows = FreeWheelInventoryRepository(session, tenant_id).search(
            candidate_type,
            q=q,
            parent_id=parent_id,
            offset=offset,
            limit=limit,
        )
        return [_freewheel_signal_candidate(row) for row in rows]
    return []


def _refresh_candidate_targeting_values_if_available(
    session,
    tenant_id: str,
    repo: GAMSyncRepository,
    key_id: str,
) -> None:
    """Best-effort lazy cache fill for GAM custom targeting value candidates."""
    key_row = repo.find_inventory_item("custom_targeting_key", key_id)
    if key_row is None or targeting_values_synced_empty(key_row):
        return

    adapter_config = TenantConfigRepository(session, tenant_id).get_adapter_config()
    if (
        adapter_config is None
        or adapter_config.adapter_type != "google_ad_manager"
        or not adapter_config.gam_network_code
        or not AdapterConfigRepository.has_gam_credentials(adapter_config)
    ):
        return

    try:
        discovery = build_gam_inventory_discovery(adapter_config, tenant_id)
        sync_targeting_values_for_key(
            repo,
            key_id=key_id,
            key_row=key_row,
            discovery=discovery,
            max_values=1000,
        )
        session.flush()
    except Exception:
        session.rollback()
        logger.exception(
            "Lazy targeting value candidate refresh failed for tenant_id=%s key_id=%s",
            tenant_id,
            key_id,
        )


def _required_signal_config_fields(kind: str) -> tuple[str, ...]:
    return {
        "audience_segment": ("segment_id",),
        "custom_key_value": ("key_id", "value_id"),
        "gam_targeting_groups": ("groups",),
        "springserve_value_list": ("key_id", "value_list_id"),
        "freewheel_viewership_profile": ("profile_id",),
        "freewheel_audience_item": ("item_id",),
        "freewheel_custom_kv": ("key", "value_id"),
    }.get(kind, ())


def _signal_config_atoms(adapter_config: dict[str, Any]) -> list[dict[str, Any]]:
    if adapter_config.get("type") == "composed":
        criteria = adapter_config.get("criteria")
        if isinstance(criteria, list):
            return [criterion for criterion in criteria if isinstance(criterion, dict)]
        return []
    return [adapter_config]


def _signal_mapping_semantics_for_kind(
    adapter_type: str,
    mapping_kind: str,
) -> SignalMappingKindTargetingSemantics | None:
    semantics = _SIGNAL_TARGETING_SEMANTICS.get(adapter_type)
    if semantics is None:
        return None
    for mapping_semantics in semantics.mapping_kinds:
        if mapping_semantics.mapping_kind == mapping_kind:
            return mapping_semantics
    return None


def _validate_signal_config_shape(
    req: SignalMappingRequest,
    adapter_type: str,
) -> list[SignalMappingValidationIssue]:
    issues: list[SignalMappingValidationIssue] = []
    if req.value_type == "categorical" and not req.categories:
        issues.append(
            SignalMappingValidationIssue(
                code="missing_categories",
                field="categories",
                message="categories is required when value_type='categorical'.",
            )
        )
    if req.value_type == "numeric":
        if req.range is None:
            issues.append(
                SignalMappingValidationIssue(
                    code="missing_range",
                    field="range",
                    message="range is required when value_type='numeric'.",
                )
            )
        elif req.range.min is not None and req.range.max is not None and req.range.min > req.range.max:
            issues.append(
                SignalMappingValidationIssue(
                    code="invalid_range",
                    field="range",
                    message="range.min must be less than or equal to range.max.",
                )
            )

    atoms = _signal_config_atoms(req.adapter_config)
    if req.adapter_config.get("type") == "composed" and not atoms:
        issues.append(
            SignalMappingValidationIssue(
                code="invalid_composed_config",
                field="adapter_config.criteria",
                message="adapter_config.type='composed' requires a non-empty criteria list.",
            )
        )
    composed_config = req.adapter_config.get("type") == "composed"
    adapter_supports_composed = True
    if composed_config:
        adapter_semantics = _SIGNAL_TARGETING_SEMANTICS.get(adapter_type, SignalTargetingSemantics())
        adapter_supports_composed = adapter_semantics.supports_composed
        if not adapter_supports_composed:
            issues.append(
                SignalMappingValidationIssue(
                    code="unsupported_composed_config",
                    field="adapter_config.type",
                    message=f"Adapter {adapter_type!r} does not support composed signal mappings.",
                )
            )
    if not atoms:
        issues.append(
            SignalMappingValidationIssue(
                code="missing_adapter_config",
                field="adapter_config",
                message="adapter_config must declare an adapter mapping kind.",
            )
        )
        return issues

    supported_kinds = _supported_signal_mapping_kinds(adapter_type)
    for atom_idx, atom in enumerate(atoms):
        field_prefix = "adapter_config" if len(atoms) == 1 else f"adapter_config.criteria.{atom_idx}"
        kind = atom.get("kind")
        if kind not in supported_kinds:
            issues.append(
                SignalMappingValidationIssue(
                    code="unsupported_signal_mapping_kind",
                    field=f"{field_prefix}.kind",
                    message=f"Signal mapping kind {kind!r} is not supported for adapter {adapter_type!r}.",
                )
            )
            continue
        mapping_semantics = _signal_mapping_semantics_for_kind(adapter_type, str(kind))
        if (
            composed_config
            and adapter_supports_composed
            and mapping_semantics
            and "flat_criteria" not in mapping_semantics.composition_models
        ):
            issues.append(
                SignalMappingValidationIssue(
                    code="unsupported_signal_composed_mapping_kind",
                    field=f"{field_prefix}.kind",
                    message=(f"Signal mapping kind {kind!r} cannot be used inside adapter_config.type='composed'."),
                )
            )
        if atom.get("mode", "include") not in {"include", "exclude"}:
            issues.append(
                SignalMappingValidationIssue(
                    code="invalid_signal_mapping_mode",
                    field=f"{field_prefix}.mode",
                    message="mode must be either 'include' or 'exclude'.",
                )
            )
        else:
            supported_modes = set(mapping_semantics.supported_modes if mapping_semantics else [])
            mode = atom.get("mode", "include")
            if supported_modes and mode not in supported_modes:
                issues.append(
                    SignalMappingValidationIssue(
                        code="unsupported_signal_mapping_mode",
                        field=f"{field_prefix}.mode",
                        message=f"Signal mapping kind {kind!r} supports mode(s): {', '.join(sorted(supported_modes))}.",
                    )
                )
        for required_field in _required_signal_config_fields(str(kind)):
            if not atom.get(required_field):
                issues.append(
                    SignalMappingValidationIssue(
                        code="missing_signal_mapping_field",
                        field=f"{field_prefix}.{required_field}",
                        message=f"Signal mapping kind {kind!r} requires {required_field}.",
                    )
                )
    return issues


def _gam_signal_config_exists(session, tenant_id: str, atom: dict[str, Any]) -> tuple[bool | None, str | None]:
    repo = GAMSyncRepository(session, tenant_id)
    kind = atom.get("kind")
    if kind == "audience_segment":
        if repo.count_inventory("audience_segment") == 0:
            return None, None
        return repo.find_inventory_item("audience_segment", str(atom.get("segment_id"))) is not None, "segment_id"
    if kind == "custom_key_value":
        key_id = str(atom.get("key_id") or "")
        value_id = str(atom.get("value_id") or "")
        if repo.count_inventory("custom_targeting_key") == 0:
            return None, None
        if repo.find_inventory_item("custom_targeting_key", key_id) is None:
            return False, "key_id"
        values = repo.list_values_for_key(key_id)
        if not values:
            return None, None
        return any(row.inventory_id == value_id for row in values), "value_id"
    return None, None


def _springserve_signal_config_exists(session, tenant_id: str, atom: dict[str, Any]) -> tuple[bool | None, str | None]:
    if atom.get("kind") != "springserve_value_list":
        return None, None
    repo = SpringServeInventoryRepository(session, tenant_id)
    if not repo.search("value_list", limit=1):
        return None, None
    value_list_id = str(atom.get("value_list_id") or "")
    rows = repo.search("value_list", q=value_list_id, limit=2)
    return any(row.entity_id == value_list_id for row in rows), "value_list_id"


def _freewheel_signal_config_exists(session, tenant_id: str, atom: dict[str, Any]) -> tuple[bool | None, str | None]:
    repo = FreeWheelInventoryRepository(session, tenant_id)
    if atom.get("kind") != "freewheel_viewership_profile":
        return None, None
    if not repo.search("standard_attribute", parent_id="viewership_profiles", limit=1):
        return None, None
    profile_id = str(atom.get("profile_id") or "")
    rows = repo.search("standard_attribute", q=profile_id, parent_id="viewership_profiles", limit=2)
    return (
        any(str((row.raw_json or {}).get("id") or row.entity_id.split(":")[-1]) == profile_id for row in rows),
        "profile_id",
    )


def _signal_config_cache_issues(
    session,
    tenant_id: str,
    req: SignalMappingRequest,
    adapter_type: str,
) -> list[SignalMappingValidationIssue]:
    issues: list[SignalMappingValidationIssue] = []
    cache_checkers = {
        "google_ad_manager": _gam_signal_config_exists,
        "springserve": _springserve_signal_config_exists,
        "freewheel": _freewheel_signal_config_exists,
    }
    checker = cache_checkers.get(adapter_type)
    if checker is None:
        return issues
    for atom_idx, atom in enumerate(_signal_config_atoms(req.adapter_config)):
        exists, field = checker(session, tenant_id, atom)
        if exists is False:
            prefix = (
                "adapter_config"
                if req.adapter_config.get("type") != "composed"
                else f"adapter_config.criteria.{atom_idx}"
            )
            issues.append(
                SignalMappingValidationIssue(
                    code="signal_mapping_candidate_not_found",
                    field=f"{prefix}.{field}" if field else prefix,
                    message="Signal mapping target was not found in the synced ad-server cache.",
                )
            )
    return issues


def _validate_signal_mapping(
    session,
    tenant_id: str,
    req: SignalMappingRequest,
    adapter_type: str,
    *,
    check_candidate_cache: bool,
) -> SignalMappingValidationResponse:
    issues = _validate_signal_config_shape(req, adapter_type)
    if check_candidate_cache and not any(issue.severity == "error" for issue in issues):
        issues.extend(_signal_config_cache_issues(session, tenant_id, req, adapter_type))
    return SignalMappingValidationResponse(
        valid=not any(issue.severity == "error" for issue in issues),
        issues=issues,
    )


def _wholesale_profile_constraints(
    req: WholesaleProductRequest,
    product_id: str,
    format_ids: list[dict[str, str]],
    adapter_type: str,
) -> dict[str, Any]:
    return {
        "formats": [fmt["id"] for fmt in format_ids],
        "channels": req.channels or [],
        "targeting_dimensions": list((req.targeting_capabilities or {}).get("allowed_dimensions") or []),
        "managed_by": _WHOLESALE_PROFILE_MANAGED_BY,
        "owner_product_id": product_id,
        "status": req.status,
        "delivery_type": "non_guaranteed",
        "adapter": adapter_type,
        "creative_formats": [_wholesale_creative_format_dict(fmt) for fmt in req.inventory.creative_formats],
        "targeting_capabilities": req.targeting_capabilities,
        "optimization_capabilities": req.optimization_capabilities,
        "allowed_actions": req.allowed_actions,
        "format_options": req.format_options,
        "vendor_metric_optimization": req.vendor_metric_optimization,
        "allowed_principal_ids": req.allowed_principal_ids,
    }


def _inventory_profile_conflict(product_id: str):
    return _api_error(
        "inventory_profile_conflict",
        f"Inventory profile {product_id!r} already exists and is not managed by the wholesale products API.",
        409,
        details={"inventory_profile_id": product_id},
    )


def _build_wholesale_product_models(
    tenant_id: str,
    product_id: str,
    req: WholesaleProductRequest,
    adapter_type: str,
    existing_profile: InventoryProfile | None = None,
) -> tuple[Product, InventoryProfile]:
    format_ids = _creative_format_id_dicts(req.inventory.creative_formats)
    publisher_properties = _publisher_property_dicts(req.inventory.publisher_properties)
    inventory_config = _execution_inventory_config(req.inventory.execution)
    implementation_config = _wholesale_implementation_config(req, adapter_type)
    profile_constraints = _wholesale_profile_constraints(req, product_id, format_ids, adapter_type)

    profile = existing_profile or InventoryProfile(
        tenant_id=tenant_id,
        profile_id=product_id,
        name=req.name,
        description=req.description,
        inventory_config=inventory_config,
        format_ids=format_ids,
        publisher_properties=publisher_properties,
        targeting_template=req.targeting_capabilities or {},
        constraints=profile_constraints,
    )
    profile.name = req.name
    profile.description = req.description
    profile.inventory_config = inventory_config
    profile.format_ids = format_ids
    profile.publisher_properties = publisher_properties
    profile.targeting_template = req.targeting_capabilities or {}
    profile.constraints = profile_constraints

    product = Product(
        tenant_id=tenant_id,
        product_id=product_id,
        name=req.name,
        description=req.description,
        format_ids=format_ids,
        targeting_template=req.targeting_capabilities or {},
        delivery_type=req.delivery_type,
        channels=req.channels,
        implementation_config=implementation_config,
        properties=publisher_properties,
        property_tags=None,
        inventory_profile=profile,
        delivery_measurement={"provider": "publisher"},
        reporting_capabilities=dict(PRODUCT_REPORTING_CAPABILITIES_DEFAULT),
        property_targeting_allowed=bool((req.targeting_capabilities or {}).get("allowed_dimensions")),
        signal_targeting_allowed=bool((req.targeting_capabilities or {}).get("allowed_signals")),
        forecast=None,
        allowed_actions=req.allowed_actions,
        format_options=req.format_options,
        video_placement_types=req.video_placement_types,
        vendor_metric_optimization=req.vendor_metric_optimization,
        archived_at=datetime.now(UTC) if req.status == "archived" else None,
        allowed_principal_ids=req.allowed_principal_ids,
    )
    return product, profile


def _update_product_from_wholesale_request(
    product: Product,
    req: WholesaleProductRequest,
    adapter_type: str,
) -> None:
    format_ids = _creative_format_id_dicts(req.inventory.creative_formats)
    publisher_properties = _publisher_property_dicts(req.inventory.publisher_properties)
    implementation_config = _wholesale_implementation_config(req, adapter_type)
    profile_constraints = _wholesale_profile_constraints(req, product.product_id, format_ids, adapter_type)

    product.name = req.name
    product.description = req.description
    product.format_ids = format_ids
    product.targeting_template = req.targeting_capabilities or {}
    product.delivery_type = req.delivery_type
    product.channels = req.channels
    product.implementation_config = implementation_config
    product.properties = publisher_properties
    product.property_tags = None
    product.delivery_measurement = product.delivery_measurement or {"provider": "publisher"}
    product.reporting_capabilities = product.reporting_capabilities or dict(PRODUCT_REPORTING_CAPABILITIES_DEFAULT)
    product.property_targeting_allowed = bool((req.targeting_capabilities or {}).get("allowed_dimensions"))
    product.signal_targeting_allowed = bool((req.targeting_capabilities or {}).get("allowed_signals"))
    product.allowed_actions = req.allowed_actions
    product.format_options = req.format_options
    product.video_placement_types = req.video_placement_types
    product.vendor_metric_optimization = req.vendor_metric_optimization
    product.archived_at = datetime.now(UTC) if req.status == "archived" else None
    product.allowed_principal_ids = req.allowed_principal_ids

    profile = product.inventory_profile
    if profile is not None:
        profile.name = req.name
        profile.description = req.description
        profile.inventory_config = _execution_inventory_config(req.inventory.execution)
        profile.format_ids = format_ids
        profile.publisher_properties = publisher_properties
        profile.targeting_template = req.targeting_capabilities or {}
        profile.constraints = profile_constraints


def _build_wholesale_inventory_profile(
    tenant_id: str,
    product_id: str,
    req: WholesaleProductRequest,
    adapter_type: str,
    existing_profile: InventoryProfile | None = None,
) -> InventoryProfile:
    """Build or update the durable wholesale-product primitive.

    Storefront-facing wholesale products are inventory bundles. Buyer-facing
    Product rows are projected at protocol time and are not persisted here.
    """
    format_ids = _creative_format_id_dicts(req.inventory.creative_formats)
    publisher_properties = _publisher_property_dicts(req.inventory.publisher_properties)
    inventory_config = _execution_inventory_config(req.inventory.execution)
    profile_constraints = _wholesale_profile_constraints(req, product_id, format_ids, adapter_type)

    profile = existing_profile or InventoryProfile(
        tenant_id=tenant_id,
        profile_id=product_id,
    )
    profile.name = req.name
    profile.description = req.description
    profile.inventory_config = inventory_config
    profile.format_ids = format_ids
    profile.publisher_properties = publisher_properties
    profile.targeting_template = req.targeting_capabilities or {}
    profile.constraints = profile_constraints
    return profile


def _default_wholesale_pricing_response(default_currency: str) -> WholesalePricingOptionResponse:
    currency = default_currency.upper()
    return WholesalePricingOptionResponse(
        pricing_option_id=f"cpm_{currency.lower()}_auction",
        pricing_model="cpm",
        rate=None,
        currency=currency,
        is_fixed=False,
        price_guidance={"floor": 0.0},
        parameters=None,
        min_spend_per_package=None,
    )


def _buyer_projection(req: WholesaleProductRequest, product_id: str, default_currency: str = "USD") -> dict[str, Any]:
    return {
        "product_id": product_id,
        "name": req.name,
        "description": req.description,
        "delivery_type": "non_guaranteed",
        "format_ids": _creative_format_id_dicts(req.inventory.creative_formats),
        "publisher_properties": _publisher_property_dicts(req.inventory.publisher_properties),
        "pricing_options": [_default_wholesale_pricing_response(default_currency).model_dump(mode="json")],
        "forecast": None,
    }


def _adapter_projection(req: WholesaleProductRequest, adapter_type: str) -> dict[str, Any]:
    config = _execution_inventory_config(req.inventory.execution)
    return {
        "adapter": adapter_type,
        "inventory_config": config,
        "implementation_config": _wholesale_implementation_config(req, adapter_type),
    }


def _serialize_selector(row: GAMInventory | FreeWheelInventory | SpringServeInventory) -> InventorySelectorSummary:
    if isinstance(row, GAMInventory):
        metadata = row.inventory_metadata or {}
        return InventorySelectorSummary(
            selector_type=row.inventory_type,
            external_id=row.inventory_id,
            name=row.name,
            path=row.path,
            parent_id=metadata.get("parent_id"),
            status=row.status,
            metadata=metadata,
        )
    if isinstance(row, FreeWheelInventory):
        return InventorySelectorSummary(
            selector_type=row.entity_type,
            external_id=row.entity_id,
            name=row.name,
            parent_id=row.parent_id,
            metadata=row.raw_json or {},
        )
    return InventorySelectorSummary(
        selector_type=row.entity_type,
        external_id=row.entity_id,
        name=row.name,
        parent_id=row.supply_router_id or row.supply_partner_id or row.key_id,
        metadata=row.raw_json or {},
    )


def _adapter_catalog_entry(registry_key: str, adapter_class: Any) -> AdapterCatalogEntry:
    metadata = _ADAPTER_CATALOG_METADATA[registry_key]
    typed_config = _ADAPTER_CONFIG_TYPED.get(registry_key)
    return AdapterCatalogEntry(
        type=registry_key,
        name=metadata["name"],
        description=metadata["description"],
        tier=metadata.get("tier", "live"),
        default_channels=list(getattr(adapter_class, "default_channels", []) or []),
        contract_version=_ADAPTER_CONTRACT_VERSION,
        capabilities_url=_tenant_management_url(f"/adapters/{registry_key}/capabilities"),
        capabilities=_adapter_capabilities_summary(getattr(adapter_class, "capabilities", None)),
        connection_schema=_adapter_connection_schema(typed_config) if typed_config else {},
    )


def _adapter_connection_schema(model_class: Any, **kwargs: Any) -> dict[str, Any]:
    """Return JSON Schema for a dynamically selected Pydantic model class."""
    return cast(dict[str, Any], model_class.model_json_schema(**kwargs))


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

        tier = _ADAPTER_CATALOG_METADATA[registry_key].get("tier", "live")
        if tier_filter is not None and tier != tier_filter:
            continue

        entries.append(_adapter_catalog_entry(registry_key, adapter_class))

    entries.sort(key=lambda e: e.type)
    return jsonify(ListAdaptersResponse(adapters=entries, count=len(entries)).model_dump())


@tenant_management_api.route("/adapters/<adapter_type>/capabilities", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=AdapterCapabilitiesResponse, HTTP_404=ApiError))
def get_adapter_contract_capabilities(adapter_type: str):
    """Return the detailed tenant-management contract capabilities for one adapter."""
    from src.adapters import ADAPTER_REGISTRY

    canonical_type = _canonical_catalog_adapter_type(adapter_type)
    if canonical_type is None:
        return _api_error("adapter_not_found", f"Unknown adapter type: {adapter_type!r}", 404)

    adapter_class = ADAPTER_REGISTRY.get(canonical_type)
    if adapter_class is None:
        return _api_error("adapter_not_found", f"Unknown adapter type: {adapter_type!r}", 404)

    return jsonify(_build_adapter_capabilities(canonical_type, adapter_class).model_dump())


@tenant_management_api.route("/tenants/<tenant_id>/inventory/adapter-capabilities", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=InventoryAdapterCapabilitiesResponse, HTTP_404=ApiError))
def get_inventory_adapter_capabilities(tenant_id: str):
    """Return the tenant adapter's wholesale-product authoring capabilities."""
    with get_db_session() as session:
        tenant, adapter, error = _require_tenant_for_authoring(session, tenant_id)
        if error is not None:
            return error
        assert tenant is not None
        adapter_type = _tenant_adapter_type(tenant, adapter)
        response = InventoryAdapterCapabilitiesResponse(
            adapter=adapter_type,
            selector_types=_WHOLESALE_SELECTOR_CAPABILITIES.get(adapter_type, []),
            creative_binding_schemas=[
                CreativeBindingSchema(
                    selector_type=None,
                    schema={
                        "type": "object",
                        "description": "Adapter-specific creative binding payload persisted with the product.",
                    },
                )
            ],
            targeting_capabilities={
                "supports_property_targeting": True,
                "supports_signal_targeting": adapter_type in {"google_ad_manager", "freewheel", "springserve"},
            },
            pricing_capabilities={
                "supported_pricing_models": list(
                    _ADAPTER_CONTRACT_PROFILES.get(adapter_type, {}).get("supported_pricing_models", [])
                )
            },
            optimization_capabilities={
                "supports_forecasting": bool(
                    _ADAPTER_CONTRACT_PROFILES.get(adapter_type, {}).get("supports_forecasting")
                ),
                "supports_pricing_recommendations": bool(
                    _ADAPTER_CONTRACT_PROFILES.get(adapter_type, {}).get("supports_pricing_recommendations")
                ),
            },
        )
        return jsonify(response.model_dump(by_alias=True))


@tenant_management_api.route("/tenants/<tenant_id>/inventory/selectors", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=ListInventorySelectorsResponse, HTTP_400=ApiError, HTTP_404=ApiError))
def list_inventory_selectors(tenant_id: str):
    """Search cached ad-server inventory selectors for wholesale-product setup."""
    selector_type = request.args.get("selector_type") or request.args.get("selectorType")
    q = request.args.get("q")
    parent_id = request.args.get("parent_id") or request.args.get("parentId")
    try:
        limit = min(max(int(request.args.get("limit", "50")), 1), 100)
        offset = max(int(request.args.get("cursor", "0")), 0)
    except ValueError:
        return _api_error("invalid_pagination", "limit and cursor must be integers", 400)

    with get_db_session() as session:
        tenant, adapter, error = _require_tenant_for_authoring(session, tenant_id)
        if error is not None:
            return error
        assert tenant is not None
        adapter_type = _tenant_adapter_type(tenant, adapter)
        supported = _supported_selector_types(adapter_type)
        resolved_selector_type = selector_type or (sorted(supported)[0] if supported else None)
        if resolved_selector_type is None:
            response = ListInventorySelectorsResponse(selectors=[], count=0)
            return jsonify(response.model_dump())
        if resolved_selector_type not in supported:
            return _api_error(
                "unsupported_selector_type",
                f"Selector type {resolved_selector_type!r} is not supported for adapter {adapter_type!r}",
                400,
                details={"supported_selector_types": sorted(supported)},
            )

        rows: list[GAMInventory | FreeWheelInventory | SpringServeInventory]
        if adapter_type == "google_ad_manager":
            rows = cast(
                list[GAMInventory | FreeWheelInventory | SpringServeInventory],
                GAMSyncRepository(session, tenant_id).search_inventory(
                    resolved_selector_type,
                    q=q,
                    parent_id=parent_id,
                    offset=offset,
                    limit=limit + 1,
                ),
            )
        elif adapter_type == "freewheel":
            rows = cast(
                list[GAMInventory | FreeWheelInventory | SpringServeInventory],
                FreeWheelInventoryRepository(session, tenant_id).search(
                    resolved_selector_type,
                    q=q,
                    parent_id=parent_id,
                    offset=offset,
                    limit=limit + 1,
                ),
            )
        elif adapter_type == "springserve":
            rows = cast(
                list[GAMInventory | FreeWheelInventory | SpringServeInventory],
                SpringServeInventoryRepository(session, tenant_id).search(
                    resolved_selector_type,
                    q=q,
                    parent_id=parent_id,
                    offset=offset,
                    limit=limit + 1,
                ),
            )
        else:
            rows = []

        page_rows = rows[:limit]
        next_cursor = str(offset + limit) if len(rows) > limit else None
        response = ListInventorySelectorsResponse(
            selectors=[_serialize_selector(row) for row in page_rows],
            count=len(page_rows),
            next_cursor=next_cursor,
        )
        return jsonify(response.model_dump())


def _publisher_properties_response(
    repo: TenantConfigRepository,
    *,
    publisher_domain: str | None = None,
) -> PublisherPropertiesResponse:
    """Build the publisher-property authoring shape for one domain or all domains."""
    partners = [
        partner
        for partner in repo.list_publisher_partners()
        if publisher_domain is None or partner.publisher_domain == publisher_domain
    ]
    properties = [
        prop
        for prop in repo.list_authorized_properties()
        if publisher_domain is None or prop.publisher_domain == publisher_domain
    ]
    tags = repo.list_property_tags()

    domains_by_name: dict[str, PublisherDomainSummary] = {}
    for partner in partners:
        domains_by_name[partner.publisher_domain] = PublisherDomainSummary(
            publisher_domain=partner.publisher_domain,
            display_name=partner.display_name,
            is_verified=partner.is_verified,
            sync_status=partner.sync_status,
            total_properties=partner.total_properties,
            authorized_properties=partner.authorized_properties,
        )
    for prop in properties:
        domains_by_name.setdefault(
            prop.publisher_domain,
            PublisherDomainSummary(
                publisher_domain=prop.publisher_domain,
                display_name=prop.publisher_domain,
                is_verified=prop.verification_status == "verified",
                sync_status=prop.verification_status,
            ),
        )

    property_summaries = [
        PublisherPropertySummary(
            property_id=prop.property_id,
            publisher_domain=prop.publisher_domain,
            property_type=prop.property_type,
            name=prop.name,
            identifiers=prop.identifiers or [],
            tags=prop.tags or [],
            verification_status=prop.verification_status,
        )
        for prop in properties
    ]

    allowed_selectors: list[AllowedPublisherSelector] = []
    for domain in sorted(domains_by_name):
        domain_properties = [prop for prop in properties if prop.publisher_domain == domain]
        allowed_selectors.append(
            AllowedPublisherSelector(
                publisher_domain=domain,
                selection_type="all",
                label=f"All properties on {domain}",
            )
        )
        if domain_properties:
            allowed_selectors.append(
                AllowedPublisherSelector(
                    publisher_domain=domain,
                    selection_type="by_id",
                    property_ids=[prop.property_id for prop in domain_properties],
                    label=f"Selected properties on {domain}",
                )
            )
        domain_tags = sorted({tag for prop in domain_properties for tag in (prop.tags or [])})
        if not domain_tags:
            domain_tags = sorted(tag.tag_id for tag in tags)
        if domain_tags:
            allowed_selectors.append(
                AllowedPublisherSelector(
                    publisher_domain=domain,
                    selection_type="by_tag",
                    property_tags=domain_tags,
                    label=f"Tagged properties on {domain}",
                )
            )

    return PublisherPropertiesResponse(
        domains=sorted(domains_by_name.values(), key=lambda domain: domain.publisher_domain),
        properties=property_summaries,
        allowed_selectors=allowed_selectors,
    )


@tenant_management_api.route("/tenants/<tenant_id>/inventory/publisher-properties", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=PublisherPropertiesResponse, HTTP_404=ApiError))
def list_publisher_properties_for_authoring(tenant_id: str):
    """Return publisher domains, properties, and ready-to-use property selectors."""
    with get_db_session() as session:
        tenant, _adapter, error = _require_tenant_for_authoring(session, tenant_id)
        if error is not None:
            return error
        assert tenant is not None
        response = _publisher_properties_response(TenantConfigRepository(session, tenant_id))
        return jsonify(response.model_dump())


@tenant_management_api.route("/tenants/<tenant_id>/inventory/publisher-properties:lookup", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(
    json=LookupPublisherPropertiesRequest,
    resp=Response(HTTP_200=PublisherPropertiesLookupResponse, HTTP_400=ApiError, HTTP_404=ApiError),
)
def lookup_publisher_properties_for_authoring(tenant_id: str):
    """Resolve one publisher domain through AAO and cache its property IDs/tags."""
    req: LookupPublisherPropertiesRequest = _validated_json_payload()

    from src.admin.blueprints.publisher_partners import (
        _normalize_publisher_domain_input,
        _persist_status,
        _validate_publisher_domain,
    )

    publisher_domain = _normalize_publisher_domain_input(req.publisher_domain)
    if not publisher_domain:
        return _api_error("publisher_domain_required", "publisher_domain is required", 400)
    is_valid_domain, domain_error = _validate_publisher_domain(publisher_domain)
    if not is_valid_domain:
        return _api_error("invalid_publisher_domain", domain_error, 400)
    ssrf_ok, ssrf_error = check_url_ssrf(f"https://{publisher_domain}")
    if not ssrf_ok:
        return _api_error("invalid_publisher_domain", f"Refused: {ssrf_error}", 400)

    with get_db_session() as session:
        session.info["management_api_caller"] = True
        tenant, _adapter, error = _require_tenant_for_authoring(session, tenant_id)
        if error is not None:
            return error
        assert tenant is not None
        agent_url = resolve_agent_url(tenant)
        if not agent_url:
            return _api_error(
                "agent_url_not_configured",
                "Agent URL not configured (set public_agent_url, virtual_host, or SALES_AGENT_DOMAIN)",
                400,
            )

        repo = TenantConfigRepository(session, tenant_id)
        partner = repo.get_publisher_partner_by_domain(publisher_domain)
        if partner is None:
            partner = repo.create_publisher_partner(publisher_domain)

        import asyncio

        status = asyncio.run(get_publisher_partner_status(publisher_domain, agent_url, force_refresh=req.force_refresh))
        _persist_status(partner, status)
        session.commit()

    sync_stats: dict[str, Any] | None = None
    if status.status in {"authorized", "unbound"}:
        sync_stats = get_property_discovery_service().sync_properties_from_adagents_sync(
            tenant_id,
            publisher_domains=[publisher_domain],
            dry_run=False,
            agent_url=agent_url,
        )

    with get_db_session() as session:
        response = _publisher_properties_response(
            TenantConfigRepository(session, tenant_id),
            publisher_domain=publisher_domain,
        )

    property_ids = [prop.property_id for prop in response.properties]
    property_tags = sorted({tag for prop in response.properties for tag in prop.tags})
    lookup_response = PublisherPropertiesLookupResponse(
        **response.model_dump(),
        publisher_domain=publisher_domain,
        agent_url=agent_url,
        is_authorized=status.status in {"authorized", "unbound"},
        aao_status=status.status,
        error=status.error,
        total_properties=status.total_properties,
        authorized_properties=status.authorized_properties,
        property_ids=property_ids,
        property_tags=property_tags,
        sync=sync_stats,
    )
    return jsonify(lookup_response.model_dump())


@tenant_management_api.route("/tenants/<tenant_id>/creative-formats", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(
    query=ListCreativeFormatsForAuthoringQuery,
    resp=Response(HTTP_200=ListCreativeFormatsForAuthoringResponse, HTTP_404=ApiError),
)
def list_creative_formats_for_authoring(tenant_id: str, query: ListCreativeFormatsForAuthoringQuery):
    """Return creative formats usable in wholesale-product authoring."""
    with get_db_session() as session:
        tenant, _adapter, error = _require_tenant_for_authoring(session, tenant_id)
        if error is not None:
            return error
        assert tenant is not None

    from src.admin.blueprints.products import get_creative_format_catalog

    catalog = get_creative_format_catalog(
        tenant_id=tenant_id,
        name_search=query.q,
        asset_types=query.asset_type or None,
    )
    response_formats: list[CreativeFormatSummary] = []
    for fmt in catalog.formats:
        raw_format_id = fmt.get("format_id") or {}
        if not raw_format_id and fmt.get("agent_url") and fmt.get("id"):
            raw_format_id = {"agent_url": fmt["agent_url"], "id": fmt["id"]}
        if not raw_format_id.get("agent_url") or not raw_format_id.get("id"):
            continue
        canonical_format_id = _format_id_dict(raw_format_id)
        response_formats.append(
            CreativeFormatSummary(
                format_id=FormatIdRef.model_validate(canonical_format_id),
                name=str(fmt.get("name") or canonical_format_id["id"]),
                dimensions=fmt.get("dimensions"),
                asset_types=list(fmt.get("asset_types") or []),
                requirements=dict(fmt.get("requirements") or {}),
                raw=fmt,
            )
        )
    response = ListCreativeFormatsForAuthoringResponse(
        creative_formats=response_formats,
        count=len(response_formats),
        errors=catalog.errors,
    )
    return jsonify(response.model_dump(mode="json", exclude_none=True))


@tenant_management_api.route("/tenants/<tenant_id>/signals/adapter-capabilities", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=SignalAdapterCapabilitiesResponse, HTTP_404=ApiError))
def get_signal_adapter_capabilities(tenant_id: str):
    """Return the tenant adapter's signal-mapping authoring capabilities."""
    with get_db_session() as session:
        tenant, adapter, error = _require_tenant_for_authoring(session, tenant_id)
        if error is not None:
            return error
        assert tenant is not None
        adapter_type = _tenant_adapter_type(tenant, adapter)
        mapping_kinds = _SIGNAL_MAPPING_CAPABILITIES.get(adapter_type, [])
        candidate_types = _SIGNAL_CANDIDATE_TYPE_CAPABILITIES.get(adapter_type, [])
        response = SignalAdapterCapabilitiesResponse(
            adapter=adapter_type,
            supports_signal_mapping_authoring=bool(mapping_kinds),
            mapping_kinds=mapping_kinds,
            supported_candidate_types=[cap.candidate_type for cap in candidate_types],
            candidate_types=candidate_types,
            default_candidate_type=_DEFAULT_SIGNAL_CANDIDATE_TYPE.get(adapter_type),
            targeting_semantics=_SIGNAL_TARGETING_SEMANTICS.get(adapter_type, SignalTargetingSemantics()),
        )
        return jsonify(response.model_dump())


@tenant_management_api.route("/tenants/<tenant_id>/signals/candidates", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=ListSignalCandidatesResponse, HTTP_400=ApiError, HTTP_404=ApiError))
def list_signal_candidates(tenant_id: str):
    """Search cached adapter signal candidates for signal-mapping setup."""
    candidate_type = request.args.get("candidate_type") or request.args.get("candidateType")
    q = request.args.get("q")
    parent_id = request.args.get("parent_id") or request.args.get("parentId")
    try:
        limit = min(max(int(request.args.get("limit", "50")), 1), 100)
        offset = max(int(request.args.get("cursor", "0")), 0)
    except ValueError:
        return _api_error("invalid_pagination", "limit and cursor must be integers", 400)

    with get_db_session() as session:
        tenant, adapter, error = _require_tenant_for_authoring(session, tenant_id)
        if error is not None:
            return error
        assert tenant is not None
        adapter_type = _tenant_adapter_type(tenant, adapter)
        supported = _supported_signal_candidate_types(adapter_type)
        resolved_candidate_type = candidate_type or (sorted(supported)[0] if supported else None)
        if resolved_candidate_type is None:
            response = ListSignalCandidatesResponse(candidates=[], count=0)
            return jsonify(response.model_dump())
        if resolved_candidate_type not in supported:
            return _api_error(
                "unsupported_signal_candidate_type",
                f"Signal candidate type {resolved_candidate_type!r} is not supported for adapter {adapter_type!r}",
                400,
                details={"supported_candidate_types": sorted(supported)},
            )
        rows = _signal_candidate_rows(
            session,
            tenant_id,
            adapter_type,
            resolved_candidate_type,
            q=q,
            parent_id=parent_id,
            offset=offset,
            limit=limit + 1,
        )
        session.commit()
        page_rows = rows[:limit]
        next_cursor = str(offset + limit) if len(rows) > limit else None
        response = ListSignalCandidatesResponse(
            candidates=page_rows,
            count=len(page_rows),
            next_cursor=next_cursor,
        )
        return jsonify(response.model_dump())


@tenant_management_api.route("/tenants/<tenant_id>/signals:validate", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(
    json=SignalMappingRequest,
    resp=Response(HTTP_200=SignalMappingValidationResponse, HTTP_404=ApiError),
)
def validate_signal_mapping(tenant_id: str):
    """Validate a signal mapping draft without persisting it."""
    req: SignalMappingRequest = _validated_json_payload()
    with get_db_session() as session:
        tenant, adapter, error = _require_tenant_for_authoring(session, tenant_id)
        if error is not None:
            return error
        assert tenant is not None
        adapter_type = _tenant_adapter_type(tenant, adapter)
        response = _validate_signal_mapping(session, tenant_id, req, adapter_type, check_candidate_cache=True)
        return jsonify(response.model_dump())


@tenant_management_api.route("/tenants/<tenant_id>/signals", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=ListSignalMappingsResponse, HTTP_400=ApiError, HTTP_404=ApiError))
def list_signal_mappings(tenant_id: str):
    """List signal mappings for an embedded tenant."""
    updated_since_raw = request.args.get("updated_since")
    updated_since: datetime | None = None
    if updated_since_raw:
        try:
            updated_since = datetime.fromisoformat(updated_since_raw.replace("Z", "+00:00"))
        except ValueError:
            return _api_error("invalid_updated_since", "updated_since must be an ISO-8601 datetime", 400)
    with get_db_session() as session:
        tenant, _adapter, error = _require_tenant_for_authoring(session, tenant_id)
        if error is not None:
            return error
        assert tenant is not None
        signals = TenantSignalRepository(session, tenant_id).list_all(updated_since=updated_since)
        response = ListSignalMappingsResponse(
            signals=[_signal_mapping_response(signal) for signal in signals],
            count=len(signals),
        )
        return jsonify(response.model_dump(mode="json"))


@tenant_management_api.route("/tenants/<tenant_id>/signals", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(
    json=SignalMappingRequest,
    resp=Response(HTTP_201=SignalMappingResponse, HTTP_400=ApiError, HTTP_404=ApiError, HTTP_409=ApiError),
)
def create_signal_mapping(tenant_id: str):
    """Create one signal mapping backed by TenantSignal."""
    req: SignalMappingRequest = _validated_json_payload()
    with get_db_session() as session:
        session.info["management_api_caller"] = True
        tenant, adapter, error = _require_tenant_for_authoring(session, tenant_id)
        if error is not None:
            return error
        assert tenant is not None
        adapter_type = _tenant_adapter_type(tenant, adapter)
        validation = _validate_signal_mapping(session, tenant_id, req, adapter_type, check_candidate_cache=True)
        if not validation.valid:
            return _api_error(
                "invalid_signal_mapping",
                "Signal mapping failed validation",
                400,
                details={"issues": [issue.model_dump() for issue in validation.issues]},
            )
        repo = TenantSignalRepository(session, tenant_id)
        if repo.get_by_id(req.signal_id) is not None:
            return _api_error("signal_mapping_exists", f"Signal mapping {req.signal_id!r} already exists", 409)
        signal = TenantSignal(
            tenant_id=tenant_id,
            signal_id=req.signal_id,
            name=req.name,
            description=req.description,
            value_type=req.value_type,
            categories=list(req.categories or []),
            tags=list(req.tags or []),
            range_min=req.range.min if req.range else None,
            range_max=req.range.max if req.range else None,
            adapter_config=dict(req.adapter_config or {}),
            data_provider=req.data_provider,
            targeting_dimension=req.targeting_dimension,
        )
        _refresh_signal_etag(signal)
        repo.add(signal)
        session.commit()
        _notify_signal_mapping_changed(tenant_id, "created", signal.signal_id, signal.name)
        return jsonify(_signal_mapping_response(signal).model_dump(mode="json")), 201


@tenant_management_api.route("/tenants/<tenant_id>/signals/<signal_id>", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=SignalMappingResponse, HTTP_404=ApiError))
def get_signal_mapping(tenant_id: str, signal_id: str):
    """Get one signal mapping."""
    with get_db_session() as session:
        tenant, _adapter, error = _require_tenant_for_authoring(session, tenant_id)
        if error is not None:
            return error
        assert tenant is not None
        signal = TenantSignalRepository(session, tenant_id).get_by_id(signal_id)
        if signal is None:
            return _api_error("signal_mapping_not_found", f"Signal mapping {signal_id!r} was not found", 404)
        return jsonify(_signal_mapping_response(signal).model_dump(mode="json"))


@tenant_management_api.route("/tenants/<tenant_id>/signals/<signal_id>", methods=["PUT"])
@require_tenant_management_api_key
@spec.validate(
    json=SignalMappingRequest,
    resp=Response(HTTP_200=SignalMappingResponse, HTTP_400=ApiError, HTTP_404=ApiError),
)
def put_signal_mapping(tenant_id: str, signal_id: str):
    """Replace one signal mapping."""
    req: SignalMappingRequest = _validated_json_payload()
    if req.signal_id != signal_id:
        return _api_error("signal_id_mismatch", "Path signal_id must match body signal_id", 400)
    with get_db_session() as session:
        session.info["management_api_caller"] = True
        tenant, adapter, error = _require_tenant_for_authoring(session, tenant_id)
        if error is not None:
            return error
        assert tenant is not None
        adapter_type = _tenant_adapter_type(tenant, adapter)
        validation = _validate_signal_mapping(session, tenant_id, req, adapter_type, check_candidate_cache=True)
        if not validation.valid:
            return _api_error(
                "invalid_signal_mapping",
                "Signal mapping failed validation",
                400,
                details={"issues": [issue.model_dump() for issue in validation.issues]},
            )
        signal = TenantSignalRepository(session, tenant_id).get_by_id(signal_id)
        if signal is None:
            return _api_error("signal_mapping_not_found", f"Signal mapping {signal_id!r} was not found", 404)
        _set_signal_fields(signal, req)
        session.commit()
        _notify_signal_mapping_changed(tenant_id, "updated", signal.signal_id, signal.name)
        return jsonify(_signal_mapping_response(signal).model_dump(mode="json"))


@tenant_management_api.route("/tenants/<tenant_id>/signals/<signal_id>", methods=["DELETE"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=DeleteSignalMappingResponse, HTTP_404=ApiError, HTTP_409=ApiError))
def delete_signal_mapping(tenant_id: str, signal_id: str):
    """Delete one signal mapping if no active media buy references it."""
    confirm_referenced = request.args.get("confirm_referenced", "").lower() in {"1", "true", "yes"}
    with get_db_session() as session:
        session.info["management_api_caller"] = True
        tenant, _adapter, error = _require_tenant_for_authoring(session, tenant_id)
        if error is not None:
            return error
        assert tenant is not None
        repo = TenantSignalRepository(session, tenant_id)
        signal = repo.get_by_id(signal_id)
        if signal is None:
            return _api_error("signal_mapping_not_found", f"Signal mapping {signal_id!r} was not found", 404)
        active_references = SignalUsageRepository(session, tenant_id).count_references(signal_id)
        if active_references and not confirm_referenced:
            return _api_error(
                "signal_mapping_in_use",
                f"Signal mapping {signal_id!r} is referenced by {active_references} active media buy(s).",
                409,
                details={"active_references": active_references},
            )
        signal_name = signal.name
        repo.delete(signal)
        session.commit()
        _notify_signal_mapping_changed(tenant_id, "deleted", signal_id, signal_name)
        response = DeleteSignalMappingResponse(success=True, message=f"Signal mapping {signal_id!r} deleted")
        return jsonify(response.model_dump())


@tenant_management_api.route("/tenants/<tenant_id>/wholesale-products:validate", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(
    json=WholesaleProductRequest,
    resp=Response(HTTP_200=WholesaleProductValidationResponse, HTTP_404=ApiError),
)
def validate_wholesale_product(tenant_id: str):
    """Validate a wholesale-product draft without persisting it."""
    req: WholesaleProductRequest = _validated_json_payload()
    with get_db_session() as session:
        tenant, adapter, error = _require_tenant_for_authoring(session, tenant_id)
        if error is not None:
            return error
        assert tenant is not None
        adapter_type = _tenant_adapter_type(tenant, adapter)
        response = _validate_wholesale_product(session, tenant_id, req, adapter_type, check_selector_cache=True)
        return jsonify(response.model_dump())


@tenant_management_api.route("/tenants/<tenant_id>/wholesale-products:preview", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(
    json=WholesaleProductRequest,
    resp=Response(HTTP_200=WholesaleProductPreviewResponse, HTTP_404=ApiError),
)
def preview_wholesale_product(tenant_id: str):
    """Preview buyer and adapter projections for a wholesale-product draft."""
    req: WholesaleProductRequest = _validated_json_payload()
    product_id = req.wholesale_product_id or "preview"
    with get_db_session() as session:
        tenant, adapter, error = _require_tenant_for_authoring(session, tenant_id)
        if error is not None:
            return error
        assert tenant is not None
        adapter_type = _tenant_adapter_type(tenant, adapter)
        validation = _validate_wholesale_product(session, tenant_id, req, adapter_type, check_selector_cache=True)
        default_currency = _default_wholesale_currency_for_authoring(session, tenant_id, adapter)
        response = WholesaleProductPreviewResponse(
            validation=validation,
            buyer_projection=_buyer_projection(req, product_id, default_currency),
            adapter_projection=_adapter_projection(req, adapter_type),
        )
        return jsonify(response.model_dump())


@tenant_management_api.route("/tenants/<tenant_id>/wholesale-products", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=ListWholesaleProductsResponse, HTTP_404=ApiError))
def list_wholesale_products(tenant_id: str):
    """List wholesale products for an embedded tenant."""
    with get_db_session() as session:
        tenant, adapter, error = _require_tenant_for_authoring(session, tenant_id)
        if error is not None:
            return error
        assert tenant is not None
        adapter_type = _tenant_adapter_type(tenant, adapter)
        default_currency = _default_wholesale_currency_for_authoring(session, tenant_id, adapter)
        profiles = [
            profile
            for profile in InventoryProfileRepository(session, tenant_id).list_all()
            if is_complete_inventory_profile(profile) and is_wholesale_owned_inventory_profile(profile)
        ]
        response = ListWholesaleProductsResponse(
            wholesale_products=[
                _wholesale_response_from_profile(profile, adapter_type, default_currency) for profile in profiles
            ],
            count=len(profiles),
        )
        return jsonify(response.model_dump(mode="json"))


@tenant_management_api.route("/tenants/<tenant_id>/wholesale-products", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(
    json=WholesaleProductRequest,
    resp=Response(HTTP_201=WholesaleProductResponse, HTTP_400=ApiError, HTTP_404=ApiError, HTTP_409=ApiError),
)
def create_wholesale_product(tenant_id: str):
    """Create a wholesale product backed by an InventoryProfile bundle."""
    req: WholesaleProductRequest = _validated_json_payload()
    product_id = req.wholesale_product_id or f"wp_{uuid.uuid4().hex[:12]}"
    with get_db_session() as session:
        session.info["management_api_caller"] = True
        tenant, adapter, error = _require_tenant_for_authoring(session, tenant_id)
        if error is not None:
            return error
        assert tenant is not None
        adapter_type = _tenant_adapter_type(tenant, adapter)
        validation = _validate_wholesale_product(session, tenant_id, req, adapter_type, check_selector_cache=True)
        if not validation.valid:
            return _api_error(
                "invalid_wholesale_product",
                "Wholesale product failed validation",
                400,
                details={"issues": [issue.model_dump() for issue in validation.issues]},
            )
        if ProductRepository(session, tenant_id).get_by_id(product_id) is not None:
            return _api_error("wholesale_product_exists", f"Wholesale product {product_id!r} already exists", 409)
        profile_repo = InventoryProfileRepository(session, tenant_id)
        existing_profile = profile_repo.get_by_id(product_id)
        if existing_profile is not None and is_wholesale_owned_inventory_profile(existing_profile, product_id):
            return _api_error("wholesale_product_exists", f"Wholesale product {product_id!r} already exists", 409)
        if existing_profile is not None and not is_wholesale_owned_inventory_profile(existing_profile, product_id):
            return _inventory_profile_conflict(product_id)
        profile = _build_wholesale_inventory_profile(
            tenant_id,
            product_id,
            req,
            adapter_type,
            existing_profile=existing_profile,
        )
        if existing_profile is None:
            profile_repo.add(profile)
        session.commit()
        invalidate_status_cache(tenant_id)

        publish_product_catalog_change(
            tenant_id=tenant_id,
            action="created",
            product_id=product_id,
            data={"name": profile.name},
            principal_ids=req.allowed_principal_ids or None,
        )
        default_currency = _default_wholesale_currency_for_authoring(session, tenant_id, adapter)
        response = _wholesale_response_from_profile(profile, adapter_type, default_currency)
        return jsonify(response.model_dump(mode="json")), 201


@tenant_management_api.route("/tenants/<tenant_id>/wholesale-products/<product_id>", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=WholesaleProductResponse, HTTP_404=ApiError))
def get_wholesale_product(tenant_id: str, product_id: str):
    """Get one wholesale product."""
    with get_db_session() as session:
        tenant, adapter, error = _require_tenant_for_authoring(session, tenant_id)
        if error is not None:
            return error
        assert tenant is not None
        profile = InventoryProfileRepository(session, tenant_id).get_by_id(product_id)
        if (
            profile is not None
            and is_complete_inventory_profile(profile)
            and is_wholesale_owned_inventory_profile(profile, product_id)
        ):
            adapter_type = _tenant_adapter_type(tenant, adapter)
            default_currency = _default_wholesale_currency_for_authoring(session, tenant_id, adapter)
            return jsonify(
                _wholesale_response_from_profile(profile, adapter_type, default_currency).model_dump(mode="json")
            )
        return _api_error("wholesale_product_not_found", f"Wholesale product {product_id!r} was not found", 404)


@tenant_management_api.route("/tenants/<tenant_id>/wholesale-products/<product_id>", methods=["PUT"])
@require_tenant_management_api_key
@spec.validate(
    json=WholesaleProductRequest,
    resp=Response(HTTP_200=WholesaleProductResponse, HTTP_400=ApiError, HTTP_404=ApiError, HTTP_409=ApiError),
)
def put_wholesale_product(tenant_id: str, product_id: str):
    """Replace one wholesale product."""
    req: WholesaleProductRequest = _validated_json_payload()
    if req.wholesale_product_id is not None and req.wholesale_product_id != product_id:
        return _api_error(
            "wholesale_product_id_mismatch",
            "Path product_id must match body wholesale_product_id",
            400,
        )
    with get_db_session() as session:
        session.info["management_api_caller"] = True
        tenant, adapter, error = _require_tenant_for_authoring(session, tenant_id)
        if error is not None:
            return error
        assert tenant is not None
        adapter_type = _tenant_adapter_type(tenant, adapter)
        validation = _validate_wholesale_product(session, tenant_id, req, adapter_type, check_selector_cache=True)
        if not validation.valid:
            return _api_error(
                "invalid_wholesale_product",
                "Wholesale product failed validation",
                400,
                details={"issues": [issue.model_dump() for issue in validation.issues]},
            )
        profile_repo = InventoryProfileRepository(session, tenant_id)
        profile = profile_repo.get_by_id(product_id)
        if profile is None:
            return _api_error("wholesale_product_not_found", f"Wholesale product {product_id!r} was not found", 404)
        if not is_wholesale_owned_inventory_profile(profile, product_id):
            return _inventory_profile_conflict(product_id)
        previous_allowed_principal_ids = list(_profile_constraints(profile).get("allowed_principal_ids") or [])
        profile = _build_wholesale_inventory_profile(
            tenant_id,
            product_id,
            req,
            adapter_type,
            existing_profile=profile,
        )
        legacy_product = ProductRepository(session, tenant_id).get_by_id(product_id)
        if legacy_product is not None:
            ProductRepository(session, tenant_id).delete(legacy_product)
        session.commit()
        invalidate_status_cache(tenant_id)

        publish_product_catalog_change(
            tenant_id=tenant_id,
            action="updated",
            product_id=product_id,
            data={"name": profile.name},
            principal_ids=catalog_acl_notification_scope(previous_allowed_principal_ids, req.allowed_principal_ids),
        )
        default_currency = _default_wholesale_currency_for_authoring(session, tenant_id, adapter)
        return jsonify(
            _wholesale_response_from_profile(profile, adapter_type, default_currency).model_dump(mode="json")
        )


@tenant_management_api.route("/tenants/<tenant_id>/wholesale-products/<product_id>", methods=["DELETE"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=DeleteWholesaleProductResponse, HTTP_404=ApiError))
def delete_wholesale_product(tenant_id: str, product_id: str):
    """Delete one wholesale product."""
    with get_db_session() as session:
        session.info["management_api_caller"] = True
        tenant, _adapter, error = _require_tenant_for_authoring(session, tenant_id)
        if error is not None:
            return error
        assert tenant is not None
        profile_repo = InventoryProfileRepository(session, tenant_id)
        profile = profile_repo.get_by_id(product_id)
        legacy_product = ProductRepository(session, tenant_id).get_by_id(product_id)
        if profile is None and legacy_product is None:
            return _api_error("wholesale_product_not_found", f"Wholesale product {product_id!r} was not found", 404)
        if legacy_product is not None and (
            profile is None or not is_wholesale_owned_inventory_profile(profile, product_id)
        ):
            ProductRepository(session, tenant_id).delete(legacy_product)
            session.commit()
            invalidate_status_cache(tenant_id)
            publish_product_record_catalog_change(tenant_id=tenant_id, action="deleted", product=legacy_product)
            response = DeleteWholesaleProductResponse(success=True, message=f"Wholesale product {product_id!r} deleted")
            return jsonify(response.model_dump())
        if profile is not None and not is_wholesale_owned_inventory_profile(profile, product_id):
            return _api_error("wholesale_product_not_found", f"Wholesale product {product_id!r} was not found", 404)

        assert profile is not None
        product_name = profile.name
        allowed_principal_ids = _profile_constraints(profile).get("allowed_principal_ids") or None
        if legacy_product is not None:
            ProductRepository(session, tenant_id).delete(legacy_product)
        profile_repo.delete(profile)
        session.commit()
        invalidate_status_cache(tenant_id)

        publish_product_catalog_change(
            tenant_id=tenant_id,
            action="deleted",
            product_id=product_id,
            data={"name": product_name},
            principal_ids=allowed_principal_ids,
        )
        response = DeleteWholesaleProductResponse(success=True, message=f"Wholesale product {product_id!r} deleted")
        return jsonify(response.model_dump())


@tenant_management_api.route("/adapters/google_ad_manager/config-schema", methods=["GET"])
@tenant_management_api.route("/adapters/gam/config-schema", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=AdapterSettingsSchemaResponse))
def get_gam_settings_schema():
    """Return the GAM runtime settings schema and supported naming macros."""
    response = _adapter_settings_schema_response(
        "google_ad_manager",
        GoogleAdManagerSettings,
        {
            "order_name_template": GAM_ORDER_NAME_MACROS,
            "line_item_name_template": GAM_LINE_ITEM_NAME_MACROS,
        },
    )
    return jsonify(response.model_dump(by_alias=True))


@tenant_management_api.route("/adapters/freewheel/config-schema", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=AdapterSettingsSchemaResponse))
def get_freewheel_settings_schema():
    """Return the FreeWheel runtime settings schema."""
    response = _adapter_settings_schema_response("freewheel", FreeWheelSettings, {})
    return jsonify(response.model_dump(by_alias=True))


@tenant_management_api.route("/adapters/broadstreet/config-schema", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=AdapterSettingsSchemaResponse))
def get_broadstreet_settings_schema():
    """Return the Broadstreet runtime settings schema and supported naming macros."""
    response = _adapter_settings_schema_response(
        "broadstreet",
        BroadstreetSettings,
        {"campaign_name_template": BROADSTREET_CAMPAIGN_NAME_MACROS},
    )
    return jsonify(response.model_dump(by_alias=True))


@tenant_management_api.route("/adapters/springserve/config-schema", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=AdapterSettingsSchemaResponse))
def get_springserve_settings_schema():
    """Return the SpringServe runtime settings schema."""
    response = _adapter_settings_schema_response("springserve", SpringServeSettings, {})
    return jsonify(response.model_dump(by_alias=True))


def _get_adapter_settings_row(session, tenant_id: str, adapter_type: str) -> tuple[Tenant | None, AdapterConfig | None]:
    """Fetch the tenant and matching adapter config for runtime settings."""

    tenant = TenantConfigRepository(session, tenant_id).get_tenant()
    if tenant is None:
        return None, None
    adapter = AdapterConfigRepository(session, tenant_id).find_by_tenant()
    if adapter is None or adapter.adapter_type != adapter_type:
        return tenant, None
    return tenant, adapter


def _gam_settings_from_adapter(adapter: AdapterConfig, tenant: Tenant | None = None) -> GoogleAdManagerSettings:
    """Build GAM runtime settings from the stored adapter row."""

    manual_approval_required = bool(adapter.gam_manual_approval_required)
    if tenant is not None:
        manual_approval_required = bool(tenant.human_review_required or adapter.gam_manual_approval_required)

    return GoogleAdManagerSettings(
        order_name_template=adapter.gam_order_name_template,
        line_item_name_template=adapter.gam_line_item_name_template,
        auto_naming_enabled=tenant.auto_naming_enabled if tenant is not None else True,
        manual_approval_required=manual_approval_required,
    )


def _freewheel_settings_from_adapter(adapter: AdapterConfig) -> FreeWheelSettings:
    """Build FreeWheel runtime settings from the stored adapter row."""

    config = dict(adapter.config_json or {})
    return FreeWheelSettings(default_advertiser_id=config.get("default_advertiser_id"))


def _broadstreet_settings_from_adapter(adapter: AdapterConfig) -> BroadstreetSettings:
    """Build Broadstreet runtime settings from the stored adapter row."""

    config = dict(adapter.config_json or {})
    return BroadstreetSettings(
        default_advertiser_id=config.get("default_advertiser_id"),
        campaign_name_template=config.get("campaign_name_template") or "AdCP-{po_number}-{product_name}",
    )


def _springserve_settings_from_adapter(adapter: AdapterConfig) -> SpringServeSettings:
    """Build SpringServe runtime settings from the stored adapter row."""

    config = dict(adapter.config_json or {})
    return SpringServeSettings(
        default_demand_partner_id=config.get("default_demand_partner_id"),
        rate_currency=config.get("rate_currency") or "USD",
        demand_class=config.get("demand_class") or "line_item",
        enable_key_value_targeting=bool(config.get("enable_key_value_targeting", False)),
    )


def _adapter_settings_not_found(tenant_id: str, adapter_type: str):
    return _api_error(
        "adapter_config_not_found",
        f"Tenant {tenant_id!r} is not configured for adapter {adapter_type!r}",
        404,
    )


def _connection_config_model(adapter_type: str) -> type[Any] | None:
    """Return the adapter connection schema used to validate stored config."""

    if adapter_type == "freewheel":
        from src.adapters.freewheel import FreeWheelConnectionConfig

        return FreeWheelConnectionConfig
    if adapter_type == "broadstreet":
        from src.adapters.broadstreet.schemas import BroadstreetConnectionConfig

        return BroadstreetConnectionConfig
    if adapter_type == "springserve":
        from src.adapters.springserve import SpringServeConnectionConfig

        return SpringServeConnectionConfig
    return None


def _validated_connection_config_payload(
    adapter_type: str, config: dict[str, Any]
) -> tuple[dict[str, Any] | None, Any]:
    """Validate stored adapter connection config and return a persistable payload."""

    model = _connection_config_model(adapter_type)
    if model is None:
        return dict(config), None

    try:
        validated = model(**config)
    except PydanticValidationError as exc:
        return None, _api_error(
            "adapter_connection_config_incomplete",
            f"Stored {adapter_type!r} connection config is incomplete; configure credentials first",
            400,
            details={"errors": _pydantic_error_details(exc)},
        )
    return validated.model_dump(), None


def _validate_adapter_settings_target(
    session,
    tenant_id: str,
    adapter_type: str,
    *,
    require_connection_config: bool = False,
) -> tuple[AdapterConfig | None, Any]:
    """Validate that a runtime-settings target exists and is saveable."""

    tenant, adapter = _get_adapter_settings_row(session, tenant_id, adapter_type)
    if tenant is None:
        return None, _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)
    if adapter is None:
        return None, _adapter_settings_not_found(tenant_id, adapter_type)
    if require_connection_config:
        _, error = _validated_connection_config_payload(adapter_type, dict(adapter.config_json or {}))
        if error is not None:
            return None, error
    return adapter, None


@tenant_management_api.route("/tenants/<tenant_id>/adapters/google_ad_manager/config", methods=["GET"])
@tenant_management_api.route("/tenants/<tenant_id>/adapters/gam/config", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=GoogleAdManagerSettings, HTTP_404=ApiError))
def get_gam_settings(tenant_id: str):
    """Return tenant-level GAM runtime settings."""
    with get_db_session() as session:
        tenant, adapter = _get_adapter_settings_row(session, tenant_id, "google_ad_manager")
        if tenant is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)
        if adapter is None:
            return _adapter_settings_not_found(tenant_id, "google_ad_manager")
        return jsonify(_gam_settings_from_adapter(adapter, tenant).model_dump())


@tenant_management_api.route("/tenants/<tenant_id>/adapters/google_ad_manager/config:validate", methods=["POST"])
@tenant_management_api.route("/tenants/<tenant_id>/adapters/gam/config:validate", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(json=GoogleAdManagerSettings, resp=Response(HTTP_200=AdapterSettingsValidationResponse))
def validate_gam_settings(tenant_id: str):
    """Validate GAM runtime settings without modifying state."""
    settings: GoogleAdManagerSettings = _validated_json_payload()
    with get_db_session() as session:
        _adapter, error = _validate_adapter_settings_target(session, tenant_id, "google_ad_manager")
        if error is not None:
            return error

    response = _settings_validation_response(
        {
            "order_name_template": settings.order_name_template,
            "line_item_name_template": settings.line_item_name_template,
        },
        {
            "order_name_template": GAM_ORDER_NAME_MACROS,
            "line_item_name_template": GAM_LINE_ITEM_NAME_MACROS,
        },
    )
    return jsonify(response.model_dump())


@tenant_management_api.route("/tenants/<tenant_id>/adapters/google_ad_manager/config", methods=["PUT"])
@tenant_management_api.route("/tenants/<tenant_id>/adapters/gam/config", methods=["PUT"])
@require_tenant_management_api_key
@spec.validate(
    json=GoogleAdManagerSettings,
    resp=Response(HTTP_200=GoogleAdManagerSettings, HTTP_400=ApiError, HTTP_404=ApiError),
)
def put_gam_settings(tenant_id: str):
    """Update tenant-level GAM runtime settings."""
    settings: GoogleAdManagerSettings = _validated_json_payload()
    validation = _settings_validation_response(
        {
            "order_name_template": settings.order_name_template,
            "line_item_name_template": settings.line_item_name_template,
        },
        {
            "order_name_template": GAM_ORDER_NAME_MACROS,
            "line_item_name_template": GAM_LINE_ITEM_NAME_MACROS,
        },
    )
    if not validation.valid:
        return _api_error(
            "invalid_adapter_settings",
            "Adapter settings failed validation",
            400,
            details={"errors": [error.model_dump() for error in validation.errors]},
        )

    with get_db_session() as session:
        session.info["management_api_caller"] = True
        tenant, adapter = _get_adapter_settings_row(session, tenant_id, "google_ad_manager")
        if tenant is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)
        if adapter is None:
            return _adapter_settings_not_found(tenant_id, "google_ad_manager")

        adapter.gam_order_name_template = settings.order_name_template
        adapter.gam_line_item_name_template = settings.line_item_name_template
        adapter.gam_manual_approval_required = settings.manual_approval_required
        tenant.auto_naming_enabled = settings.auto_naming_enabled
        tenant.human_review_required = settings.manual_approval_required
        adapter.updated_at = datetime.now(UTC)
        session.commit()
        invalidate_status_cache(tenant_id)
        return jsonify(_gam_settings_from_adapter(adapter, tenant).model_dump())


@tenant_management_api.route("/tenants/<tenant_id>/adapters/freewheel/config", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=FreeWheelSettings, HTTP_404=ApiError))
def get_freewheel_settings(tenant_id: str):
    """Return tenant-level FreeWheel runtime settings."""
    with get_db_session() as session:
        tenant, adapter = _get_adapter_settings_row(session, tenant_id, "freewheel")
        if tenant is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)
        if adapter is None:
            return _adapter_settings_not_found(tenant_id, "freewheel")
        return jsonify(_freewheel_settings_from_adapter(adapter).model_dump())


@tenant_management_api.route("/tenants/<tenant_id>/adapters/freewheel/config:validate", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(json=FreeWheelSettings, resp=Response(HTTP_200=AdapterSettingsValidationResponse))
def validate_freewheel_settings(tenant_id: str):
    """Validate FreeWheel runtime settings without modifying state."""
    _settings: FreeWheelSettings = _validated_json_payload()
    with get_db_session() as session:
        _adapter, error = _validate_adapter_settings_target(
            session,
            tenant_id,
            "freewheel",
            require_connection_config=True,
        )
        if error is not None:
            return error
    return jsonify(AdapterSettingsValidationResponse(valid=True).model_dump())


@tenant_management_api.route("/tenants/<tenant_id>/adapters/freewheel/config", methods=["PUT"])
@require_tenant_management_api_key
@spec.validate(
    json=FreeWheelSettings,
    resp=Response(HTTP_200=FreeWheelSettings, HTTP_400=ApiError, HTTP_404=ApiError),
)
def put_freewheel_settings(tenant_id: str):
    """Update tenant-level FreeWheel runtime settings."""
    settings: FreeWheelSettings = _validated_json_payload()

    with get_db_session() as session:
        session.info["management_api_caller"] = True
        tenant, adapter = _get_adapter_settings_row(session, tenant_id, "freewheel")
        if tenant is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)
        if adapter is None:
            return _adapter_settings_not_found(tenant_id, "freewheel")

        merged = dict(adapter.config_json or {})
        merged["default_advertiser_id"] = settings.default_advertiser_id
        validated_payload, error = _validated_connection_config_payload("freewheel", merged)
        if error is not None:
            return error
        assert validated_payload is not None

        adapter.config_json = validated_payload
        attributes.flag_modified(adapter, "config_json")
        adapter.updated_at = datetime.now(UTC)
        session.commit()
        invalidate_status_cache(tenant_id)
        return jsonify(_freewheel_settings_from_adapter(adapter).model_dump())


@tenant_management_api.route("/tenants/<tenant_id>/adapters/broadstreet/config", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=BroadstreetSettings, HTTP_404=ApiError))
def get_broadstreet_settings(tenant_id: str):
    """Return tenant-level Broadstreet runtime settings."""
    with get_db_session() as session:
        tenant, adapter = _get_adapter_settings_row(session, tenant_id, "broadstreet")
        if tenant is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)
        if adapter is None:
            return _adapter_settings_not_found(tenant_id, "broadstreet")
        return jsonify(_broadstreet_settings_from_adapter(adapter).model_dump())


@tenant_management_api.route("/tenants/<tenant_id>/adapters/broadstreet/config:validate", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(json=BroadstreetSettings, resp=Response(HTTP_200=AdapterSettingsValidationResponse))
def validate_broadstreet_settings(tenant_id: str):
    """Validate Broadstreet runtime settings without modifying state."""
    settings: BroadstreetSettings = _validated_json_payload()
    with get_db_session() as session:
        _adapter, error = _validate_adapter_settings_target(
            session,
            tenant_id,
            "broadstreet",
            require_connection_config=True,
        )
        if error is not None:
            return error

    response = _settings_validation_response(
        {"campaign_name_template": settings.campaign_name_template},
        {"campaign_name_template": BROADSTREET_CAMPAIGN_NAME_MACROS},
    )
    return jsonify(response.model_dump())


@tenant_management_api.route("/tenants/<tenant_id>/adapters/broadstreet/config", methods=["PUT"])
@require_tenant_management_api_key
@spec.validate(
    json=BroadstreetSettings,
    resp=Response(HTTP_200=BroadstreetSettings, HTTP_400=ApiError, HTTP_404=ApiError),
)
def put_broadstreet_settings(tenant_id: str):
    """Update tenant-level Broadstreet runtime settings."""
    settings: BroadstreetSettings = _validated_json_payload()
    validation = _settings_validation_response(
        {"campaign_name_template": settings.campaign_name_template},
        {"campaign_name_template": BROADSTREET_CAMPAIGN_NAME_MACROS},
    )
    if not validation.valid:
        return _api_error(
            "invalid_adapter_settings",
            "Adapter settings failed validation",
            400,
            details={"errors": [error.model_dump() for error in validation.errors]},
        )

    with get_db_session() as session:
        session.info["management_api_caller"] = True
        tenant, adapter = _get_adapter_settings_row(session, tenant_id, "broadstreet")
        if tenant is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)
        if adapter is None:
            return _adapter_settings_not_found(tenant_id, "broadstreet")

        merged = dict(adapter.config_json or {})
        merged["default_advertiser_id"] = settings.default_advertiser_id
        merged["campaign_name_template"] = settings.campaign_name_template
        validated_payload, error = _validated_connection_config_payload("broadstreet", merged)
        if error is not None:
            return error
        assert validated_payload is not None

        adapter.config_json = validated_payload
        attributes.flag_modified(adapter, "config_json")
        adapter.updated_at = datetime.now(UTC)
        session.commit()
        invalidate_status_cache(tenant_id)
        return jsonify(_broadstreet_settings_from_adapter(adapter).model_dump())


@tenant_management_api.route("/tenants/<tenant_id>/adapters/springserve/config", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=SpringServeSettings, HTTP_404=ApiError))
def get_springserve_settings(tenant_id: str):
    """Return tenant-level SpringServe runtime settings."""
    with get_db_session() as session:
        tenant, adapter = _get_adapter_settings_row(session, tenant_id, "springserve")
        if tenant is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)
        if adapter is None:
            return _adapter_settings_not_found(tenant_id, "springserve")
        return jsonify(_springserve_settings_from_adapter(adapter).model_dump())


@tenant_management_api.route("/tenants/<tenant_id>/adapters/springserve/config:validate", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(json=SpringServeSettings, resp=Response(HTTP_200=AdapterSettingsValidationResponse))
def validate_springserve_settings(tenant_id: str):
    """Validate SpringServe runtime settings without modifying state."""
    _settings: SpringServeSettings = _validated_json_payload()
    with get_db_session() as session:
        _adapter, error = _validate_adapter_settings_target(
            session,
            tenant_id,
            "springserve",
            require_connection_config=True,
        )
        if error is not None:
            return error
    return jsonify(AdapterSettingsValidationResponse(valid=True).model_dump())


@tenant_management_api.route("/tenants/<tenant_id>/adapters/springserve/config", methods=["PUT"])
@require_tenant_management_api_key
@spec.validate(
    json=SpringServeSettings,
    resp=Response(HTTP_200=SpringServeSettings, HTTP_400=ApiError, HTTP_404=ApiError),
)
def put_springserve_settings(tenant_id: str):
    """Update tenant-level SpringServe runtime settings."""
    settings: SpringServeSettings = _validated_json_payload()

    with get_db_session() as session:
        session.info["management_api_caller"] = True
        tenant, adapter = _get_adapter_settings_row(session, tenant_id, "springserve")
        if tenant is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)
        if adapter is None:
            return _adapter_settings_not_found(tenant_id, "springserve")

        merged = dict(adapter.config_json or {})
        merged["default_demand_partner_id"] = settings.default_demand_partner_id
        merged["rate_currency"] = settings.rate_currency
        merged["demand_class"] = settings.demand_class
        merged["enable_key_value_targeting"] = settings.enable_key_value_targeting
        validated_payload, error = _validated_connection_config_payload("springserve", merged)
        if error is not None:
            return error
        assert validated_payload is not None

        adapter.config_json = validated_payload
        attributes.flag_modified(adapter, "config_json")
        adapter.updated_at = datetime.now(UTC)
        session.commit()
        invalidate_status_cache(tenant_id)
        return jsonify(_springserve_settings_from_adapter(adapter).model_dump())


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
                auto_approve_format_ids=json.dumps(data.get("auto_approve_format_ids", ["display_image"])),
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
            # for most child tables. These publisher-authorization tables use backrefs
            # without a delete cascade, so wipe their rows first via bulk deletes. Issuing
            # the bulk deletes explicitly avoids the unit-of-work attempting to NULL
            # non-nullable or composite-PK tenant_id columns.
            db_session.execute(delete(AuthorizedProperty).where(AuthorizedProperty.tenant_id == tenant_id))
            db_session.execute(delete(PublisherPartner).where(PublisherPartner.tenant_id == tenant_id))
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


def _auto_provision_gam_default_advertiser(tenant_id: str, adapter_dict: dict) -> None:
    """Ensure the default GAM advertiser exists and record it on the tenant.

    Checks the local sync cache first. If the advertiser is already cached,
    records it without a GAM API call. Otherwise calls ensure to find-or-create
    it in GAM, then records the result.

    Only called when the caller has explicitly opted in via
    ``provision_default_resources=True``. Failures are logged but do not
    propagate — the tenant is still valid.
    """
    try:
        with get_db_session() as session:
            session.info["management_api_caller"] = True
            tenant_row = session.get(Tenant, tenant_id)
            if tenant_row is None:
                logger.warning("[provision] tenant not found when setting default GAM advertiser tenant=%s", tenant_id)
                return
            cached = GAMSyncRepository(session, tenant_id).find_advertiser_by_name(_GAM_DEFAULT_ADVERTISER_NAME)
            if cached is not None:
                tenant_row.default_gam_advertiser_id = cached.advertiser_id
                session.commit()
                logger.info(
                    "[provision] default GAM advertiser set from cache tenant=%s advertiser_id=%s",
                    tenant_id,
                    cached.advertiser_id,
                )
                return

        result = gam_ensure_advertiser_companyservice(
            network_code=str(adapter_dict["network_code"]),
            config=adapter_dict,
            name=_GAM_DEFAULT_ADVERTISER_NAME,
        )
        with get_db_session() as session:
            session.info["management_api_caller"] = True
            tenant_row = session.get(Tenant, tenant_id)
            if tenant_row is None:
                return
            tenant_row.default_gam_advertiser_id = result.advertiser_id
            GAMSyncRepository(session, tenant_id).upsert_advertiser(
                advertiser_id=result.advertiser_id,
                name=_GAM_DEFAULT_ADVERTISER_NAME,
                status="active",
                synced_at=datetime.now(UTC),
            )
            if result.created:
                adapter = AdapterConfigRepository(session, tenant_id).find_by_tenant()
                if adapter is not None:
                    adapter.gam_advertiser_create_permission_proven_at = datetime.now(UTC)
            session.commit()
        logger.info(
            "[provision] default GAM advertiser set tenant=%s advertiser_id=%s created=%s",
            tenant_id,
            result.advertiser_id,
            result.created,
        )
    except Exception:
        logger.exception(
            "[provision] default GAM advertiser auto-provision failed for tenant=%s — "
            "tenant is still provisioned; use the ensure endpoint to set default_gam_advertiser_id manually",
            tenant_id,
        )


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
    req: ProvisionTenantRequest = _validated_json_payload()

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
        media_buy_manual_approval = _tenant_media_buy_manual_approval_required(
            req.media_buy_approval,
            default=True,
        )

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
            approval_mode=_tenant_creative_approval_mode(req.creative_approval),
            embed_breadcrumb_root=(
                req.embed_breadcrumb_root.model_dump() if req.embed_breadcrumb_root is not None else None
            ),
            authorized_emails=[req.contact_email],
            authorized_domains=[],
            human_review_required=media_buy_manual_approval,
            auto_approve_format_ids=[],
            measurement_providers={"providers": ["Publisher Ad Server"], "default": "Publisher Ad Server"},
        )
        session.add(new_tenant)
        session.flush()

        _persist_adapter_config(
            session,
            tenant_id,
            req.adapter,
            manual_approval_required=media_buy_manual_approval,
        )

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

    if req.provision_default_resources and not req.default_gam_advertiser_id:
        if adapter_dict.get("type") == "google_ad_manager":
            _auto_provision_gam_default_advertiser(tenant_id, adapter_dict)

    mcp_url, a2a_url, admin_url_path = _surface_urls(tenant_id, subdomain, request.url_root)
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
    req: PreviewAdapterRequest = _validated_json_payload()
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
    req: UpdateTenantRequest = _validated_json_payload()

    with get_db_session() as session:
        session.info["management_api_caller"] = True

        tenant = TenantConfigRepository(session, tenant_id).get_tenant()
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
            previous_agent_url = resolve_agent_url(tenant)
            tenant.public_agent_url = req.public_agent_url
            if not _agent_urls_match(previous_agent_url, resolve_agent_url(tenant)):
                TenantConfigRepository(session, tenant_id).invalidate_publisher_partner_aao_statuses(
                    "Agent URL changed; refresh publisher authorization."
                )
        if "default_gam_advertiser_id" in req.model_fields_set:
            tenant.default_gam_advertiser_id = req.default_gam_advertiser_id
        if req.embed_breadcrumb_root is not None:
            tenant.embed_breadcrumb_root = req.embed_breadcrumb_root.model_dump()
        if req.creative_approval is not None:
            tenant.approval_mode = _tenant_creative_approval_mode(req.creative_approval)
        if req.media_buy_approval is not None:
            manual_approval_required = _tenant_media_buy_manual_approval_required(
                req.media_buy_approval,
                default=bool(tenant.human_review_required),
            )
            tenant.human_review_required = manual_approval_required
            adapter = AdapterConfigRepository(session, tenant_id).find_by_tenant()
            if adapter is not None:
                _set_adapter_manual_approval_required(adapter, manual_approval_required)
                adapter.updated_at = datetime.now(UTC)
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
        tenant = TenantConfigRepository(session, tenant_id).get_tenant()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        adapter = AdapterConfigRepository(session, tenant_id).find_by_tenant()
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
    body = _validated_json_payload()
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

        new_adapter = _persist_adapter_config(
            session,
            tenant_id,
            adapter_schema,
            manual_approval_required=bool(tenant.human_review_required),
        )
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
        tenant = TenantConfigRepository(session, tenant_id).get_tenant()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        adapter = AdapterConfigRepository(session, tenant_id).find_by_tenant()
        if adapter is None:
            return jsonify(
                TestConnectionResponse(
                    success=False, error="No adapter configured", tested_at=datetime.now(UTC)
                ).model_dump(mode="json")
            )

        probe = probe_adapter_connection(adapter.adapter_type, _adapter_probe_config(adapter))
        invalidate_status_cache(tenant_id)
        return jsonify(
            TestConnectionResponse(
                success=probe.success,
                error=probe.error_message,
                error_code=probe.error_code,
                remediation=probe.remediation,
                details=probe.details or None,
                capability_checks=_adapter_capability_checks(adapter.adapter_type, probe),
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


def _grant_account_access_for_existing_principal(
    session,
    tenant_id: str,
    principal_id: str | None,
    account_id: str,
) -> None:
    """Grant AgentAccountAccess for pre-mapped agent-billed accounts.

    Tenant-management callers may create accounts before the buyer principal
    has been provisioned. In that case Account.principal_id still records the
    intended owner, and the later sync_accounts path will grant access once the
    principal exists. When the principal already exists, grant immediately so
    list_accounts/create_media_buy authorization sees the account without an
    extra sync_accounts round trip.
    """
    if principal_id is None:
        return
    principal = session.get(Principal, (tenant_id, principal_id))
    if principal is None:
        logger.info(
            "Skipping AgentAccountAccess grant for account %s: principal %s/%s does not exist yet",
            account_id,
            tenant_id,
            principal_id,
        )
        return
    AccountRepository(session, tenant_id).ensure_access(principal_id, account_id)


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
    req: CreateAccountRequest = _validated_json_payload()

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
            _grant_account_access_for_existing_principal(
                session,
                tenant_id,
                req.buyer_agent_principal_id if req.billing == "agent" else None,
                account.account_id,
            )
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
        old_status = existing.status
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
        _grant_account_access_for_existing_principal(
            session,
            tenant_id,
            existing.principal_id if existing.billing == "agent" else None,
            existing.account_id,
        )
        existing.updated_at = datetime.now(UTC)

        try:
            session.commit()
        except EmbeddedTenantWriteError as exc:
            session.rollback()
            return _api_error("managed_tenant_write_blocked", str(exc), 403)
        session.refresh(existing)
        invalidate_status_cache(tenant_id)
        if old_status != existing.status:
            notify_account_status_changed(
                tenant_id=tenant_id,
                account_id=existing.account_id,
                from_status=old_status,
                to_status=existing.status,
                principal_id=existing.principal_id,
            )
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
    req: CreateBuyerAdvertiserMappingRequest = _validated_json_payload()

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
        invalidate_status_cache(tenant_id)
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
    req: UpdateBuyerAdvertiserMappingRequest = _validated_json_payload()

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
        invalidate_status_cache(tenant_id)
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
        invalidate_status_cache(tenant_id)

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

            advertisers_sync_id = advertisers_id

            def _run_advertisers_in_thread(
                tenant_id: str = tenant_id,
                sync_id: str = advertisers_sync_id,
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


@tenant_management_api.route("/tenants/<tenant_id>/targeting/values/<key_id>/refresh", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(
    resp=Response(HTTP_200=TargetingValuesRefreshResponse, HTTP_400=ApiError, HTTP_404=ApiError, HTTP_502=ApiError)
)
def refresh_targeting_values(tenant_id: str, key_id: str):
    """Refresh one custom-targeting key's values into the local cache.

    Embedded storefronts call this when the publisher UI reports
    ``needs_sync`` for a key. The regular UI endpoint remains cache-first;
    this management endpoint is the server-to-server path that is allowed to
    talk to GAM and populate ``gam_inventory`` lazily per key.
    """
    with get_db_session() as session:
        session.info["management_api_caller"] = True

        tenant_config = TenantConfigRepository(session, tenant_id)
        tenant = tenant_config.get_tenant()
        if tenant is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        gam_sync_repo = GAMSyncRepository(session, tenant_id)
        key_row = gam_sync_repo.find_inventory_item("custom_targeting_key", key_id)
        if key_row is None:
            return _api_error("targeting_key_not_found", f"Custom targeting key {key_id!r} does not exist", 404)

        adapter_config = tenant_config.get_adapter_config()
        if (
            adapter_config is None
            or adapter_config.adapter_type != "google_ad_manager"
            or not adapter_config.gam_network_code
        ):
            return _api_error(
                "gam_not_configured",
                f"Tenant {tenant_id!r} is not configured for Google Ad Manager targeting refresh",
                400,
            )

        if not AdapterConfigRepository.has_gam_credentials(adapter_config):
            return _api_error(
                "gam_credentials_unavailable",
                f"Tenant {tenant_id!r} has no GAM credentials available for targeting refresh",
                400,
            )

        try:
            discovery = build_gam_inventory_discovery(adapter_config, tenant_id)
            values = sync_targeting_values_for_key(
                gam_sync_repo,
                key_id=key_id,
                key_row=key_row,
                discovery=discovery,
                max_values=1000,
            )
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.exception(
                "Targeting value refresh failed for tenant_id=%s key_id=%s",
                tenant_id,
                key_id,
            )
            return _api_error(
                "targeting_values_refresh_failed",
                f"Failed to refresh targeting values for key {key_id!r}",
                502,
                details={"tenant_id": tenant_id, "key_id": key_id, "error": str(exc)},
            )

    response = TargetingValuesRefreshResponse(key_id=key_id, synced=len(values))
    return jsonify(response.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Sprint 5 piece D — GAM advertisers cache lookup
# ---------------------------------------------------------------------------


_GAM_ADVERTISERS_DEFAULT_LIMIT = 50
_GAM_ADVERTISERS_MAX_LIMIT = 500
_GAM_DEFAULT_ADVERTISER_NAME = "Interchange - Default"


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


def _gam_advertiser_schema(row: GamAdvertiser) -> GamAdvertiserSchema:
    """Project a cached GAM advertiser row onto the wire schema."""
    return GamAdvertiserSchema(
        id=row.advertiser_id,
        name=row.name,
        currency_code=row.currency_code,
        status=row.status,
    )


def _gam_create_error_response(exc: Exception):
    """Map GAM advertiser creation failures to tenant-management API errors."""
    message = str(exc)
    code, remediation, gam_extra = _classify_gam_message(message)
    safe_message = (
        f"{gam_extra.get('service')}.{gam_extra.get('reason')}"
        if gam_extra.get("service") and gam_extra.get("reason")
        else type(exc).__name__
    )
    details = _vendor_fault(
        "gam",
        "create_advertiser",
        "CompanyService.createCompanies",
        message=safe_message,
        extra=gam_extra or None,
    )
    if remediation:
        details["remediation"] = remediation

    return _api_error(
        f"adapter_{code}",
        "GAM advertiser ensure failed.",
        403 if code == "permission_denied" else 400,
        details=details,
    )


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

    advertisers = [_gam_advertiser_schema(row) for row in rows]
    response = ListGamAdvertisersResponse(
        advertisers=advertisers,
        next_cursor=_encode_advertisers_cursor(offset + limit) if has_more else None,
        synced_at=synced_at,
    )
    return jsonify(response.model_dump(mode="json"))


@tenant_management_api.route("/tenants/<tenant_id>/gam/advertisers:ensure", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(
    json=EnsureGamAdvertiserRequest,
    resp=Response(
        HTTP_200=EnsureGamAdvertiserResponse,
        HTTP_201=EnsureGamAdvertiserResponse,
        HTTP_400=ApiError,
        HTTP_403=ApiError,
        HTTP_404=ApiError,
    ),
)
def ensure_gam_advertiser(tenant_id: str):
    """Idempotently ensure a GAM advertiser exists.

    This is the explicit write path Storefront can call when the buyer-routing
    picker cannot find the desired ``Interchange-*`` advertiser. A 201 response
    with ``created=true`` is also the permission proof that the configured GAM
    credential can create advertiser companies. A 200 response with
    ``created=false`` means an existing advertiser was found or attached; it
    does not prove create permission.
    """
    req: EnsureGamAdvertiserRequest = _validated_json_payload()
    name = req.name.strip()
    if not name:
        return _api_error("invalid_advertiser_name", "name must not be blank", 400)

    with get_db_session() as session:
        session.info["management_api_caller"] = True

        tenant = TenantConfigRepository(session, tenant_id).get_tenant()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        adapter = AdapterConfigRepository(session, tenant_id).find_by_tenant()
        if adapter is None or adapter.adapter_type != "google_ad_manager":
            return _api_error(
                "adapter_not_configured",
                "Tenant must have a Google Ad Manager adapter configured before ensuring GAM advertisers.",
                400,
            )
        if not adapter.gam_network_code:
            return _api_error("adapter_invalid_config", "GAM network_code is required", 400)

        gam_repo = GAMSyncRepository(session, tenant_id)
        cached = gam_repo.find_advertiser_by_name(name)
        if cached is not None and cached.status == "active":
            response = EnsureGamAdvertiserResponse(
                advertiser=_gam_advertiser_schema(cached),
                created=False,
                dry_run=False,
            )
            return jsonify(response.model_dump(mode="json"))

        try:
            result = gam_ensure_advertiser_companyservice(
                network_code=str(adapter.gam_network_code),
                config=_adapter_probe_config(adapter),
                name=name,
                dry_run=req.dry_run,
            )
        except Exception as exc:
            logger.warning(
                "GAM advertiser ensure failed for tenant_id=%s name=%r error_type=%s",
                tenant_id,
                name,
                type(exc).__name__,
            )
            return _gam_create_error_response(exc)

        if result.dry_run:
            response = EnsureGamAdvertiserResponse(
                advertiser=GamAdvertiserSchema(
                    id=result.advertiser_id,
                    name=result.name,
                    currency_code=None,
                    status="active",
                ),
                created=False,
                dry_run=True,
            )
            return jsonify(response.model_dump(mode="json"))

        row = gam_repo.upsert_advertiser(
            advertiser_id=result.advertiser_id,
            name=name,
            status="active",
            synced_at=datetime.now(UTC),
        )
        if result.created:
            adapter.gam_advertiser_create_permission_proven_at = datetime.now(UTC)
        try:
            session.commit()
        except EmbeddedTenantWriteError as exc:
            session.rollback()
            return _api_error("managed_tenant_write_blocked", str(exc), 403)
        except IntegrityError:
            session.rollback()
            existing = GAMSyncRepository(session, tenant_id).get_advertiser(result.advertiser_id)
            if existing is None:
                raise
            if result.created:
                refreshed_adapter = AdapterConfigRepository(session, tenant_id).find_by_tenant()
                if refreshed_adapter is not None:
                    refreshed_adapter.gam_advertiser_create_permission_proven_at = datetime.now(UTC)
                    session.commit()
            response = EnsureGamAdvertiserResponse(
                advertiser=_gam_advertiser_schema(existing),
                created=result.created,
                dry_run=result.dry_run,
            )
            if result.created:
                invalidate_status_cache(tenant_id)
            return jsonify(response.model_dump(mode="json")), 201 if result.created else 200
        session.refresh(row)

    response = EnsureGamAdvertiserResponse(
        advertiser=_gam_advertiser_schema(row),
        created=result.created,
        dry_run=result.dry_run,
    )
    if result.created:
        invalidate_status_cache(tenant_id)
    return jsonify(response.model_dump(mode="json")), 201 if result.created else 200


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
    req: ApproveWorkflowRequest = _validated_json_payload()
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
    req: RejectWorkflowRequest = _validated_json_payload()
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
    ``advertisers`` / ``reporting`` / ``signal_coverage`` /
    ``pricing_availability``), ``status``, ``limit`` (default 20, max 500),
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

    req: CreateWebhookSubscriptionRequest = _validated_json_payload()

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
        webhook_id: str = sub_webhook_id
        tenant_id: str = sub_tenant_id
        url: str = sub_url
        extra_headers: dict[str, Any] | None = sub_extra_headers

    for event_type in targets:
        envelope = build_envelope(
            event_type=event_type,
            tenant_id=tenant_id,
            data={"test": True, "subject_type": "tenant", "subject_id": tenant_id},
        )
        # _SubProxy is a duck-typed stand-in for WebhookSubscription that
        # carries just the fields deliver_event_sync reads. Real subscription
        # row would be overkill for a connectivity test.
        status_code, latency_ms, error = asyncio.run(deliver_event_sync(_SubProxy(), secret, envelope))
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
