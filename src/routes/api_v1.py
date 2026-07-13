"""REST API v1 endpoints.

REST transport for AdCP tools, proving the 3-transport pattern
(MCP + A2A + REST). Each endpoint calls the shared _impl/_raw function
and applies version compat at the boundary.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.core.resolved_identity import ResolvedIdentity

from adcp.types import BrandReference
from adcp.types.generated_poc.media_buy.get_media_buy_delivery_request import (
    AttributionWindow,
    ReportingDimensions,
)
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from src.core.adcp_version import validate_adcp_version_pins
from src.core.auth_context import require_auth, resolve_auth
from src.core.exceptions import AdCPValidationError
from src.core.request_compat import ADCP_NEGOTIATION_FIELDS
from src.core.schema_helpers import coerce_creative_filters, to_account_reference
from src.core.tools import accounts as accounts_module
from src.core.tools import capabilities as capabilities_module
from src.core.tools import creative_formats as creative_formats_module
from src.core.tools import media_buy_create as media_buy_create_module
from src.core.tools import media_buy_delivery as media_buy_delivery_module
from src.core.tools import media_buy_update as media_buy_update_module
from src.core.tools import performance as performance_module
from src.core.tools import products as products_module
from src.core.tools import properties as properties_module
from src.core.tools.creatives import listing as creatives_listing_module
from src.core.tools.creatives import sync_wrappers as creatives_sync_module
from src.core.version_compat import apply_version_compat

logger = logging.getLogger(__name__)


def _coerce_rest_query_version_pins(query_params: Mapping[str, str]) -> dict[str, Any]:
    """Convert the textual REST query representation of the legacy integer pin.

    URL query parameters are strings even when their OpenAPI/schema type is an
    integer. Keep the core negotiator strict (MCP/A2A/JSON bodies must supply an
    actual int), and perform only this transport-required coercion at REST
    ingress. Non-integer spellings remain strings so the core emits the proper
    ``VALIDATION_ERROR`` instead of silently normalizing malformed input.
    """
    pins: dict[str, Any] = dict(query_params)
    raw_major = pins.get("adcp_major_version")
    if isinstance(raw_major, str):
        digits = raw_major[1:] if raw_major.startswith("-") else raw_major
        if digits and digits.isascii() and digits.isdigit():
            try:
                pins["adcp_major_version"] = int(raw_major)
            except ValueError:
                # Extremely long digit strings can exceed Python's protected
                # conversion limit. Leave them textual so validation rejects
                # them without an untyped exception.
                pass
    return pins


def _merge_rest_version_pins(
    query_pins: Mapping[str, Any],
    body: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one negotiation snapshot across REST query and JSON locations."""
    merged = {field: query_pins[field] for field in ADCP_NEGOTIATION_FIELDS if field in query_pins}
    context = body.get("context")
    if isinstance(context, dict):
        merged["context"] = context

    for field in ADCP_NEGOTIATION_FIELDS:
        if field not in body:
            continue
        body_value = body[field]
        if field in merged and merged[field] != body_value:
            raise AdCPValidationError(
                f"Conflicting {field} values were supplied in the REST query and JSON body.",
                field=field,
                details={
                    "query_value": merged[field],
                    "body_value": body_value,
                },
                suggestion=f"Send {field} in one location, or send the same value in both.",
                context=context if isinstance(context, dict) else None,
            )
        merged[field] = body_value
    return merged


async def _validate_version_pins(request: Request) -> None:
    """AdCP version negotiation on the REST boundary (parity with MCP/A2A).

    Checks the buyer's raw pin (``adcp_version`` / ``adcp_major_version``) in
    query params and, for body-carrying methods, the raw JSON body — BEFORE
    Pydantic parsing, so the *Body models' local ``adcp_version`` defaults
    (client-absent values) never trigger a rejection. Raises
    AdCPVersionUnsupportedError, rendered by the app-level AdCPError handler
    as the two-layer VERSION_UNSUPPORTED envelope. Starlette caches the body,
    so the endpoint's own body parsing is unaffected.
    """
    query_pins = _coerce_rest_query_version_pins(request.query_params)
    if request.method in ("POST", "PUT", "PATCH"):
        try:
            body = await request.json()
        except ValueError:
            validate_adcp_version_pins(query_pins)
            return  # malformed/empty JSON is reported by the endpoint's body parsing
        if isinstance(body, dict):
            validate_adcp_version_pins(_merge_rest_version_pins(query_pins, body))
            return
    validate_adcp_version_pins(query_pins)


async def _version_after_resolve(request: Request, _identity=resolve_auth) -> None:
    """Version negotiation AFTER auth resolution (discovery / auth-optional routes).

    ``resolve_auth`` is a sub-dependency here, so FastAPI resolves identity
    before the version check runs. Discovery routes never reject on auth, so the
    ordering is a no-op for them, but keeping the same shape as the auth-required
    variant makes the version gate uniform across every route. ``_identity`` is
    unused (the sub-dependency is the point) and intentionally unannotated so it
    does not depend on the TYPE_CHECKING-only ResolvedIdentity import.
    """
    await _validate_version_pins(request)


async def _version_after_require(request: Request, _identity=require_auth) -> None:
    """Version negotiation AFTER auth ENFORCEMENT (auth-required routes).

    AUTH before VERSION (#1546): ``require_auth`` is a sub-dependency, so an
    unauthenticated caller is rejected with AUTH_TOKEN_INVALID before the version
    check runs — you don't disclose ``supported_versions`` (a VERSION_UNSUPPORTED
    body) to a caller who hasn't authenticated. Parity with the MCP auth
    middleware and the A2A ``on_message_send`` auth gate, which enforce the same
    order at their boundaries.
    """
    await _validate_version_pins(request)


# Version negotiation now runs per-route AFTER auth (below) rather than as a
# blanket router dependency, which would have rejected a bad pin before auth.
router = APIRouter(prefix="/api/v1", tags=["api-v1"])


# Note: ToolError handling lives entirely in the global ``@app.exception_handler``
# in src/app.py — REST routes never catch ToolError or import the MCP-boundary
# type (AdCPToolError). The wire-code -> HTTP status table moved to
# src/core/tool_error_logging.py alongside handle_tool_error.


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
#
# FIXME(#1442): The *Body REST request models below inherit bare
# pydantic.BaseModel and so lack the Pattern #7 environment-based extra-field
# policy (extra="forbid" in dev/CI, extra="ignore" in prod). They parse buyer
# REST input and should extend SalesAgentBaseModel. Migrating them changes
# extra-field handling on the REST boundary, a behavioral change that needs
# REST integration coverage before flipping -- tracked as a follow-up. Until
# then they are allowlisted in tests/unit/test_architecture_no_bare_basemodel.py.


class GetProductsBody(BaseModel):  # FIXME(#1442): extend SalesAgentBaseModel (Pattern #7)
    brief: str = ""
    brand: dict[str, Any] | None = None  # adcp 3.6.0: BrandReference with domain field
    filters: dict[str, Any] | None = None
    adcp_version: str = "1.0.0"


class CreateMediaBuyBody(BaseModel):  # FIXME(#1442): extend SalesAgentBaseModel (Pattern #7)
    brand: BrandReference | str | None = None  # adcp 3.6.0: BrandReference with domain field
    packages: list[dict[str, Any]] = []  # Validated downstream by CreateMediaBuyRequest
    start_time: str | None = None
    end_time: str | None = None
    po_number: str | None = None
    account: dict[str, Any] | None = None  # AccountReference; coerced by CreateMediaBuyRequest
    idempotency_key: str | None = None
    adcp_version: str = "1.0.0"


class UpdateMediaBuyBody(BaseModel):  # FIXME(#1442): extend SalesAgentBaseModel (Pattern #7)
    paused: bool | None = None
    flight_start_date: str | None = None
    flight_end_date: str | None = None
    budget: float | None = None
    currency: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    adcp_version: str = "1.0.0"


class GetMediaBuyDeliveryBody(BaseModel):  # FIXME(#1442): extend SalesAgentBaseModel (Pattern #7)
    media_buy_ids: list[str] | None = None
    status_filter: Any = None
    start_date: str | None = None
    end_date: str | None = None
    reporting_dimensions: ReportingDimensions | None = None
    attribution_window: AttributionWindow | None = None
    include_package_daily_breakdown: bool | None = None
    account: dict[str, Any] | None = None
    adcp_version: str = "1.0.0"


class SyncCreativesBody(BaseModel):  # FIXME(#1442): extend SalesAgentBaseModel (Pattern #7)
    creatives: list[dict[str, Any]] = []
    assignments: dict[str, Any] | None = None
    creative_ids: list[str] | None = None
    delete_missing: bool = False
    dry_run: bool = False
    validation_mode: str = "strict"
    adcp_version: str = "1.0.0"


class ListCreativesBody(BaseModel):  # FIXME(#1442): extend SalesAgentBaseModel (Pattern #7)
    media_buy_id: str | None = None
    media_buy_ids: list[str] | None = None
    status: str | None = None
    format: str | None = None
    # Structured AdCP CreativeFilters object (statuses, concept_ids, format_ids,
    # tags, date ranges, …). Coerced to a typed CreativeFilters in the handler so
    # REST honours the same structured filters as MCP/A2A instead of dropping them.
    filters: dict[str, Any] | None = None
    adcp_version: str = "1.0.0"


class UpdatePerformanceIndexBody(BaseModel):  # FIXME(#1442): extend SalesAgentBaseModel (Pattern #7)
    media_buy_id: str
    performance_data: list[dict[str, Any]] = []
    adcp_version: str = "1.0.0"


class ListCreativeFormatsBody(BaseModel):  # FIXME(#1442): extend SalesAgentBaseModel (Pattern #7)
    format_ids: list[dict[str, Any]] | None = None
    name_search: str | None = None
    is_responsive: bool | None = None
    asset_types: list[str] | None = None
    min_width: int | None = None
    max_width: int | None = None
    min_height: int | None = None
    max_height: int | None = None
    wcag_level: str | None = None
    disclosure_positions: list[str] | None = None
    disclosure_persistence: list[str] | None = None
    output_format_ids: list[dict[str, Any]] | None = None
    input_format_ids: list[dict[str, Any]] | None = None
    adcp_version: str = "1.0.0"


class ListAuthorizedPropertiesBody(BaseModel):  # FIXME(#1442): extend SalesAgentBaseModel (Pattern #7)
    property_tags: list[str] | None = None
    publisher_domains: list[str] | None = None
    adcp_version: str = "1.0.0"


class ListAccountsBody(BaseModel):  # FIXME(#1442): extend SalesAgentBaseModel (Pattern #7)
    status: str | None = None
    sandbox: bool | None = None
    pagination: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    adcp_version: str = "1.0.0"


class SyncAccountsBody(BaseModel):  # FIXME(#1442): extend SalesAgentBaseModel (Pattern #7)
    accounts: list[dict[str, Any]] = []
    delete_missing: bool = False
    dry_run: bool = False
    push_notification_config: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    adcp_version: str = "1.0.0"


# ---------------------------------------------------------------------------
# Discovery endpoints (auth-optional)
# ---------------------------------------------------------------------------


@router.post("/products", dependencies=[Depends(_version_after_resolve)])
async def get_products(body: GetProductsBody, identity: ResolvedIdentity | None = resolve_auth):
    """Get available products matching the brief (auth-optional discovery skill).

    ``ToolError`` propagates to the global handler in ``src.app`` for envelope
    translation; no defensive catch needed here.
    """
    req = products_module.create_get_products_request(
        brief=body.brief,
        brand=body.brand,
        filters=body.filters,
    )
    response = await products_module._get_products_impl(req, identity)
    # Pass the MODEL, not a pre-dumped dict: apply_version_compat short-circuits
    # on a dict (the legacy pass-through) and would never derive the v2-compat
    # pricing fields (is_fixed / rate / price_guidance.floor) from the
    # pricing-option models. Dumping here made the "1.0.0" Body default a no-op,
    # so unpinned legacy clients silently got clean v3 responses (#1546 review).
    return apply_version_compat("get_products", response, body.adcp_version)


@router.get("/capabilities", dependencies=[Depends(_version_after_resolve)])
async def get_capabilities(identity: ResolvedIdentity | None = resolve_auth):
    """Get AdCP capabilities (auth-optional discovery skill)."""
    response = await capabilities_module.get_adcp_capabilities_raw(identity=identity)
    return response.model_dump(mode="json")


@router.post("/creative-formats", dependencies=[Depends(_version_after_resolve)])
async def list_creative_formats(body: ListCreativeFormatsBody, identity: ResolvedIdentity | None = resolve_auth):
    """List available creative formats (auth-optional discovery skill)."""
    from src.core.schemas import ListCreativeFormatsRequest

    body_fields = body.model_dump(exclude={"adcp_version"}, exclude_none=True)
    req = ListCreativeFormatsRequest(**body_fields) if body_fields else None

    response = creative_formats_module.list_creative_formats_raw(req=req, identity=identity)
    return response.model_dump(mode="json")


@router.post("/authorized-properties", dependencies=[Depends(_version_after_resolve)])
async def list_authorized_properties(
    body: ListAuthorizedPropertiesBody, identity: ResolvedIdentity | None = resolve_auth
):
    """List authorized properties (auth-optional discovery skill)."""
    from src.core.schemas import ListAuthorizedPropertiesRequest

    body_fields = body.model_dump(exclude={"adcp_version"}, exclude_none=True)
    req = ListAuthorizedPropertiesRequest(**body_fields) if body_fields else None

    response = properties_module.list_authorized_properties_raw(req=req, identity=identity)
    return response.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Auth-required endpoints
# ---------------------------------------------------------------------------


async def _raw_json_body(request: Request) -> dict[str, Any]:
    """The HTTP body as sent on the wire — the idempotency payload-hash input.

    A dependency rather than a route ``request`` parameter, so route signatures
    stay Depends-only (the rest-depends-auth guard). Prefers the pre-rewrite
    bytes stashed by ``RestCompatMiddleware`` — when a deprecated-field
    translation fires, ``request.json()`` would observe the NORMALIZED body,
    not the bytes the buyer sent, and seller-side compat-table changes would
    flip honest retries into conflicts mid-TTL. Starlette caches the body, so
    the fallback read does not consume it before model parsing.
    """
    raw = getattr(request.state, "raw_wire_payload", None)
    if raw is not None:
        return json.loads(raw)
    return await request.json()


# Module-level singleton, matching require_auth (ruff B008 forbids Depends() in defaults).
raw_json_body = Depends(_raw_json_body)


@router.post("/media-buys", dependencies=[Depends(_version_after_require)])
async def create_media_buy(
    body: CreateMediaBuyBody,
    identity: ResolvedIdentity = require_auth,
    raw_wire_payload: dict[str, Any] = raw_json_body,
):
    """Create a new media buy (auth required).

    Per AdCP 4.3 (commit 3c604130) per-package fields (budget, product_id,
    targeting_overlay, creatives, pacing, daily_budget) live inside packages[].
    """
    account_ref = to_account_reference(body.account)
    response = await media_buy_create_module.create_media_buy_raw(
        brand=body.brand,
        packages=body.packages,  # type: ignore[arg-type]  # REST sends raw dicts; coerced by CreateMediaBuyRequest
        start_time=body.start_time,
        end_time=body.end_time,
        po_number=body.po_number,
        account=account_ref,
        idempotency_key=body.idempotency_key,
        identity=identity,
        raw_wire_payload=raw_wire_payload,
    )
    return response.model_dump(mode="json")


@router.put("/media-buys/{media_buy_id}", dependencies=[Depends(_version_after_require)])
async def update_media_buy(media_buy_id: str, body: UpdateMediaBuyBody, identity: ResolvedIdentity = require_auth):
    """Update an existing media buy (auth required)."""
    response = media_buy_update_module.update_media_buy_raw(
        media_buy_id=media_buy_id,
        paused=body.paused,
        flight_start_date=body.flight_start_date,
        flight_end_date=body.flight_end_date,
        budget=body.budget,
        currency=body.currency,
        start_time=body.start_time,
        end_time=body.end_time,
        identity=identity,
    )
    return response.model_dump(mode="json")


@router.post("/media-buys/delivery", dependencies=[Depends(_version_after_require)])
async def get_media_buy_delivery(body: GetMediaBuyDeliveryBody, identity: ResolvedIdentity = require_auth):
    """Get delivery metrics for media buys (auth required)."""
    if body.account is not None:
        from src.core.transport_helpers import enrich_identity_with_account

        account_ref = to_account_reference(body.account)
        enriched = enrich_identity_with_account(identity, account_ref)
        assert enriched is not None  # identity is non-None (from require_auth)
        identity = enriched

    response = media_buy_delivery_module.get_media_buy_delivery_raw(
        media_buy_ids=body.media_buy_ids,
        status_filter=body.status_filter,
        start_date=body.start_date,
        end_date=body.end_date,
        reporting_dimensions=body.reporting_dimensions,
        attribution_window=body.attribution_window,
        include_package_daily_breakdown=body.include_package_daily_breakdown,
        identity=identity,
    )
    return response.model_dump(mode="json")


@router.post("/creatives/sync", dependencies=[Depends(_version_after_require)])
async def sync_creatives(body: SyncCreativesBody, identity: ResolvedIdentity = require_auth):
    """Sync creatives (auth required)."""
    response = creatives_sync_module.sync_creatives_raw(
        creatives=body.creatives,  # type: ignore[arg-type]  # REST accepts dicts, _impl handles both
        assignments=body.assignments,
        creative_ids=body.creative_ids,
        delete_missing=body.delete_missing,
        dry_run=body.dry_run,
        validation_mode=body.validation_mode,
        identity=identity,
    )
    return response.model_dump(mode="json")


@router.post("/creatives", dependencies=[Depends(_version_after_require)])
async def list_creatives(body: ListCreativesBody, identity: ResolvedIdentity = require_auth):
    """List creatives (auth required)."""
    filters = coerce_creative_filters(body.filters)
    response = creatives_listing_module.list_creatives_raw(
        media_buy_id=body.media_buy_id,
        media_buy_ids=body.media_buy_ids,
        status=body.status,
        format=body.format,
        filters=filters,
        identity=identity,
    )
    return response.model_dump(mode="json")


@router.post("/performance-index", dependencies=[Depends(_version_after_require)])
async def update_performance_index(body: UpdatePerformanceIndexBody, identity: ResolvedIdentity = require_auth):
    """Update performance index for a media buy (auth required)."""
    response = performance_module.update_performance_index_raw(
        media_buy_id=body.media_buy_id,
        performance_data=body.performance_data,
        identity=identity,
    )
    return response.model_dump(mode="json")


@router.post("/accounts", dependencies=[Depends(_version_after_require)])
async def list_accounts(body: ListAccountsBody, identity: ResolvedIdentity = require_auth):
    """List accounts accessible to the authenticated agent (auth required)."""
    from src.core.schemas.account import ListAccountsRequest

    req = ListAccountsRequest(**body.model_dump(exclude_none=True, exclude={"adcp_version"}))
    response = accounts_module.list_accounts_raw(req=req, identity=identity)
    return response.model_dump(mode="json")


@router.post("/accounts/sync", dependencies=[Depends(_version_after_require)])
async def sync_accounts(body: SyncAccountsBody, identity: ResolvedIdentity = require_auth):
    """Sync accounts by natural key (auth required)."""
    from src.core.schemas.account import SyncAccountsRequest

    req = SyncAccountsRequest(**body.model_dump(exclude_none=True, exclude={"adcp_version"}))
    response = await accounts_module.sync_accounts_raw(req=req, identity=identity)
    return response.model_dump(mode="json")
