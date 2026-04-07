import logging
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.context import Context
from rich.console import Console
from sqlalchemy import select

from src.adapters.mock_creative_engine import MockCreativeEngine
from src.core.exceptions import AdCPAuthenticationError
from src.core.transport_helpers import resolve_identity_from_context

logger = logging.getLogger(__name__)

# Database models

# Other imports
from src.core.config_loader import (
    get_current_tenant,
    load_config,
    set_current_tenant,
)
from src.core.database.database import init_db
from src.core.database.database_session import get_db_session
from src.core.database.models import Product as ModelProduct
from src.core.database.models import (
    WorkflowStep,
)

# Schema models (explicit imports to avoid collisions)
# Schema adapters (wrapping generated schemas)
from src.core.schemas import (
    Creative,
    CreativeAssignment,
    CreativeStatus,
    Error,  # noqa: F401 - Required for MCP protocol error handling (regression test PR #332)
    Product,
)

# Initialize Rich console
console = Console()

# Backward compatibility alias for deprecated Task model
# The workflow system now uses WorkflowStep exclusively
Task = WorkflowStep

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

# Centralized identity resolution — runs before every tool call.
# Tools read identity via ctx.get_state('identity') instead of calling
# resolve_identity_from_context() directly.
from src.core.mcp_auth_middleware import MCPAuthMiddleware
from src.core.mcp_compat_middleware import RequestCompatMiddleware

mcp.add_middleware(MCPAuthMiddleware())
mcp.add_middleware(RequestCompatMiddleware())

# Initialize creative engine with minimal config (will be tenant-specific later)
creative_engine_config: dict[str, Any] = {}
creative_engine = MockCreativeEngine(creative_engine_config)


# Removed get_task_from_db - replaced by workflow-based system


# --- In-Memory State ---
creative_assignments: dict[str, dict[str, list[str]]] = {}
creative_statuses: dict[str, CreativeStatus] = {}
product_catalog: list[Product] = []
creative_library: dict[str, Creative] = {}  # creative_id -> Creative
creative_assignments_v2: dict[str, CreativeAssignment] = {}  # assignment_id -> CreativeAssignment
# REMOVED: human_tasks dictionary - now using direct database queries only

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


def get_product_catalog(tenant_id: str | None = None) -> list[Product]:
    """Get products for the current tenant.

    Uses shared convert_product_model_to_schema() to ensure consistent
    conversion logic across all product catalog providers.
    """
    from sqlalchemy.orm import selectinload

    from src.core.product_conversion import convert_product_model_to_schema

    if tenant_id is None:
        tenant = get_current_tenant()
        tenant_id = tenant["tenant_id"]

    with get_db_session() as session:
        stmt = select(ModelProduct).filter_by(tenant_id=tenant_id).options(selectinload(ModelProduct.pricing_options))
        products = session.scalars(stmt).all()

        loaded_products = []
        for product in products:
            loaded_products.append(convert_product_model_to_schema(product))

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

# --- Strategy and Simulation Control ---
from src.core.strategy import StrategyManager


def get_strategy_manager(context: Context | None) -> StrategyManager:
    """Get strategy manager for current context."""
    identity = resolve_identity_from_context(context, require_valid_token=True, protocol="mcp")

    if not identity or not identity.tenant_id:
        raise AdCPAuthenticationError("No tenant configuration found")

    if identity.tenant and isinstance(identity.tenant, dict):
        set_current_tenant(identity.tenant)
    else:
        tenant_config = get_current_tenant()
        if not tenant_config:
            raise AdCPAuthenticationError("No tenant configuration found")

    return StrategyManager(tenant_id=identity.tenant_id, principal_id=identity.principal_id)


# Health/debug routes moved to src/routes/health.py (FastAPI migration).
# Admin and landing routes moved to src/app.py (FastAPI migration).
# Task management tools extracted to src/core/tools/task_management.py.


# Import MCP tools from separate modules at the end to avoid circular imports
# Tools are imported and then registered with MCP manually (no decorators in tool modules)
# Import error logging wrapper for centralized error visibility
from src.core.tool_error_logging import with_error_logging
from src.core.tools.accounts import list_accounts, sync_accounts
from src.core.tools.capabilities import get_adcp_capabilities
from src.core.tools.creative_formats import list_creative_formats
from src.core.tools.creatives import list_creatives, sync_creatives
from src.core.tools.media_buy_create import create_media_buy
from src.core.tools.media_buy_delivery import get_media_buy_delivery
from src.core.tools.media_buy_list import get_media_buys
from src.core.tools.media_buy_update import update_media_buy
from src.core.tools.performance import update_performance_index
from src.core.tools.products import get_products
from src.core.tools.properties import list_authorized_properties
from src.core.tools.task_management import complete_task, get_task, list_tasks

# Register tools with MCP (must be done after imports to avoid circular dependency)
# This breaks the circular import: tool modules no longer import mcp from main.py
# Tools are wrapped with error logging to ensure errors appear in activity feed
mcp.tool()(with_error_logging(list_accounts))
mcp.tool()(with_error_logging(sync_accounts))
mcp.tool()(with_error_logging(get_adcp_capabilities))
mcp.tool()(with_error_logging(get_products))
mcp.tool()(with_error_logging(list_creative_formats))
mcp.tool()(with_error_logging(sync_creatives))
mcp.tool()(with_error_logging(list_creatives))
mcp.tool()(with_error_logging(list_authorized_properties))
mcp.tool()(with_error_logging(create_media_buy))
mcp.tool()(with_error_logging(update_media_buy))
mcp.tool()(with_error_logging(get_media_buy_delivery))
mcp.tool()(with_error_logging(get_media_buys))
mcp.tool()(with_error_logging(update_performance_index))
mcp.tool()(with_error_logging(list_tasks))
mcp.tool()(with_error_logging(get_task))
mcp.tool()(with_error_logging(complete_task))
