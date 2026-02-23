import logging
import os
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from rich.console import Console
from sqlalchemy import select

from src.adapters.mock_creative_engine import MockCreativeEngine
from src.core.auth import (
    get_principal_from_context,
)
from src.landing import generate_tenant_landing_page

logger = logging.getLogger(__name__)

# Database models

# Other imports
from src.core.config_loader import (
    get_current_tenant,
    get_tenant_by_virtual_host,
    load_config,
    set_current_tenant,
)
from src.core.database.database import init_db
from src.core.database.database_session import get_db_session
from src.core.database.models import Principal as ModelPrincipal
from src.core.database.models import Product as ModelProduct
from src.core.database.models import (
    Tenant,
    WorkflowStep,
)
from src.core.domain_config import (
    extract_subdomain_from_host,
    is_sales_agent_domain,
)

# Schema models (explicit imports to avoid collisions)
# Schema adapters (wrapping generated schemas)
from src.core.schemas import (
    CreateMediaBuyRequest,
    Creative,
    CreativeAssignment,
    CreativeGroup,
    CreativeStatus,
    Error,  # noqa: F401 - Required for MCP protocol error handling (regression test PR #332)
    Product,
)

# Initialize Rich console
console = Console()

# Backward compatibility alias for deprecated Task model
# The workflow system now uses WorkflowStep exclusively
Task = WorkflowStep

# Temporary placeholder classes for missing schemas
# TODO: These should be properly defined in schemas.py
from pydantic import BaseModel


class ApproveAdaptationRequest(BaseModel):
    creative_id: str
    adaptation_id: str
    approve: bool = True
    modifications: dict[str, Any] | None = None


class ApproveAdaptationResponse(BaseModel):
    success: bool
    message: str


# --- Helper Functions ---


# --- Helper Functions ---
# Helper functions moved to src/core/helpers/ modules and imported above

# --- Authentication ---
# Auth functions moved to src/core/auth.py and imported above


# --- Initialization ---
# NOTE: Database initialization moved to startup script to avoid import-time failures
# The run_all_services.py script handles database initialization before starting the MCP server

# Try to load config, but use defaults if no tenant context available
try:
    config = load_config()
except (RuntimeError, Exception) as e:
    # Use minimal config for test environments or when DB is unavailable
    # This handles both "No tenant context set" and database connection errors
    if "No tenant context" in str(e) or "connection" in str(e).lower() or "operational" in str(e).lower():
        config = {
            "creative_engine": {},
            "dry_run": False,
            "adapters": {"mock": {"enabled": True}},
            "ad_server": {"adapter": "mock", "enabled": True},
        }
    else:
        raise

from contextlib import asynccontextmanager


# Lifespan context manager for FastMCP startup/shutdown
@asynccontextmanager
async def lifespan_context(app):
    """Handle application startup and shutdown."""
    # Startup: Initialize delivery webhook scheduler
    from src.services.delivery_webhook_scheduler import start_delivery_webhook_scheduler

    logger.info("Starting delivery webhook scheduler...")
    try:
        await start_delivery_webhook_scheduler()
        logger.info("✅ Delivery webhook scheduler started")
    except Exception as e:
        logger.error(f"Failed to start delivery webhook scheduler: {e}", exc_info=True)

    # Startup: Initialize media buy status scheduler
    from src.services.media_buy_status_scheduler import start_media_buy_status_scheduler

    logger.info("Starting media buy status scheduler...")
    try:
        await start_media_buy_status_scheduler()
        logger.info("✅ Media buy status scheduler started")
    except Exception as e:
        logger.error(f"Failed to start media buy status scheduler: {e}", exc_info=True)

    yield

    # Shutdown: Stop media buy status scheduler
    from src.services.media_buy_status_scheduler import stop_media_buy_status_scheduler

    logger.info("Stopping media buy status scheduler...")
    try:
        await stop_media_buy_status_scheduler()
        logger.info("✅ Media buy status scheduler stopped")
    except Exception as e:
        logger.error(f"Failed to stop media buy status scheduler: {e}", exc_info=True)

    # Shutdown: Stop delivery webhook scheduler
    from src.services.delivery_webhook_scheduler import stop_delivery_webhook_scheduler

    logger.info("Stopping delivery webhook scheduler...")
    try:
        await stop_delivery_webhook_scheduler()
        logger.info("✅ Delivery webhook scheduler stopped")
    except Exception as e:
        logger.error(f"Failed to stop delivery webhook scheduler: {e}", exc_info=True)


mcp = FastMCP(
    name="AdCPSalesAgent",
    # Sessions enabled for HTTP context (tenant detection via headers)
    # Note: stateless_http is now configured at runtime via run() or global settings
    lifespan=lifespan_context,
)

# Initialize creative engine with minimal config (will be tenant-specific later)
creative_engine_config: dict[str, Any] = {}
creative_engine = MockCreativeEngine(creative_engine_config)


def load_media_buys_from_db():
    """Load existing media buys from database into memory on startup."""
    try:
        # We can't load tenant-specific media buys at startup since we don't have tenant context
        # Media buys will be loaded on-demand when needed
        console.print("[dim]Media buys will be loaded on-demand from database[/dim]")
    except Exception as e:
        console.print(f"[yellow]Warning: Could not initialize media buys from database: {e}[/yellow]")


def load_tasks_from_db():
    """[DEPRECATED] This function is no longer needed - tasks are queried directly from database."""
    # This function is kept for backward compatibility but does nothing
    # All task operations now use direct database queries
    pass


# Removed get_task_from_db - replaced by workflow-based system


# --- In-Memory State ---
media_buys: dict[str, tuple[CreateMediaBuyRequest, str]] = {}
creative_assignments: dict[str, dict[str, list[str]]] = {}
creative_statuses: dict[str, CreativeStatus] = {}
product_catalog: list[Product] = []
creative_library: dict[str, Creative] = {}  # creative_id -> Creative
creative_groups: dict[str, CreativeGroup] = {}  # group_id -> CreativeGroup
creative_assignments_v2: dict[str, CreativeAssignment] = {}  # assignment_id -> CreativeAssignment
# REMOVED: human_tasks dictionary - now using direct database queries only

# Note: load_tasks_from_db() is no longer needed - tasks are queried directly from database

# Authentication cache removed - FastMCP v2.11.0+ properly forwards headers

# Import audit logger for later use

# Import context manager for workflow steps
from src.core.context_manager import ContextManager

context_mgr = ContextManager()

# --- Adapter Configuration ---
# Get adapter from config, fallback to mock
SELECTED_ADAPTER = ((config.get("ad_server", {}).get("adapter") or "mock") if config else "mock").lower()
AVAILABLE_ADAPTERS = ["mock", "gam", "kevel", "triton", "triton_digital"]

# --- In-Memory State (already initialized above, just adding context_map) ---
context_map: dict[str, str] = {}  # Maps context_id to media_buy_id

# --- Dry Run Mode ---
DRY_RUN_MODE = config.get("dry_run", False)
if DRY_RUN_MODE:
    console.print("[bold yellow]🏃 DRY RUN MODE ENABLED - Adapter calls will be logged[/bold yellow]")

# Display selected adapter
if SELECTED_ADAPTER not in AVAILABLE_ADAPTERS:
    console.print(f"[bold red]❌ Invalid adapter '{SELECTED_ADAPTER}'. Using 'mock' instead.[/bold red]")
    SELECTED_ADAPTER = "mock"
console.print(f"[bold cyan]🔌 Using adapter: {SELECTED_ADAPTER.upper()}[/bold cyan]")


# --- Creative Conversion Helper ---
# Creative helper functions moved to src/core/helpers.py and imported above


# --- Security Helper ---


# --- Activity Feed Helper ---


# --- MCP Tools (Full Implementation) ---


# Unified update tools


# --- Admin Tools ---


# --- Human-in-the-Loop Task Queue Tools ---
# DEPRECATED workflow functions moved to src/core/helpers/workflow_helpers.py and imported above

# Removed get_pending_workflows - replaced by admin dashboard workflow views

# Removed assign_task - assignment handled through admin UI workflow management

# Dry run logs are now handled by the adapters themselves


def get_product_catalog() -> list[Product]:
    """Get products for the current tenant.

    Uses shared convert_product_model_to_schema() to ensure consistent
    conversion logic across all product catalog providers.
    """
    from sqlalchemy.orm import selectinload

    from src.core.product_conversion import convert_product_model_to_schema

    tenant = get_current_tenant()

    with get_db_session() as session:
        stmt = (
            select(ModelProduct)
            .filter_by(tenant_id=tenant["tenant_id"])
            .options(selectinload(ModelProduct.pricing_options))
        )
        products = session.scalars(stmt).all()

        # Use shared conversion function - handles all required fields,
        # pricing options (with typed instances), and all edge cases
        loaded_products = []
        for product in products:
            try:
                converted_product = convert_product_model_to_schema(product)
                loaded_products.append(converted_product)
            except Exception as e:
                logger.error(f"Failed to convert product {product.product_id}: {e}")
                # Re-raise to surface conversion errors
                raise ValueError(f"Product {product.product_id} conversion failed: {e}") from e

    # convert_product_model_to_schema returns LibraryProduct,
    # which our Product extends - safe cast at runtime
    return loaded_products


# Creative macro support is now simplified to a single creative_macro string
# that AEE can provide as a third type of provided_signal.
# Ad servers like GAM can inject this string into creatives.

if __name__ == "__main__":
    init_db(exit_on_error=True)  # Exit on error when run as main
    # Server is now run via run_server.py script

# Always add health check endpoint
from fastapi import Request
from fastapi.responses import JSONResponse

# --- Strategy and Simulation Control ---
from src.core.strategy import StrategyManager


def get_strategy_manager(context: Context | None) -> StrategyManager:
    """Get strategy manager for current context."""
    principal_id, tenant_config = get_principal_from_context(context)
    if tenant_config:
        set_current_tenant(tenant_config)
    else:
        tenant_config = get_current_tenant()

    if not tenant_config:
        raise ToolError("No tenant configuration found")

    return StrategyManager(tenant_id=tenant_config.get("tenant_id"), principal_id=principal_id)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request):
    """Health check endpoint."""
    return JSONResponse({"status": "healthy", "service": "mcp"})


@mcp.custom_route("/admin/reset-db-pool", methods=["POST"])
async def reset_db_pool(request: Request):
    """Reset database connection pool after external data changes.

    This is a testing-only endpoint that flushes the SQLAlchemy connection pool,
    ensuring fresh connections see recently committed data. Only works when
    ADCP_TESTING environment variable is set to 'true'.

    Use case: E2E tests that initialize data via external script need to ensure
    the running MCP server's connection pool picks up that fresh data.
    """
    # Security: Only allow in testing mode
    if os.getenv("ADCP_TESTING") != "true":
        logger.warning("Attempted to reset DB pool outside testing mode")
        return JSONResponse({"error": "This endpoint is only available in testing mode"}, status_code=403)

    try:
        from src.core.database.database_session import reset_engine

        logger.info("Resetting database connection pool and tenant context (testing mode)")

        # Reset SQLAlchemy connection pool
        reset_engine()
        logger.info("  ✓ Database connection pool reset")

        # CRITICAL: Clear tenant context ContextVar
        # After data initialization, the tenant context may contain stale tenant data
        # that was loaded before products were created. Force fresh tenant lookup.
        from src.core.config_loader import current_tenant

        try:
            current_tenant.set(None)
            logger.info("  ✓ Cleared tenant context (will force fresh lookup on next request)")
        except Exception as ctx_error:
            logger.warning(f"  ⚠️ Could not clear tenant context: {ctx_error}")

        return JSONResponse(
            {
                "status": "success",
                "message": "Database connection pool and tenant context reset successfully",
            }
        )
    except Exception as e:
        logger.error(f"Failed to reset database state: {e}")
        return JSONResponse({"error": f"Failed to reset: {str(e)}"}, status_code=500)


@mcp.custom_route("/debug/db-state", methods=["GET"])
async def debug_db_state(request: Request):
    """Debug endpoint to show database state (testing only)."""
    if os.getenv("ADCP_TESTING") != "true":
        return JSONResponse({"error": "Only available in testing mode"}, status_code=403)

    try:
        from src.core.database.database_session import get_db_session

        with get_db_session() as session:
            # Count all products
            product_stmt = select(ModelProduct)
            all_products = session.scalars(product_stmt).all()

            # Get ci-test-token principal
            principal_stmt = select(ModelPrincipal).filter_by(access_token="ci-test-token")
            principal = session.scalars(principal_stmt).first()

            principal_info = None
            tenant_info = None
            tenant_products: list[ModelProduct] = []

            if principal:
                principal_info = {
                    "principal_id": principal.principal_id,
                    "tenant_id": principal.tenant_id,
                }

                # Get tenant
                tenant_stmt = select(Tenant).filter_by(tenant_id=principal.tenant_id)
                tenant = session.scalars(tenant_stmt).first()
                if tenant:
                    tenant_info = {
                        "tenant_id": tenant.tenant_id,
                        "name": tenant.name,
                        "is_active": tenant.is_active,
                    }

                # Get products for that tenant
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


@mcp.custom_route("/debug/tenant", methods=["GET"])
async def debug_tenant(request: Request):
    """Debug endpoint to check tenant detection from headers."""
    headers = dict(request.headers)

    # Check for Apx-Incoming-Host header
    apx_host = headers.get("apx-incoming-host") or headers.get("Apx-Incoming-Host")
    host_header = headers.get("host") or headers.get("Host")

    # Resolve tenant using same logic as auth
    tenant_id = None
    tenant_name = None
    detection_method = None

    # Try Apx-Incoming-Host first
    if apx_host:
        tenant = get_tenant_by_virtual_host(apx_host)
        if tenant:
            tenant_id = tenant.get("tenant_id")
            tenant_name = tenant.get("name")
            detection_method = "apx-incoming-host"

    # Try Host header subdomain
    if not tenant_id and host_header:
        subdomain = host_header.split(".")[0] if "." in host_header else None
        if subdomain and subdomain not in ["localhost", "adcp-sales-agent", "www", "sales-agent"]:
            tenant_id = subdomain
            detection_method = "host-subdomain"

    response_data = {
        "tenant_id": tenant_id,
        "tenant_name": tenant_name,
        "detection_method": detection_method,
        "apx_incoming_host": apx_host,
        "host": host_header,
    }

    # Add X-Tenant-Id header to response
    response = JSONResponse(response_data)
    if tenant_id:
        response.headers["X-Tenant-Id"] = tenant_id

    return response


@mcp.custom_route("/debug/root", methods=["GET"])
async def debug_root(request: Request):
    """Debug endpoint to test root route logic without redirects."""
    headers = dict(request.headers)

    # Check for Apx-Incoming-Host header (Approximated.app virtual host)
    # Try both capitalized and lowercase versions since HTTP header names are case-insensitive
    apx_host = headers.get("apx-incoming-host") or headers.get("Apx-Incoming-Host")
    # Also check standard Host header for direct virtual hosts
    host_header = headers.get("host") or headers.get("Host")

    virtual_host = apx_host or host_header

    # Get tenant
    tenant = get_tenant_by_virtual_host(virtual_host) if virtual_host else None

    debug_info = {
        "all_headers": headers,
        "apx_host": apx_host,
        "host_header": host_header,
        "virtual_host": virtual_host,
        "tenant_found": tenant is not None,
        "tenant_id": tenant.get("tenant_id") if tenant else None,
        "tenant_name": tenant.get("name") if tenant else None,
    }

    # Also test landing page generation
    if tenant:
        try:
            html_content = generate_tenant_landing_page(tenant, virtual_host)
            debug_info["landing_page_generated"] = True
            debug_info["landing_page_length"] = len(html_content)
        except Exception as e:
            debug_info["landing_page_generated"] = False
            debug_info["landing_page_error"] = str(e)

    return JSONResponse(debug_info)


@mcp.custom_route("/debug/landing", methods=["GET"])
async def debug_landing(request: Request):
    """Debug endpoint to test landing page generation directly."""
    headers = dict(request.headers)

    # Same logic as root route
    apx_host = headers.get("apx-incoming-host") or headers.get("Apx-Incoming-Host")
    host_header = headers.get("host") or headers.get("Host")
    virtual_host = apx_host or host_header

    if virtual_host:
        tenant = get_tenant_by_virtual_host(virtual_host)
        if tenant:
            try:
                html_content = generate_tenant_landing_page(tenant, virtual_host)
                return HTMLResponse(content=html_content)
            except Exception as e:
                return JSONResponse({"error": f"Landing page generation failed: {e}"}, status_code=500)

    return JSONResponse({"error": "No tenant found"}, status_code=404)


@mcp.custom_route("/debug/root-logic", methods=["GET"])
async def debug_root_logic(request: Request):
    """Debug endpoint that exactly mimics the root route logic for testing."""
    headers = dict(request.headers)

    # Exact same logic as root route
    apx_host = headers.get("apx-incoming-host") or headers.get("Apx-Incoming-Host")
    host_header = headers.get("host") or headers.get("Host")
    virtual_host = apx_host or host_header

    debug_info: dict[str, Any] = {
        "step": "initial",
        "virtual_host": virtual_host,
        "apx_host": apx_host,
        "host_header": host_header,
    }

    if virtual_host:
        debug_info["step"] = "virtual_host_found"

        # First try to look up tenant by exact virtual host match
        tenant = get_tenant_by_virtual_host(virtual_host)
        debug_info["exact_tenant_lookup"] = tenant is not None

        # If no exact match, check for domain-based routing patterns
        if not tenant and is_sales_agent_domain(virtual_host) and not virtual_host.startswith("admin."):
            debug_info["step"] = "subdomain_fallback"
            subdomain = extract_subdomain_from_host(virtual_host)
            debug_info["extracted_subdomain"] = subdomain

            # This is the fallback logic we don't need for test-agent
            try:
                with get_db_session() as db_session:
                    stmt = select(Tenant).filter_by(subdomain=subdomain, is_active=True)
                    tenant_obj = db_session.scalars(stmt).first()
                    if tenant_obj:
                        debug_info["subdomain_tenant_found"] = True
                        # Build tenant dict...
                    else:
                        debug_info["subdomain_tenant_found"] = False
            except Exception as e:
                debug_info["subdomain_error"] = str(e)

        if tenant:
            debug_info["step"] = "tenant_found"
            debug_info["tenant_id"] = tenant.get("tenant_id")
            debug_info["tenant_name"] = tenant.get("name")

            # Try landing page generation
            try:
                html_content = generate_tenant_landing_page(tenant, virtual_host)
                debug_info["step"] = "landing_page_success"
                debug_info["landing_page_length"] = len(html_content)
                debug_info["would_return"] = "HTMLResponse"
            except Exception as e:
                debug_info["step"] = "landing_page_error"
                debug_info["error"] = str(e)
                debug_info["would_return"] = "fallback HTMLResponse"
        else:
            debug_info["step"] = "no_tenant_found"
            debug_info["would_return"] = "redirect to /admin/"
    else:
        debug_info["step"] = "no_virtual_host"
        debug_info["would_return"] = "redirect to /admin/"

    return JSONResponse(debug_info)


@mcp.custom_route("/health/config", methods=["GET"])
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



# Admin and landing routes moved to src/app.py (FastAPI migration).
# Task management tools extracted to src/core/tools/task_management.py.


# Import MCP tools from separate modules at the end to avoid circular imports
# Tools are imported and then registered with MCP manually (no decorators in tool modules)
# Import error logging wrapper for centralized error visibility
from src.core.tool_error_logging import with_error_logging
from src.core.tools.capabilities import get_adcp_capabilities
from src.core.tools.creative_formats import list_creative_formats
from src.core.tools.creatives import list_creatives, sync_creatives
from src.core.tools.media_buy_create import create_media_buy
from src.core.tools.media_buy_delivery import get_media_buy_delivery
from src.core.tools.media_buy_update import update_media_buy
from src.core.tools.performance import update_performance_index
from src.core.tools.products import get_products
from src.core.tools.properties import list_authorized_properties
from src.core.tools.task_management import complete_task, get_task, list_tasks

# Register tools with MCP (must be done after imports to avoid circular dependency)
# This breaks the circular import: tool modules no longer import mcp from main.py
# Tools are wrapped with error logging to ensure errors appear in activity feed
mcp.tool()(with_error_logging(get_adcp_capabilities))
mcp.tool()(with_error_logging(get_products))
mcp.tool()(with_error_logging(list_creative_formats))
mcp.tool()(with_error_logging(sync_creatives))
mcp.tool()(with_error_logging(list_creatives))
mcp.tool()(with_error_logging(list_authorized_properties))
mcp.tool()(with_error_logging(create_media_buy))
mcp.tool()(with_error_logging(update_media_buy))
mcp.tool()(with_error_logging(get_media_buy_delivery))
mcp.tool()(with_error_logging(update_performance_index))
mcp.tool()(with_error_logging(list_tasks))
mcp.tool()(with_error_logging(get_task))
mcp.tool()(with_error_logging(complete_task))
