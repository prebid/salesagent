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

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from fastmcp.exceptions import ToolError
from pydantic import BaseModel

from src.core.auth_context import require_auth, resolve_auth
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


def _handle_tool_error(e: ToolError) -> JSONResponse:
    """Convert MCP ToolError to HTTP error response."""
    from src.core.tool_error_logging import extract_error_info

    error_code, error_message, recovery = extract_error_info(e)
    return JSONResponse(
        status_code=500,
        content={
            "error_code": error_code,
            "message": error_message,
            "recovery": recovery,
            "details": None,
        },
    )


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class GetProductsBody(BaseModel):
    brief: str = ""
    brand: dict[str, Any] | None = None  # adcp 3.6.0: BrandReference with domain field
    filters: dict[str, Any] | None = None
    adcp_version: str = "1.0.0"


class CreateMediaBuyBody(BaseModel):
    buyer_ref: str
    brand: dict[str, Any] | None = None  # adcp 3.6.0: BrandReference with domain field
    packages: list[dict[str, Any]] = []
    start_time: str | None = None
    end_time: str | None = None
    budget: Any | None = None
    po_number: str | None = None
    product_ids: list[str] | None = None
    total_budget: float | None = None
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
    buyer_refs: list[str] | None = None
    start_date: str | None = None
    end_date: str | None = None
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
    buyer_ref: str | None = None
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


# ---------------------------------------------------------------------------
# Discovery endpoints (auth-optional)
# ---------------------------------------------------------------------------


@router.post("/products")
async def get_products(body: GetProductsBody, identity: ResolvedIdentity | None = resolve_auth):
    """Get available products matching the brief (auth-optional discovery skill)."""
    req = products_module.create_get_products_request(
        brief=body.brief,
        brand=body.brand,
        filters=body.filters,
    )

    try:
        response = await products_module._get_products_impl(req, identity)
    except ToolError as e:
        return _handle_tool_error(e)

    result = response.model_dump(mode="json")
    return apply_version_compat("get_products", result, body.adcp_version)


@router.get("/capabilities")
async def get_capabilities(identity: ResolvedIdentity | None = resolve_auth):
    """Get AdCP capabilities (auth-optional discovery skill)."""

    try:
        response = await capabilities_module.get_adcp_capabilities_raw(identity=identity)
    except ToolError as e:
        return _handle_tool_error(e)

    return response.model_dump(mode="json")


@router.post("/creative-formats")
async def list_creative_formats(body: ListCreativeFormatsBody, identity: ResolvedIdentity | None = resolve_auth):
    """List available creative formats (auth-optional discovery skill)."""

    try:
        response = creative_formats_module.list_creative_formats_raw(identity=identity)
    except ToolError as e:
        return _handle_tool_error(e)

    return response.model_dump(mode="json")


@router.post("/authorized-properties")
async def list_authorized_properties(
    body: ListAuthorizedPropertiesBody, identity: ResolvedIdentity | None = resolve_auth
):
    """List authorized properties (auth-optional discovery skill)."""

    try:
        response = properties_module.list_authorized_properties_raw(identity=identity)
    except ToolError as e:
        return _handle_tool_error(e)

    return response.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Auth-required endpoints
# ---------------------------------------------------------------------------


@router.post("/media-buys")
async def create_media_buy(body: CreateMediaBuyBody, identity: ResolvedIdentity = require_auth):
    """Create a new media buy (auth required)."""
    try:
        response = await media_buy_create_module.create_media_buy_raw(
            buyer_ref=body.buyer_ref,
            brand=body.brand,
            packages=body.packages,
            start_time=body.start_time,
            end_time=body.end_time,
            budget=body.budget,
            po_number=body.po_number,
            product_ids=body.product_ids,
            total_budget=body.total_budget,
            identity=identity,
        )
    except ToolError as e:
        return _handle_tool_error(e)

    return response.model_dump(mode="json")


@router.put("/media-buys/{media_buy_id}")
async def update_media_buy(media_buy_id: str, body: UpdateMediaBuyBody, identity: ResolvedIdentity = require_auth):
    """Update an existing media buy (auth required)."""
    try:
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
    except ToolError as e:
        return _handle_tool_error(e)

    return response.model_dump(mode="json")


@router.post("/media-buys/delivery")
async def get_media_buy_delivery(body: GetMediaBuyDeliveryBody, identity: ResolvedIdentity = require_auth):
    """Get delivery metrics for media buys (auth required)."""
    try:
        response = media_buy_delivery_module.get_media_buy_delivery_raw(
            media_buy_ids=body.media_buy_ids,
            buyer_refs=body.buyer_refs,
            start_date=body.start_date,
            end_date=body.end_date,
            identity=identity,
        )
    except ToolError as e:
        return _handle_tool_error(e)

    return response.model_dump(mode="json")


@router.post("/creatives/sync")
async def sync_creatives(body: SyncCreativesBody, identity: ResolvedIdentity = require_auth):
    """Sync creatives (auth required)."""
    try:
        response = creatives_sync_module.sync_creatives_raw(
            creatives=body.creatives,  # type: ignore[arg-type]  # REST accepts dicts, _impl handles both
            assignments=body.assignments,
            creative_ids=body.creative_ids,
            delete_missing=body.delete_missing,
            dry_run=body.dry_run,
            validation_mode=body.validation_mode,
            identity=identity,
        )
    except ToolError as e:
        return _handle_tool_error(e)

    return response.model_dump(mode="json")


@router.post("/creatives")
async def list_creatives(body: ListCreativesBody, identity: ResolvedIdentity = require_auth):
    """List creatives (auth required)."""
    try:
        response = creatives_listing_module.list_creatives_raw(
            media_buy_id=body.media_buy_id,
            media_buy_ids=body.media_buy_ids,
            buyer_ref=body.buyer_ref,
            status=body.status,
            format=body.format,
            identity=identity,
        )
    except ToolError as e:
        return _handle_tool_error(e)

    return response.model_dump(mode="json")


@router.post("/performance-index")
async def update_performance_index(body: UpdatePerformanceIndexBody, identity: ResolvedIdentity = require_auth):
    """Update performance index for a media buy (auth required)."""
    try:
        response = performance_module.update_performance_index_raw(
            media_buy_id=body.media_buy_id,
            performance_data=body.performance_data,
            identity=identity,
        )
    except ToolError as e:
        return _handle_tool_error(e)

    return response.model_dump(mode="json")
