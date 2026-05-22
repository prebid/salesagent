"""REST API v1 endpoints.

REST transport for AdCP tools, proving the 3-transport pattern
(MCP + A2A + REST). Each endpoint calls the shared _impl/_raw function
and applies version compat at the boundary.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.core.resolved_identity import ResolvedIdentity

from adcp.types.generated_poc.core.brand_ref import BrandReference
from adcp.types.generated_poc.media_buy.get_media_buy_delivery_request import (
    AttributionWindow,
    ReportingDimensions,
)
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from fastmcp.exceptions import ToolError
from pydantic import BaseModel

from src.core.auth_context import require_auth, resolve_auth
from src.core.exceptions import AdCPError, build_two_layer_error_envelope
from src.core.tool_error_logging import AdCPToolError, extract_error_info
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

router = APIRouter(prefix="/api/v1", tags=["api-v1"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_error_code_to_status() -> dict[str, int]:
    """Derive the wire-code → HTTP status map from ``AdCPError`` subclasses.

    Walks every concrete subclass of ``AdCPError`` and reads its
    class-level ``error_code`` + ``status_code`` declarations, then
    propagates each declaration to its wire-translated equivalents via
    ``ERROR_CODE_MAPPING``. Eliminates the drift potential of a
    hand-maintained table — previously the table declared
    ``AUTH_REQUIRED → 401`` while ``AdCPAuthorizationError`` (same wire
    code) carried ``status_code = 403``; a plain-ToolError raise from
    authorization code surfaced as 401 instead of 403. Same pattern for
    ``SERVICE_UNAVAILABLE`` (table said 503, adapter class said 502).
    The class attribute is the source of truth.

    When a wire code is shared by multiple subclasses (e.g.,
    ``AUTH_REQUIRED`` from both ``AdCPAuthenticationError`` 401 and
    ``AdCPAuthorizationError`` 403), the **highest** status code wins —
    the more restrictive one is the spec-aligned answer when the table
    is used for a plain-ToolError fallback that has no carried context.
    """
    from src.core.exceptions import ERROR_CODE_MAPPING

    # INVALID_REQUEST is AdCP's "generic 4xx bucket" wire code that does not
    # correspond to any specific typed subclass — it's the translation target
    # for several upstream codes. Anchor it to HTTP 400 (the conventional
    # bad-request status) so propagation from differently-statused upstream
    # codes (e.g., NOT_FOUND=404 -> INVALID_REQUEST) doesn't accidentally
    # promote it to 404.
    table: dict[str, int] = {"INVALID_REQUEST": 400}
    _GENERIC_CATCHALLS = {"INVALID_REQUEST"}

    stack = list(AdCPError.__subclasses__())
    while stack:
        cls = stack.pop()
        stack.extend(cls.__subclasses__())
        code = getattr(cls, "error_code", None)
        status = getattr(cls, "status_code", None)
        if not code or not status:
            continue
        # Index the raw class code so plain-ToolError("CODE") fallbacks resolve.
        existing = table.get(code)
        if existing is None or status > existing:
            table[code] = status
        # Also index the wire-translated code so the same status applies after
        # ``translate_error_code()`` rewrites it at the boundary. Skip generic
        # catchall targets like INVALID_REQUEST — they have a fixed status
        # independent of which specific upstream code triggered them.
        wire_code = ERROR_CODE_MAPPING.get(code)
        if wire_code and wire_code not in _GENERIC_CATCHALLS:
            existing_wire = table.get(wire_code)
            if existing_wire is None or status > existing_wire:
                table[wire_code] = status
    return table


# Plain ``ToolError("CODE", "message")`` legacy paths don't carry the typed
# AdCPError that owns ``status_code``. Derived from class declarations at
# import time so the table cannot drift from the source of truth.
_ERROR_CODE_TO_STATUS: dict[str, int] = _build_error_code_to_status()


def _handle_tool_error(e: ToolError) -> JSONResponse:
    """Convert MCP ToolError to the spec-compliant two-layer envelope body.

    Routes that catch ``ToolError`` defensively land here. If the exception
    is the typed ``AdCPToolError`` raised by the MCP boundary translator, its
    envelope and status_code are forwarded unchanged so 4xx errors don't get
    mislabeled as 5xx. Plain ``ToolError`` (raised by other paths) is rebuilt
    into an envelope via a synthetic ``AdCPError``; its HTTP status is
    resolved from ``_ERROR_CODE_TO_STATUS`` for known wire codes and falls
    through to 500 only when the code is unrecognized.
    """
    if isinstance(e, AdCPToolError):
        return JSONResponse(status_code=e.status_code, content=e.envelope)

    error_code, error_message, recovery = extract_error_info(e)
    synthetic = AdCPError(error_message)
    synthetic.error_code = error_code
    synthetic.status_code = _ERROR_CODE_TO_STATUS.get(error_code, 500)
    if recovery is not None:
        synthetic.recovery = recovery
    return JSONResponse(status_code=synthetic.status_code, content=build_two_layer_error_envelope(synthetic))


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class GetProductsBody(BaseModel):
    brief: str = ""
    brand: dict[str, Any] | None = None  # adcp 3.6.0: BrandReference with domain field
    filters: dict[str, Any] | None = None
    adcp_version: str = "1.0.0"


class CreateMediaBuyBody(BaseModel):
    brand: BrandReference | str | None = None  # adcp 3.6.0: BrandReference with domain field
    packages: list[dict[str, Any]] = []  # Validated downstream by CreateMediaBuyRequest
    start_time: str | None = None
    end_time: str | None = None
    po_number: str | None = None
    adcp_version: str = "1.0.0"


class UpdateMediaBuyBody(BaseModel):
    paused: bool | None = None
    flight_start_date: str | None = None
    flight_end_date: str | None = None
    budget: float | None = None
    currency: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    adcp_version: str = "1.0.0"


class GetMediaBuyDeliveryBody(BaseModel):
    media_buy_ids: list[str] | None = None
    status_filter: Any = None
    start_date: str | None = None
    end_date: str | None = None
    reporting_dimensions: ReportingDimensions | None = None
    attribution_window: AttributionWindow | None = None
    include_package_daily_breakdown: bool | None = None
    account: dict[str, Any] | None = None
    adcp_version: str = "1.0.0"


class SyncCreativesBody(BaseModel):
    creatives: list[dict[str, Any]] = []
    assignments: dict[str, Any] | None = None
    creative_ids: list[str] | None = None
    delete_missing: bool = False
    dry_run: bool = False
    validation_mode: str = "strict"
    adcp_version: str = "1.0.0"


class ListCreativesBody(BaseModel):
    media_buy_id: str | None = None
    media_buy_ids: list[str] | None = None
    status: str | None = None
    format: str | None = None
    adcp_version: str = "1.0.0"


class UpdatePerformanceIndexBody(BaseModel):
    media_buy_id: str
    performance_data: list[dict[str, Any]] = []
    adcp_version: str = "1.0.0"


class ListCreativeFormatsBody(BaseModel):
    adcp_version: str = "1.0.0"


class ListAuthorizedPropertiesBody(BaseModel):
    adcp_version: str = "1.0.0"


class ListAccountsBody(BaseModel):
    status: str | None = None
    sandbox: bool | None = None
    pagination: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    adcp_version: str = "1.0.0"


class SyncAccountsBody(BaseModel):
    accounts: list[dict[str, Any]] = []
    delete_missing: bool = False
    dry_run: bool = False
    push_notification_config: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    adcp_version: str = "1.0.0"


# ---------------------------------------------------------------------------
# Discovery endpoints (auth-optional)
# ---------------------------------------------------------------------------


@router.post("/products")
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
    result = response.model_dump(mode="json")
    return apply_version_compat("get_products", result, body.adcp_version)


@router.get("/capabilities")
async def get_capabilities(identity: ResolvedIdentity | None = resolve_auth):
    """Get AdCP capabilities (auth-optional discovery skill)."""
    response = await capabilities_module.get_adcp_capabilities_raw(identity=identity)
    return response.model_dump(mode="json")


@router.post("/creative-formats")
async def list_creative_formats(body: ListCreativeFormatsBody, identity: ResolvedIdentity | None = resolve_auth):
    """List available creative formats (auth-optional discovery skill)."""
    response = creative_formats_module.list_creative_formats_raw(identity=identity)
    return response.model_dump(mode="json")


@router.post("/authorized-properties")
async def list_authorized_properties(
    body: ListAuthorizedPropertiesBody, identity: ResolvedIdentity | None = resolve_auth
):
    """List authorized properties (auth-optional discovery skill)."""
    response = properties_module.list_authorized_properties_raw(identity=identity)
    return response.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Auth-required endpoints
# ---------------------------------------------------------------------------


@router.post("/media-buys")
async def create_media_buy(body: CreateMediaBuyBody, identity: ResolvedIdentity = require_auth):
    """Create a new media buy (auth required).

    Per AdCP 4.3 (commit 3c604130) per-package fields (budget, product_id,
    targeting_overlay, creatives, pacing, daily_budget) live inside packages[].
    """
    response = await media_buy_create_module.create_media_buy_raw(
        brand=body.brand,
        packages=body.packages,  # type: ignore[arg-type]  # REST sends raw dicts; coerced by CreateMediaBuyRequest
        start_time=body.start_time,
        end_time=body.end_time,
        po_number=body.po_number,
        identity=identity,
    )
    return response.model_dump(mode="json")


@router.put("/media-buys/{media_buy_id}")
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


@router.post("/media-buys/delivery")
async def get_media_buy_delivery(body: GetMediaBuyDeliveryBody, identity: ResolvedIdentity = require_auth):
    """Get delivery metrics for media buys (auth required)."""
    if body.account is not None:
        from adcp.types import AccountReference as LibraryAccountReference

        from src.core.transport_helpers import enrich_identity_with_account

        account_ref = LibraryAccountReference(**body.account)
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


@router.post("/creatives/sync")
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


@router.post("/creatives")
async def list_creatives(body: ListCreativesBody, identity: ResolvedIdentity = require_auth):
    """List creatives (auth required)."""
    response = creatives_listing_module.list_creatives_raw(
        media_buy_id=body.media_buy_id,
        media_buy_ids=body.media_buy_ids,
        status=body.status,
        format=body.format,
        identity=identity,
    )
    return response.model_dump(mode="json")


@router.post("/performance-index")
async def update_performance_index(body: UpdatePerformanceIndexBody, identity: ResolvedIdentity = require_auth):
    """Update performance index for a media buy (auth required)."""
    response = performance_module.update_performance_index_raw(
        media_buy_id=body.media_buy_id,
        performance_data=body.performance_data,
        identity=identity,
    )
    return response.model_dump(mode="json")


@router.post("/accounts")
async def list_accounts(body: ListAccountsBody, identity: ResolvedIdentity = require_auth):
    """List accounts accessible to the authenticated agent (auth required)."""
    from src.core.schemas.account import ListAccountsRequest

    req = ListAccountsRequest(**body.model_dump(exclude_none=True, exclude={"adcp_version"}))
    response = accounts_module.list_accounts_raw(req=req, identity=identity)
    return response.model_dump(mode="json")


@router.post("/accounts/sync")
async def sync_accounts(body: SyncAccountsBody, identity: ResolvedIdentity = require_auth):
    """Sync accounts by natural key (auth required)."""
    from src.core.schemas.account import SyncAccountsRequest

    req = SyncAccountsRequest(**body.model_dump(exclude_none=True, exclude={"adcp_version"}))
    response = await accounts_module.sync_accounts_raw(req=req, identity=identity)
    return response.model_dump(mode="json")
