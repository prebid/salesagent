"""Health and debug endpoints.

Extracted from src/core/main.py @mcp.custom_route handlers into
standard FastAPI routes so they are served by the unified FastAPI app.
"""

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Product as ModelProduct
from src.core.database.models import Tenant as ModelTenant
from src.core.database.repositories.principal import PrincipalRepository
from src.core.domain_routing import RoutingResult, route_landing_page
from src.landing import generate_tenant_landing_page

logger = logging.getLogger(__name__)

router = APIRouter()


def _routed_page(headers: dict) -> tuple[RoutingResult, str | None, str | None]:
    """What this request routes to, and the landing page that comes of it.

    The routing decision comes from ``route_landing_page`` — the one answer to "which tenant
    is at this host", which the root route acts on — so a debug report here cannot name a
    detection the deployment does not have. These endpoints report rather than serve, so a
    page that fails to render is part of the report and not an error response.

    Returns the decision, the rendered page when one rendered, and the failure when it did not.
    """
    result = route_landing_page(headers)
    if result.tenant is None:
        return result, None, None
    try:
        return result, generate_tenant_landing_page(result.tenant), None
    except Exception as e:  # reported, not raised: a debug endpoint answers either way
        return result, None, str(e)


# The routes on this router exist only where the deployment allows them: ``src/app.py``
# includes it when ``get_settings().debug_routes_enabled`` says so, and nowhere else does it
# exist at all. No per-request check: a route that is not mounted cannot be reached.
debug_router = APIRouter()


@router.get("/health")
async def health(request: Request):
    """Health check endpoint."""
    return JSONResponse({"status": "healthy", "service": "mcp"})


@debug_router.post("/_internal/reset-db-pool")
async def reset_db_pool(request: Request):
    """Reset database connection pool after external data changes.

    A testing-only endpoint that flushes the SQLAlchemy connection pool, so fresh
    connections see recently committed data. On the debug router, so it exists only where
    the deployment mounts that router.
    """
    try:
        from src.core.database.database_session import reset_engine

        logger.info("Resetting database connection pool and tenant context (testing mode)")

        reset_engine()
        logger.info("  ✓ Database connection pool reset")

        return JSONResponse(
            {
                "status": "success",
                "message": "Database connection pool and tenant context reset successfully",
            }
        )
    except Exception as e:
        logger.error(f"Failed to reset database state: {e}")
        return JSONResponse({"error": f"Failed to reset: {str(e)}"}, status_code=500)


@debug_router.get("/debug/db-state")
async def debug_db_state(request: Request):
    """Debug endpoint to show database state (testing only)."""
    try:
        with get_db_session() as session:
            product_stmt = select(ModelProduct)
            all_products = session.scalars(product_stmt).all()

            # The CI seed, by its stable slug, then the principal inside it. No token is
            # turned into a principal here; the seed tenant holds exactly one principal,
            # and this route only reports whether the seed exists.
            #
            # A direct read of a KNOWN row's id -- this is a debug report about the CI seed,
            # not a request identifying its seller. The id is the seed's stable spelling
            # (scripts/setup/init_database_ci.py CI_TEST_TENANT_ID); src/ does not import
            # from scripts/, so the literal is repeated rather than shared.
            seed_tenant_id = session.scalars(
                select(ModelTenant.tenant_id).filter_by(tenant_id="ci-test", is_active=True)
            ).first()
            principal = (
                next(iter(PrincipalRepository(session, seed_tenant_id).list_all()), None) if seed_tenant_id else None
            )

            principal_info = None
            tenant_info = None
            tenant_products: list[ModelProduct] = []

            if principal:
                principal_info = {
                    "principal_id": principal.principal_id,
                    "tenant_id": principal.tenant_id,
                }

                tenant_stmt = select(ModelTenant).filter_by(tenant_id=principal.tenant_id)
                tenant = session.scalars(tenant_stmt).first()
                if tenant:
                    tenant_info = {
                        "tenant_id": tenant.tenant_id,
                        "name": tenant.name,
                        "is_active": tenant.is_active,
                    }

                tenant_product_stmt = select(ModelProduct).filter_by(tenant_id=principal.tenant_id)
                tenant_products = list(session.scalars(tenant_product_stmt).all())

            return JSONResponse(
                {
                    "total_products": len(all_products),
                    "principal": principal_info,
                    "tenant": tenant_info,
                    "tenant_products_count": len(tenant_products),
                    "tenant_product_ids": [p.product_id for p in tenant_products],
                }
            )
    except Exception as e:
        logger.error(f"Debug endpoint error: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@debug_router.get("/debug/tenant")
async def debug_tenant(request: Request):
    """Debug endpoint to check tenant detection from headers."""
    # The routing decision the app acts on, not a second lookup: a debug endpoint claiming a
    # detection production does not have is worse than no endpoint, and the admin domain is
    # where an own lookup diverges — the app sends it to admin login and never seeks a tenant.
    result = route_landing_page(dict(request.headers))
    tenant_id = result.tenant.get("tenant_id") if result.tenant else None

    response_data = {
        "tenant_id": tenant_id,
        "tenant_name": result.tenant.get("name") if result.tenant else None,
        "detection_method": "host" if result.tenant else None,
        "host": result.effective_host,
    }

    response = JSONResponse(response_data)
    if tenant_id:
        response.headers["X-Tenant-Id"] = tenant_id

    return response


@debug_router.get("/debug/root")
async def debug_root(request: Request):
    """Debug endpoint to test root route logic without redirects."""
    headers = dict(request.headers)
    result, html_content, render_error = _routed_page(headers)

    # ``all_headers`` still carries whatever arrived, so an operator debugging a proxy can
    # see every header verbatim; what is gone is this route naming one of them as a tenant
    # input of its own.
    debug_info: dict[str, Any] = {
        "all_headers": headers,
        "virtual_host": result.effective_host,
        "tenant_found": result.tenant is not None,
        "tenant_id": result.tenant.get("tenant_id") if result.tenant else None,
        "tenant_name": result.tenant.get("name") if result.tenant else None,
    }

    if result.tenant is not None:
        debug_info["landing_page_generated"] = html_content is not None
        if html_content is not None:
            debug_info["landing_page_length"] = len(html_content)
        else:
            debug_info["landing_page_error"] = render_error

    return JSONResponse(debug_info)


@debug_router.get("/debug/landing")
async def debug_landing(request: Request):
    """Debug endpoint to test landing page generation directly."""
    _, html_content, render_error = _routed_page(dict(request.headers))

    if html_content is not None:
        return HTMLResponse(content=html_content)
    if render_error is not None:
        return JSONResponse({"error": f"Landing page generation failed: {render_error}"}, status_code=500)
    return JSONResponse({"error": "No tenant found"}, status_code=404)


@debug_router.get("/debug/root-logic")
async def debug_root_logic(request: Request):
    """Debug endpoint that reports what the root route does with this request.

    The same routing call the root route makes, so the report and the behaviour cannot drift.
    """
    result, html_content, render_error = _routed_page(dict(request.headers))

    debug_info: dict[str, Any] = {
        "routing_type": result.type,
        "virtual_host": result.effective_host,
        "exact_tenant_lookup": result.tenant is not None,
    }

    if result.type == "admin":
        debug_info["step"] = "admin_domain"
        debug_info["would_return"] = "redirect to /admin/login"
    elif not result.effective_host:
        debug_info["step"] = "no_virtual_host"
        debug_info["would_return"] = "fallback HTMLResponse"
    elif result.tenant is None:
        debug_info["step"] = "no_tenant_found"
        debug_info["would_return"] = "fallback HTMLResponse"
    elif html_content is not None:
        debug_info["step"] = "landing_page_success"
        debug_info["tenant_id"] = result.tenant.get("tenant_id")
        debug_info["tenant_name"] = result.tenant.get("name")
        debug_info["landing_page_length"] = len(html_content)
        debug_info["would_return"] = "HTMLResponse"
    else:
        debug_info["step"] = "landing_page_error"
        debug_info["tenant_id"] = result.tenant.get("tenant_id")
        debug_info["error"] = render_error
        debug_info["would_return"] = "fallback HTMLResponse"

    return JSONResponse(debug_info)


@router.get("/health/config")
async def health_config(request: Request):
    """Configuration health check endpoint."""
    try:
        from src.core.startup import validate_startup_requirements

        validate_startup_requirements()
        return JSONResponse(
            {
                "status": "healthy",
                "service": "mcp",
                "component": "configuration",
                "message": "All configuration validation passed",
            }
        )
    except Exception as e:
        return JSONResponse(
            {"status": "unhealthy", "service": "mcp", "component": "configuration", "error": str(e)}, status_code=500
        )
