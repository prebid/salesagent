"""REST API v1 endpoints.

First REST transport for AdCP tools, proving the 3-transport pattern
(MCP + A2A + REST) with get_products as the pilot.
"""

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from fastmcp.exceptions import ToolError
from pydantic import BaseModel

from src.core.auth import get_principal_from_token
from src.core.config_loader import set_current_tenant
from src.core.product_conversion import add_v2_compat_to_products, needs_v2_compat
from src.core.tools import products as products_module

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api-v1"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class GetProductsBody(BaseModel):
    """REST request body for POST /api/v1/products."""

    brief: str = ""
    brand_manifest: dict[str, Any] | None = None
    filters: dict[str, Any] | None = None
    adcp_version: str = "1.0.0"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/products")
async def get_products(body: GetProductsBody, request: Request):
    """Get available products matching the brief.

    Auth-optional: get_products is a discovery skill that works without
    authentication. When a token is present, products are scoped to the
    authenticated tenant/principal.
    """
    from src.core.auth_context import AuthContext

    # Read auth context populated by middleware
    auth_ctx: AuthContext = getattr(request.state, "auth_context", AuthContext.unauthenticated())

    # Resolve principal and tenant if token is present
    ctx = None
    if auth_ctx.auth_token:
        principal_id = get_principal_from_token(auth_ctx.auth_token)
        if principal_id:
            from datetime import UTC, datetime

            from src.core.tool_context import ToolContext

            ctx = ToolContext(
                context_id="rest-api",
                tenant_id=principal_id.split("_")[0] if "_admin" in principal_id else "default",
                principal_id=principal_id,
                tool_name="get_products",
                request_timestamp=datetime.now(UTC),
            )
            set_current_tenant({"tenant_id": ctx.tenant_id})

    # Build validated request
    req = products_module.create_get_products_request(
        brief=body.brief,
        brand_manifest=body.brand_manifest,
        filters=body.filters,
    )

    # Call shared implementation (accessed via module for testability)
    try:
        response = await products_module._get_products_impl(req, ctx)
    except ToolError as e:
        # Translate MCP-specific ToolError to HTTP error
        return JSONResponse(
            status_code=500,
            content={"error_code": "INTERNAL_ERROR", "message": str(e), "details": None},
        )

    # Serialize and apply version compat
    result = response.model_dump(mode="json")
    if needs_v2_compat(body.adcp_version) and "products" in result:
        result["products"] = add_v2_compat_to_products(result["products"])

    return result
