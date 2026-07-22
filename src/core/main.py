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

# ---------------------------------------------------------------------------
# Scheduler registry — one entry per interval scheduler.
# Each tuple is (name, start_fn_import_path, stop_fn_import_path).
# Startup iterates forward; shutdown iterates in reverse (LIFO).
# ---------------------------------------------------------------------------
_SCHEDULER_REGISTRY = [
    (
        "delivery webhook",
        "src.services.delivery_webhook_scheduler",
        "start_delivery_webhook_scheduler",
        "stop_delivery_webhook_scheduler",
    ),
    (
        "media buy status",
        "src.services.media_buy_status_scheduler",
        "start_media_buy_status_scheduler",
        "stop_media_buy_status_scheduler",
    ),
    (
        "TMP health",
        "src.services.tmp_health_scheduler",
        "start_tmp_health_scheduler",
        "stop_tmp_health_scheduler",
    ),
]


# Explicit verb forms for _run_scheduler_fn log messages.
# Using string templates like "%sing" / "%sed" produces "Stoping"/"stoped" for
# verb="stop" — anyone grepping deploy logs for "Stopping"/"stopped" misses all
# three schedulers. Pass the full forms instead.
_SCHEDULER_VERB_FORMS: dict[str, tuple[str, str]] = {
    "start": ("Starting", "started"),
    "stop": ("Stopping", "stopped"),
}


async def _run_scheduler_fn(verb: str, name: str, module_path: str, fn_name: str) -> None:
    """Import and call a scheduler lifecycle function, logging success/failure.

    Extracted from the identical ``_start_scheduler`` / ``_stop_scheduler``
    pair — they differed only in the verb string used in log messages.

    Args:
        verb:        Lifecycle verb — one of ``"start"`` or ``"stop"``.
        name:        Scheduler name (e.g. ``"TMP health"``).
        module_path: Dotted import path of the scheduler module.
        fn_name:     Name of the function to call on the module.
    """
    import importlib

    present, past = _SCHEDULER_VERB_FORMS.get(verb, (verb.capitalize() + "ing", verb + "ed"))
    logger.info("%s %s scheduler...", present, name)
    try:
        mod = importlib.import_module(module_path)
        await getattr(mod, fn_name)()
        logger.info("✅ %s scheduler %s", name, past)
    except Exception as e:
        logger.error("Failed to %s %s scheduler: %s", verb, name, e, exc_info=True)


def _background_schedulers_enabled() -> bool:
    """Whether to start the background schedulers on app startup.

    ``ADCP_RUN_BACKGROUND_SCHEDULERS`` is a **test-only** knob that defaults to
    ENABLED (schedulers run in production unless the value is exactly ``"false"``,
    read at runtime). The test harness sets it to ``false`` because the schedulers
    run a batch immediately on startup, on the *real* wall clock, and mutate
    media-buy status rows — which silently rewrites the rows a test just seeded
    (e.g. promoting a seeded ``pending_start`` buy to ``active`` before the
    assertion runs). It is NOT an operator control: disabling it in production
    stops automatic pending->active->completed status transitions and delivery
    webhooks, so an accidental disable is logged at WARNING (below) to make it
    visible in production logs.
    """
    import os

    return os.getenv("ADCP_RUN_BACKGROUND_SCHEDULERS", "true").lower() != "false"


# Lifespan context manager for FastMCP startup/shutdown
@asynccontextmanager
async def lifespan_context(app):
    """Handle application startup and shutdown.

    Schedulers are started in registry order and stopped in reverse (LIFO)
    so dependencies are torn down cleanly.
    """
    schedulers_enabled = _background_schedulers_enabled()
    if not schedulers_enabled:
        # WARNING, not INFO: this is a test-only knob (see
        # _background_schedulers_enabled). If it is ever set in production the
        # status/webhook schedulers do not run — surface that loudly.
        logger.warning(
            "Background schedulers DISABLED via ADCP_RUN_BACKGROUND_SCHEDULERS=false — "
            "media-buy status transitions and delivery webhooks will NOT run. "
            "This is a test-only knob; unset it in production."
        )
        yield
        return

    for name, module_path, start_fn, _stop_fn in _SCHEDULER_REGISTRY:
        await _run_scheduler_fn("start", name, module_path, start_fn)

    yield

    for name, module_path, _start_fn, stop_fn in reversed(_SCHEDULER_REGISTRY):
        await _run_scheduler_fn("stop", name, module_path, stop_fn)


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


# Import MCP tools from separate modules and register with MCP manually.
# Tool descriptions and ToolAnnotations are imported from the AdCP SDK at
# registration time. Our tools are a subset of the SDK's 57 — matching tools
# get agent-facing descriptions and annotations (readOnlyHint, destructiveHint,
# idempotentHint). Non-matching tools keep their existing docstrings.
from adcp.server.mcp_tools import ADCP_TOOL_DEFINITIONS
from mcp.types import ToolAnnotations

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

_sdk_tool_defs = {td["name"]: td for td in ADCP_TOOL_DEFINITIONS}


def _register_tool(fn: Any) -> None:
    """Register an MCP tool with SDK description and annotations when available."""
    tool_name = fn.__name__
    sdk_def = _sdk_tool_defs.get(tool_name)
    kwargs: dict[str, Any] = {}
    if sdk_def:
        kwargs["description"] = sdk_def["description"]
        if sdk_def.get("annotations"):
            kwargs["annotations"] = ToolAnnotations(**sdk_def["annotations"])
    mcp.tool(**kwargs)(with_error_logging(fn))


_register_tool(list_accounts)
_register_tool(sync_accounts)
_register_tool(get_adcp_capabilities)
_register_tool(get_products)
_register_tool(list_creative_formats)
_register_tool(sync_creatives)
_register_tool(list_creatives)
_register_tool(list_authorized_properties)
_register_tool(create_media_buy)
_register_tool(update_media_buy)
_register_tool(get_media_buy_delivery)
_register_tool(get_media_buys)
_register_tool(update_performance_index)
_register_tool(list_tasks)
_register_tool(get_task)
_register_tool(complete_task)
