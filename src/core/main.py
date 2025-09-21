import json
import logging
import os
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.server.dependencies import get_http_headers
from rich.console import Console

from src.adapters.google_ad_manager import GoogleAdManager
from src.adapters.kevel import Kevel
from src.adapters.mock_ad_server import MockAdServer as MockAdServerAdapter
from src.adapters.mock_creative_engine import MockCreativeEngine
from src.adapters.triton_digital import TritonDigital
from src.core.audit_logger import get_audit_logger
from src.core.testing_hooks import (
    DeliverySimulator,
    TimeSimulator,
    apply_testing_hooks,
    get_testing_context,
)
from src.landing import generate_tenant_landing_page
from src.services.activity_feed import activity_feed

logger = logging.getLogger(__name__)

# Database models
from product_catalog_providers.factory import get_product_catalog_provider
from scripts.setup.init_database import init_db

# Other imports
from src.core.config_loader import (
    get_current_tenant,
    get_tenant_by_virtual_host,
    load_config,
    set_current_tenant,
)
from src.core.context_manager import get_context_manager
from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig, AuthorizedProperty, MediaBuy, PropertyTag, Tenant
from src.core.database.models import Principal as ModelPrincipal
from src.core.database.models import Product as ModelProduct

# Schema models (explicit imports to avoid collisions)
from src.core.schemas import (
    ActivateSignalResponse,
    CreateMediaBuyRequest,
    CreateMediaBuyResponse,
    Creative,
    CreativeAssignment,
    CreativeGroup,
    CreativeStatus,
    GetMediaBuyDeliveryRequest,
    GetMediaBuyDeliveryResponse,
    GetProductsRequest,
    GetProductsResponse,
    GetSignalsRequest,
    GetSignalsResponse,
    ListAuthorizedPropertiesRequest,
    ListAuthorizedPropertiesResponse,
    ListCreativeFormatsResponse,
    ListCreativesResponse,
    MediaBuyDeliveryData,
    MediaPackage,
    PackagePerformance,
    Principal,
    Product,
    Property,
    PropertyIdentifier,
    PropertyTagMetadata,
    ReportingPeriod,
    Signal,
    SyncCreativesResponse,
    TaskStatus,
    UpdateMediaBuyRequest,
    UpdateMediaBuyResponse,
    UpdatePerformanceIndexRequest,
    UpdatePerformanceIndexResponse,
)
from src.services.policy_check_service import PolicyCheckService, PolicyStatus
from src.services.slack_notifier import get_slack_notifier

# Initialize Rich console
console = Console()

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


def safe_parse_json_field(field_value, field_name="field", default=None):
    """
    Safely parse a database field that might be JSON string (SQLite) or dict (PostgreSQL JSONB).

    Args:
        field_value: The field value from database (could be str, dict, None, etc.)
        field_name: Name of the field for logging purposes
        default: Default value to return on parse failure (default: None)

    Returns:
        Parsed dict/list or default value
    """
    if not field_value:
        return default if default is not None else {}

    if isinstance(field_value, str):
        try:
            parsed = json.loads(field_value)
            # Validate the parsed result is the expected type
            if default is not None and not isinstance(parsed, type(default)):
                logger.warning(f"Parsed {field_name} has unexpected type: {type(parsed)}, expected {type(default)}")
                return default
            return parsed
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Invalid JSON in {field_name}: {e}")
            return default if default is not None else {}
    elif isinstance(field_value, dict | list):
        return field_value
    else:
        logger.warning(f"Unexpected type for {field_name}: {type(field_value)}")
        return default if default is not None else {}


# --- Authentication ---


def get_principal_from_token(token: str, tenant_id: str | None = None) -> str | None:
    """Looks up a principal_id from the database using a token.

    If tenant_id is provided, only looks in that specific tenant.
    If not provided, searches globally by token and sets the tenant context.
    """

    # Use standardized session management
    with get_db_session() as session:
        # Use explicit transaction for consistency
        with session.begin():
            if tenant_id:
                # If tenant_id specified, ONLY look in that tenant
                principal = session.query(ModelPrincipal).filter_by(access_token=token, tenant_id=tenant_id).first()

                if not principal:
                    # Also check if it's the admin token for this specific tenant
                    tenant = session.query(Tenant).filter_by(tenant_id=tenant_id, is_active=True).first()
                    if tenant and token == tenant.admin_token:
                        # Set tenant context for admin token
                        tenant_dict = {
                            "tenant_id": tenant.tenant_id,
                            "name": tenant.name,
                            "subdomain": tenant.subdomain,
                            "ad_server": tenant.ad_server,
                            "max_daily_budget": tenant.max_daily_budget,
                            "enable_axe_signals": tenant.enable_axe_signals,
                            "authorized_emails": tenant.authorized_emails or [],
                            "authorized_domains": tenant.authorized_domains or [],
                            "slack_webhook_url": tenant.slack_webhook_url,
                            "admin_token": tenant.admin_token,
                            "auto_approve_formats": tenant.auto_approve_formats or [],
                            "human_review_required": tenant.human_review_required,
                            "slack_audit_webhook_url": tenant.slack_audit_webhook_url,
                            "hitl_webhook_url": tenant.hitl_webhook_url,
                            "policy_settings": tenant.policy_settings,
                        }
                        set_current_tenant(tenant_dict)
                        return f"{tenant_id}_admin"
                    return None
            else:
                # No tenant specified - search globally by token
                principal = session.query(ModelPrincipal).filter_by(access_token=token).first()

                if not principal:
                    return None

                # CRITICAL: Validate the tenant exists and is active before proceeding
                tenant_check = session.query(Tenant).filter_by(tenant_id=principal.tenant_id, is_active=True).first()
                if not tenant_check:
                    # Tenant is disabled or deleted - fail securely
                    return None

            # Get the tenant for this principal and set it as current context
            tenant = session.query(Tenant).filter_by(tenant_id=principal.tenant_id, is_active=True).first()
            if tenant:
                tenant_dict = {
                    "tenant_id": tenant.tenant_id,
                    "name": tenant.name,
                    "subdomain": tenant.subdomain,
                    "ad_server": tenant.ad_server,
                    "max_daily_budget": tenant.max_daily_budget,
                    "enable_axe_signals": tenant.enable_axe_signals,
                    "authorized_emails": tenant.authorized_emails or [],
                    "authorized_domains": tenant.authorized_domains or [],
                    "slack_webhook_url": tenant.slack_webhook_url,
                    "admin_token": tenant.admin_token,
                    "auto_approve_formats": tenant.auto_approve_formats or [],
                    "human_review_required": tenant.human_review_required,
                    "slack_audit_webhook_url": tenant.slack_audit_webhook_url,
                    "hitl_webhook_url": tenant.hitl_webhook_url,
                    "policy_settings": tenant.policy_settings,
                }
                set_current_tenant(tenant_dict)

                # Check if this is the admin token for the tenant
                if token == tenant.admin_token:
                    return f"{tenant.tenant_id}_admin"

            return principal.principal_id


def get_principal_from_context(context: Context | None) -> str | None:
    """Extract principal ID from the FastMCP context using x-adcp-auth header.

    Uses the current recommended FastMCP pattern with get_http_headers().
    Requires FastMCP >= 2.11.0.
    """
    if not context:
        return None

    # Get headers using the recommended FastMCP approach
    headers = get_http_headers()

    # Get the x-adcp-auth header
    auth_token = headers.get("x-adcp-auth")
    if not auth_token:
        return None

    # Check if a specific tenant was requested via header or subdomain
    requested_tenant_id = None
    tenant_context = None

    # 1. Check Apx-Incoming-Host header (for Approximated.app virtual hosts)
    apx_host = headers.get("apx-incoming-host")
    if apx_host:
        tenant_context = get_tenant_by_virtual_host(apx_host)
        if tenant_context:
            requested_tenant_id = tenant_context["tenant_id"]
            # Set tenant context immediately for virtual host routing
            set_current_tenant(tenant_context)

    # 2. Check x-adcp-tenant header (set by middleware for path-based routing)
    if not requested_tenant_id:
        requested_tenant_id = headers.get("x-adcp-tenant")

    # 3. If not found, check host header for subdomain
    if not requested_tenant_id:
        host = headers.get("host", "")
        subdomain = host.split(".")[0] if "." in host else None
        if subdomain and subdomain not in ["localhost", "adcp-sales-agent", "www"]:
            requested_tenant_id = subdomain

    # Validate token and get principal
    # If a specific tenant was requested, validate against it
    # Otherwise, look up by token alone and set tenant context
    return get_principal_from_token(auth_token, requested_tenant_id)


def get_principal_adapter_mapping(principal_id: str) -> dict[str, Any]:
    """Get the platform mappings for a principal."""
    tenant = get_current_tenant()
    with get_db_session() as session:
        principal = (
            session.query(ModelPrincipal).filter_by(principal_id=principal_id, tenant_id=tenant["tenant_id"]).first()
        )
        return principal.platform_mappings if principal else {}


def get_principal_object(principal_id: str) -> Principal | None:
    """Get a Principal object for the given principal_id."""
    tenant = get_current_tenant()
    with get_db_session() as session:
        principal = (
            session.query(ModelPrincipal).filter_by(principal_id=principal_id, tenant_id=tenant["tenant_id"]).first()
        )

        if principal:
            return Principal(
                principal_id=principal.principal_id,
                name=principal.name,
                platform_mappings=principal.platform_mappings,
            )
    return None


def get_adapter_principal_id(principal_id: str, adapter: str) -> str | None:
    """Get the adapter-specific ID for a principal."""
    mappings = get_principal_adapter_mapping(principal_id)

    # Map adapter names to their specific fields
    adapter_field_map = {
        "gam": "gam_advertiser_id",
        "kevel": "kevel_advertiser_id",
        "triton": "triton_advertiser_id",
        "mock": "mock_advertiser_id",
    }

    field_name = adapter_field_map.get(adapter)
    if field_name:
        return str(mappings.get(field_name, "")) if mappings.get(field_name) else None
    return None


def get_adapter(principal: Principal, dry_run: bool = False, testing_context=None):
    """Get the appropriate adapter instance for the selected adapter type."""
    # Get tenant and adapter config from database
    tenant = get_current_tenant()
    selected_adapter = tenant.get("ad_server", "mock")

    # Get adapter config from adapter_config table
    with get_db_session() as session:
        config_row = session.query(AdapterConfig).filter_by(tenant_id=tenant["tenant_id"]).first()

        adapter_config = {"enabled": True}
        if config_row:
            adapter_type = config_row.adapter_type
            if adapter_type == "mock":
                adapter_config["dry_run"] = config_row.mock_dry_run
            elif adapter_type == "google_ad_manager":
                adapter_config["network_code"] = config_row.gam_network_code
                adapter_config["refresh_token"] = config_row.gam_refresh_token
                adapter_config["company_id"] = config_row.gam_company_id
                adapter_config["trafficker_id"] = config_row.gam_trafficker_id
                adapter_config["manual_approval_required"] = config_row.gam_manual_approval_required
            elif adapter_type == "kevel":
                adapter_config["network_id"] = config_row.kevel_network_id
                adapter_config["api_key"] = config_row.kevel_api_key
                adapter_config["manual_approval_required"] = config_row.kevel_manual_approval_required
            elif adapter_type == "triton":
                adapter_config["station_id"] = config_row.triton_station_id
                adapter_config["api_key"] = config_row.triton_api_key

    if not selected_adapter:
        # Default to mock if no adapter specified
        selected_adapter = "mock"
        adapter_config = {"enabled": True}

    # Create the appropriate adapter instance with tenant_id and testing context
    tenant_id = tenant["tenant_id"]
    if selected_adapter == "mock":
        return MockAdServerAdapter(
            adapter_config, principal, dry_run, tenant_id=tenant_id, strategy_context=testing_context
        )
    elif selected_adapter == "google_ad_manager":
        return GoogleAdManager(adapter_config, principal, dry_run, tenant_id=tenant_id)
    elif selected_adapter == "kevel":
        return Kevel(adapter_config, principal, dry_run, tenant_id=tenant_id)
    elif selected_adapter in ["triton", "triton_digital"]:
        return TritonDigital(adapter_config, principal, dry_run, tenant_id=tenant_id)
    else:
        # Default to mock for unsupported adapters
        return MockAdServerAdapter(
            adapter_config, principal, dry_run, tenant_id=tenant_id, strategy_context=testing_context
        )


# --- Initialization ---
# NOTE: Database initialization moved to startup script to avoid import-time failures
# The run_all_services.py script handles database initialization before starting the MCP server

# Try to load config, but use defaults if no tenant context available
try:
    config = load_config()
except (RuntimeError, Exception) as e:
    # Use minimal config for test environments or when DB is unavailable
    # This handles both "No tenant in context" and database connection errors
    if "No tenant in context" in str(e) or "connection" in str(e).lower() or "operational" in str(e).lower():
        config = {
            "creative_engine": {},
            "dry_run": False,
            "adapters": {"mock": {"enabled": True}},
            "ad_server": {"adapter": "mock", "enabled": True},
        }
    else:
        raise

mcp = FastMCP(
    name="AdCPSalesAgent",
    # Use stateless HTTP mode to avoid session requirements
    stateless_http=True,
)

# Initialize creative engine with minimal config (will be tenant-specific later)
creative_engine_config = {}
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
def _convert_creative_to_adapter_asset(creative: Creative, package_assignments: list[str]) -> dict[str, Any]:
    """Convert AdCP v1.3+ Creative object to format expected by ad server adapters."""

    # Base asset object with common fields
    asset = {
        "creative_id": creative.creative_id,
        "name": creative.name,
        "format": creative.format,
        "package_assignments": package_assignments,
    }

    # Determine creative type using AdCP v1.3+ logic
    creative_type = creative.get_creative_type()

    if creative_type == "third_party_tag":
        # Third-party tag creative - use AdCP v1.3+ snippet fields
        snippet = creative.get_snippet_content()
        if not snippet:
            raise ValueError(f"No snippet found for third-party creative {creative.creative_id}")

        asset["snippet"] = snippet
        asset["snippet_type"] = creative.snippet_type or _detect_snippet_type(snippet)
        asset["url"] = creative.url  # Keep URL for fallback

        # Add delivery settings
        if creative.delivery_settings:
            asset["delivery_settings"] = creative.delivery_settings

    elif creative_type == "native":
        # Native creative - use AdCP v1.3+ template_variables field
        template_vars = creative.get_template_variables_dict()
        if not template_vars:
            raise ValueError(f"No template_variables found for native creative {creative.creative_id}")

        asset["template_variables"] = template_vars
        asset["url"] = creative.url  # Fallback URL

        # Add delivery settings
        if creative.delivery_settings:
            asset["delivery_settings"] = creative.delivery_settings

    elif creative_type == "vast":
        # VAST reference
        asset["snippet"] = creative.get_snippet_content() or creative.url
        asset["snippet_type"] = creative.snippet_type or ("vast_xml" if ".xml" in creative.url else "vast_url")

    else:  # hosted_asset
        # Traditional hosted asset (image/video)
        asset["media_url"] = creative.get_primary_content_url()
        asset["url"] = asset["media_url"]  # For backward compatibility

    # Add common optional fields
    if creative.click_url:
        asset["click_url"] = creative.click_url
    if creative.width:
        asset["width"] = creative.width
    if creative.height:
        asset["height"] = creative.height
    if creative.duration:
        asset["duration"] = creative.duration

    return asset


def _detect_snippet_type(snippet: str) -> str:
    """Auto-detect snippet type from content for legacy support."""
    if snippet.startswith("<?xml") or ".xml" in snippet:
        return "vast_xml"
    elif snippet.startswith("http") and "vast" in snippet.lower():
        return "vast_url"
    elif snippet.startswith("<script"):
        return "javascript"
    else:
        return "html"  # Default


# --- Security Helper ---
def _get_principal_id_from_context(context: Context) -> str:
    """Extracts the token from the header and returns a principal_id."""
    principal_id = get_principal_from_context(context)
    if not principal_id:
        raise ToolError("Missing or invalid x-adcp-auth header for authentication.")

    console.print(f"[bold green]Authenticated principal '{principal_id}'[/bold green]")
    return principal_id


def _verify_principal(media_buy_id: str, context: Context):
    principal_id = _get_principal_id_from_context(context)
    if media_buy_id not in media_buys:
        raise ValueError(f"Media buy '{media_buy_id}' not found.")
    if media_buys[media_buy_id][1] != principal_id:
        # Log security violation
        from src.core.audit_logger import get_audit_logger

        tenant = get_current_tenant()
        security_logger = get_audit_logger("AdCP", tenant["tenant_id"])
        security_logger.log_security_violation(
            operation="access_media_buy",
            principal_id=principal_id,
            resource_id=media_buy_id,
            reason=f"Principal does not own media buy (owner: {media_buys[media_buy_id][1]})",
        )
        raise PermissionError(f"Principal '{principal_id}' does not own media buy '{media_buy_id}'.")


# --- Activity Feed Helper ---


def log_tool_activity(context: Context, tool_name: str, start_time: float = None):
    """Log tool activity to the activity feed."""
    try:
        tenant = get_current_tenant()
        if not tenant:
            return

        # Get principal name
        principal_id = get_principal_from_context(context)
        principal_name = "Unknown"

        if principal_id:
            with get_db_session() as session:
                principal = (
                    session.query(ModelPrincipal)
                    .filter_by(principal_id=principal_id, tenant_id=tenant["tenant_id"])
                    .first()
                )
                if principal:
                    principal_name = principal.name

        # Calculate response time if start_time provided
        response_time_ms = None
        if start_time:
            response_time_ms = int((time.time() - start_time) * 1000)

        # Log to activity feed (for WebSocket real-time updates)
        activity_feed.log_api_call(
            tenant_id=tenant["tenant_id"],
            principal_name=principal_name,
            method=tool_name,
            status_code=200,
            response_time_ms=response_time_ms,
        )

        # Also log to audit logs (for persistent dashboard activity feed)
        audit_logger = get_audit_logger("MCP", tenant["tenant_id"])
        details = {"tool": tool_name, "status": "success"}
        if response_time_ms:
            details["response_time_ms"] = response_time_ms

        audit_logger.log_operation(
            operation=tool_name,
            principal_name=principal_name,
            success=True,
            details=details,
        )
    except Exception as e:
        # Don't let activity logging break the main flow
        console.print(f"[yellow]Activity logging error: {e}[/yellow]")


# --- MCP Tools (Full Implementation) ---


@mcp.tool
async def get_products(brief: str, promoted_offering: str, context: Context = None) -> GetProductsResponse:
    """Get available products matching the brief.

    Args:
        brief: Brief description of the advertising campaign or requirements
        promoted_offering: What is being promoted/advertised (required per AdCP spec)
        context: FastMCP context (automatically provided)

    Returns:
        GetProductsResponse containing matching products
    """
    from src.core.tool_context import ToolContext

    # Create request object from individual parameters (MCP-compliant)
    req = GetProductsRequest(brief=brief or "", promoted_offering=promoted_offering)

    start_time = time.time()

    # Handle both old Context and new ToolContext
    if isinstance(context, ToolContext):
        # New context management - everything is already extracted
        testing_ctx_raw = context.testing_context
        # Convert dict testing context back to TestingContext object if needed
        if isinstance(testing_ctx_raw, dict):
            from src.core.testing_hooks import TestingContext

            testing_ctx = TestingContext(**testing_ctx_raw)
        else:
            testing_ctx = testing_ctx_raw
        principal_id = context.principal_id
        tenant = {"tenant_id": context.tenant_id}  # Simplified tenant info
    else:
        # Legacy path - extract from FastMCP Context
        testing_ctx = get_testing_context(context)
        # For discovery endpoints, authentication is optional
        principal_id = get_principal_from_context(context)  # Returns None if no auth
        tenant = get_current_tenant()
        if not tenant:
            raise ToolError("No tenant context available")

    # Get the Principal object with ad server mappings
    principal = get_principal_object(principal_id) if principal_id else None
    principal_data = principal.model_dump() if principal else None

    # Validate promoted_offering per AdCP spec
    if not req.promoted_offering or not req.promoted_offering.strip():
        raise ToolError("promoted_offering is required per AdCP spec and cannot be empty")

    offering = req.promoted_offering.strip()
    generic_terms = {
        "footwear",
        "shoes",
        "clothing",
        "apparel",
        "electronics",
        "food",
        "beverages",
        "automotive",
        "athletic",
    }
    words = offering.split()

    # Must have at least 2 words (brand + product)
    if len(words) < 2:
        raise ToolError(
            f"Invalid promoted_offering: '{offering}'. Must include both brand and specific product "
            f"(e.g., 'Nike Air Jordan 2025 basketball shoes', not just 'shoes')"
        )

    # Check if it's just generic category terms without a brand
    if all(word.lower() in generic_terms or word.lower() in ["and", "or", "the", "a", "an"] for word in words):
        raise ToolError(
            f"Invalid promoted_offering: '{offering}'. Must include brand name and specific product, "
            f"not just generic category (e.g., 'Nike Air Jordan 2025' not 'athletic footwear')"
        )

    # Check policy compliance first
    policy_service = PolicyCheckService()
    # Safely parse policy_settings that might be JSON string (SQLite) or dict (PostgreSQL JSONB)
    tenant_policies = safe_parse_json_field(tenant.get("policy_settings"), field_name="policy_settings", default={})

    policy_result = await policy_service.check_brief_compliance(
        brief=req.brief,
        promoted_offering=req.promoted_offering,
        tenant_policies=tenant_policies if tenant_policies else None,
    )

    # Log the policy check
    audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
    audit_logger.log_operation(
        operation="policy_check",
        principal_name=principal_id or "anonymous",
        principal_id=principal_id or "anonymous",
        adapter_id="policy_service",
        success=policy_result.status != PolicyStatus.BLOCKED,
        details={
            "brief": req.brief[:100] + "..." if len(req.brief) > 100 else req.brief,
            "promoted_offering": (
                req.promoted_offering[:100] + "..."
                if req.promoted_offering and len(req.promoted_offering) > 100
                else req.promoted_offering
            ),
            "policy_status": policy_result.status,
            "reason": policy_result.reason,
            "restrictions": policy_result.restrictions,
        },
    )

    # Handle policy result based on settings
    # Use the already parsed policy_settings from above
    policy_settings = tenant_policies

    if policy_result.status == PolicyStatus.BLOCKED:
        # Always block if policy says blocked
        logger.warning(f"Brief blocked by policy: {policy_result.reason}")
        # Return empty products list per AdCP spec (errors handled at transport layer)
        return GetProductsResponse(products=[])

    # If restricted and manual review is required, create a task
    if policy_result.status == PolicyStatus.RESTRICTED and policy_settings.get("require_manual_review", False):
        # Create a manual review task
        with get_db_session() as session:
            task_id = f"policy_review_{tenant['tenant_id']}_{int(datetime.now(UTC).timestamp())}"

            task_details = {
                "brief": req.brief,
                "promoted_offering": req.promoted_offering,
                "principal_id": principal_id,
                "policy_status": policy_result.status,
                "restrictions": policy_result.restrictions,
                "reason": policy_result.reason,
            }

            new_task = Task(
                tenant_id=tenant["tenant_id"],
                task_id=task_id,
                media_buy_id=None,  # No media buy associated
                task_type="policy_review",
                status="pending",
                details=task_details,
                created_at=datetime.now(UTC),
            )
            session.add(new_task)
            session.commit()

        logger.info(f"Created policy review task {task_id} for restricted brief")

        # Return empty list with message about pending review
        return GetProductsResponse(
            products=[],
            message="Request pending manual review due to policy restrictions",
            context_id=context.meta.get("headers", {}).get("x-context-id") if hasattr(context, "meta") else None,
        )

    # Determine product catalog configuration based on tenant's signals discovery settings
    catalog_config = {"provider": "database", "config": {}}  # Default to database provider

    # Check if signals discovery is configured for this tenant
    if hasattr(tenant, "signals_agent_config") and tenant.get("signals_agent_config"):
        signals_config = tenant["signals_agent_config"]

        # Parse signals config if it's a string (SQLite) vs dict (PostgreSQL JSONB)
        if isinstance(signals_config, str):
            import json

            try:
                signals_config = json.loads(signals_config)
            except json.JSONDecodeError:
                logger.error(f"Invalid signals_agent_config JSON for tenant {tenant['tenant_id']}")
                signals_config = {}

        # If signals discovery is enabled, use hybrid provider
        if isinstance(signals_config, dict) and signals_config.get("enabled", False):
            logger.info(f"Using hybrid provider with signals discovery for tenant {tenant['tenant_id']}")
            catalog_config = {
                "provider": "hybrid",
                "config": {
                    "database": {},  # Use database provider defaults
                    "signals_discovery": signals_config,
                    "ranking_strategy": "signals_first",  # Prioritize signals-enhanced products
                    "max_products": 20,
                    "deduplicate": True,
                },
            }

    # Get the product catalog provider for this tenant
    provider = await get_product_catalog_provider(
        tenant["tenant_id"],
        catalog_config,
    )

    # Query products using the brief, including context for signals forwarding
    context_data = {
        "promoted_offering": req.promoted_offering,
        "tenant_id": tenant["tenant_id"],
        "principal_id": principal_id,
    }

    products = await provider.get_products(
        brief=req.brief,
        tenant_id=tenant["tenant_id"],
        principal_id=principal_id,
        principal_data=principal_data,
        context=context_data,
    )

    # Filter products based on policy compliance
    eligible_products = []
    for product in products:
        is_eligible, reason = policy_service.check_product_eligibility(policy_result, product.model_dump())

        if is_eligible:
            # Product passed policy checks - add to eligible products
            # Note: policy_compliance field removed in AdCP v2.4
            eligible_products.append(product)
        else:
            logger.info(f"Product {product.product_id} excluded: {reason}")

    # Apply testing hooks to response
    response_data = {"products": [p.model_dump() for p in eligible_products]}
    response_data = apply_testing_hooks(response_data, testing_ctx, "get_products")

    # Reconstruct products from modified data
    modified_products = [Product(**p) for p in response_data["products"]]

    # Filter pricing data for anonymous users
    pricing_message = None
    if principal_id is None:  # Anonymous user
        # Remove pricing data from products for anonymous users
        for product in modified_products:
            product.cpm = None
            product.min_spend = None
        pricing_message = "Please connect through an authorized buying agent for pricing data"

    # Log activity
    log_tool_activity(context, "get_products", start_time)

    # Create response with pricing message if anonymous
    base_message = f"Found {len(modified_products)} matching products"
    final_message = f"{base_message}. {pricing_message}" if pricing_message else base_message

    # Set status based on operation result
    status = TaskStatus.from_operation_state(
        operation_type="discovery", has_errors=False, requires_approval=False, requires_auth=principal_id is None
    )

    return GetProductsResponse(products=modified_products, message=final_message, status=status)


@mcp.tool
def list_creative_formats(context: Context) -> ListCreativeFormatsResponse:
    """List all available creative formats (AdCP spec endpoint).

    Returns comprehensive standard formats from AdCP registry plus any custom tenant formats.
    Prioritizes database formats over registry formats when format_id conflicts exist.
    """
    start_time = time.time()

    # For discovery endpoints, authentication is optional
    principal_id = get_principal_from_context(context)  # Returns None if no auth

    # Get tenant information
    tenant = get_current_tenant()
    if not tenant:
        raise ToolError("No tenant context available")

    formats = []
    format_ids_seen = set()

    # First, query database for tenant-specific and custom formats
    with get_db_session() as session:
        from src.core.database.models import CreativeFormat
        from src.core.schemas import AssetRequirement, Format

        # Get formats for this tenant (or global formats)
        db_formats = (
            session.query(CreativeFormat)
            .filter(
                (CreativeFormat.tenant_id == tenant["tenant_id"])
                | (CreativeFormat.tenant_id.is_(None))  # Global formats
            )
            .all()
        )

        for db_format in db_formats:
            # Convert database model to schema format
            assets_required = []
            if db_format.specs and isinstance(db_format.specs, dict):
                # Convert old specs format to new assets_required format
                if "assets" in db_format.specs:
                    for asset in db_format.specs["assets"]:
                        assets_required.append(
                            AssetRequirement(
                                asset_type=asset.get("asset_type", "unknown"), quantity=1, requirements=asset
                            )
                        )

            format_obj = Format(
                format_id=db_format.format_id,
                name=db_format.name,
                type=db_format.type,
                is_standard=db_format.is_standard or False,
                iab_specification=getattr(db_format, "iab_specification", None),
                requirements=db_format.specs or {},
                assets_required=assets_required if assets_required else None,
            )
            formats.append(format_obj)
            format_ids_seen.add(db_format.format_id)

    # Add standard formats from FORMAT_REGISTRY that aren't already in database
    from src.core.schemas import FORMAT_REGISTRY

    for format_id, standard_format in FORMAT_REGISTRY.items():
        if format_id not in format_ids_seen:
            formats.append(standard_format)
            format_ids_seen.add(format_id)

    # Sort formats by type and name for consistent ordering
    formats.sort(key=lambda f: (f.type, f.name))

    # Log the operation
    audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
    audit_logger.log_operation(
        operation="list_creative_formats",
        principal_name=principal_id or "anonymous",
        principal_id=principal_id or "anonymous",
        adapter_id="N/A",
        success=True,
        details={
            "format_count": len(formats),
            "standard_formats": len([f for f in formats if f.is_standard]),
            "custom_formats": len([f for f in formats if not f.is_standard]),
            "format_types": list({f.type for f in formats}),
        },
    )

    # Log activity
    log_tool_activity(context, "list_creative_formats", start_time)

    message = f"Found {len(formats)} creative formats across {len({f.type for f in formats})} format types"

    # Set status based on operation result
    status = TaskStatus.from_operation_state(
        operation_type="discovery", has_errors=False, requires_approval=False, requires_auth=principal_id is None
    )

    # Create response with schema validation metadata
    response = ListCreativeFormatsResponse(
        formats=formats, message=message, specification_version="AdCP v2.4", status=status
    )

    # Add schema validation metadata for client validation
    from src.core.schema_validation import INCLUDE_SCHEMAS_IN_RESPONSES, enhance_mcp_response_with_schema

    if INCLUDE_SCHEMAS_IN_RESPONSES:
        # Convert to dict, enhance with schema, return enhanced dict
        response_dict = response.model_dump()
        enhanced_response = enhance_mcp_response_with_schema(
            response_data=response_dict,
            model_class=ListCreativeFormatsResponse,
            include_full_schema=False,  # Set to True for development debugging
        )
        # Return the enhanced response (FastMCP handles dict returns)
        return enhanced_response

    return response


@mcp.tool
def sync_creatives(
    creatives: list[dict],
    media_buy_id: str = None,
    buyer_ref: str = None,
    assign_to_packages: list[str] = None,
    upsert: bool = True,
    context: Context = None,
) -> SyncCreativesResponse:
    """Sync creative assets to centralized library (AdCP spec endpoint).

    Primary creative management endpoint that handles:
    - Bulk creative upload/update with upsert semantics
    - Creative assignment to media buy packages
    - Support for both hosted assets (media_url) and third-party tags (snippet)

    Args:
        creatives: Array of creative assets to sync
        media_buy_id: Publisher's ID of the media buy (optional)
        buyer_ref: Buyer's reference for the media buy (optional)
        assign_to_packages: Package IDs to assign creatives to (optional)
        upsert: Whether to update existing creatives or create new ones
        context: FastMCP context (automatically provided)

    Returns:
        SyncCreativesResponse with synced creatives and assignments
    """
    from pydantic import ValidationError

    from src.core.schemas import Creative

    # Process raw creative dictionaries without schema validation initially
    # Schema objects will be created later with populated internal fields
    raw_creatives = [creative if isinstance(creative, dict) else creative.model_dump() for creative in creatives]

    start_time = time.time()

    # Authentication
    principal_id = _get_principal_id_from_context(context)

    # Get tenant information
    tenant = get_current_tenant()
    if not tenant:
        raise ToolError("No tenant context available")

    # Track synced and failed creatives
    synced_creatives = []
    failed_creatives = []
    assignments = []

    with get_db_session() as session:
        # Resolve media buy
        media_buy = None
        if media_buy_id:
            from src.core.database.models import MediaBuy

            media_buy = (
                session.query(MediaBuy).filter_by(tenant_id=tenant["tenant_id"], media_buy_id=media_buy_id).first()
            )
        elif buyer_ref:
            from src.core.database.models import MediaBuy

            media_buy = session.query(MediaBuy).filter_by(tenant_id=tenant["tenant_id"], buyer_ref=buyer_ref).first()

        if not media_buy and (media_buy_id or buyer_ref):
            raise ToolError(f"Media buy not found: {media_buy_id or buyer_ref}")

        # Process each creative with proper transaction isolation
        for creative in raw_creatives:
            try:
                # First, validate the creative against the schema before database operations
                try:
                    # Create temporary schema object for validation
                    # Map input fields to schema field names
                    schema_data = {
                        "creative_id": creative.get("creative_id") or str(uuid.uuid4()),
                        "name": creative.get("name", ""),  # Ensure name is never None
                        "format_id": creative.get("format"),  # Use alias name
                        "click_through_url": creative.get("click_url"),
                        "width": creative.get("width"),
                        "height": creative.get("height"),
                        "duration": creative.get("duration"),
                        "principal_id": principal_id,
                        "created_at": datetime.now(UTC),
                        "updated_at": datetime.now(UTC),
                        "status": "pending",
                    }

                    # Handle snippet vs media content properly (mutually exclusive)
                    if creative.get("snippet"):
                        # Snippet-based creative
                        schema_data.update(
                            {
                                "snippet": creative.get("snippet"),
                                "snippet_type": creative.get("snippet_type"),
                                "content_uri": "<script>/* Snippet-based creative */</script>",  # HTML-looking placeholder
                            }
                        )
                    else:
                        # Media-based creative
                        schema_data["content_uri"] = (
                            creative.get("url") or "https://placeholder.example.com/missing.jpg"
                        )

                    if creative.get("template_variables"):
                        schema_data["template_variables"] = creative.get("template_variables")

                    # Validate by creating a Creative schema object
                    # This will fail if required fields are missing or invalid (like empty name)
                    Creative(**schema_data)

                    # Additional business logic validation
                    if not creative.get("name") or str(creative.get("name")).strip() == "":
                        raise ValueError("Creative name cannot be empty")

                    if not creative.get("format"):
                        raise ValueError("Creative format is required")

                except (ValidationError, ValueError) as validation_error:
                    # Creative failed validation - add to failed list
                    failed_creatives.append(
                        {"creative_id": creative.get("creative_id", "unknown"), "error": str(validation_error)}
                    )
                    continue  # Skip to next creative

                # Use savepoint for individual creative transaction isolation
                with session.begin_nested():
                    # Check if creative already exists (for upsert)
                    existing_creative = None
                    if upsert and creative.get("creative_id"):
                        from src.core.database.models import Creative as DBCreative

                        existing_creative = (
                            session.query(DBCreative)
                            .filter_by(tenant_id=tenant["tenant_id"], creative_id=creative.get("creative_id"))
                            .first()
                        )

                    if existing_creative and upsert:
                        # Update existing creative
                        existing_creative.name = creative.get("name")
                        existing_creative.format = creative.get("format")
                        existing_creative.updated_at = datetime.now(UTC)

                        # Store creative properties in data field
                        data = existing_creative.data or {}
                        data.update(
                            {
                                "url": creative.get("url"),
                                "click_url": creative.get("click_url"),
                                "width": creative.get("width"),
                                "height": creative.get("height"),
                                "duration": creative.get("duration"),
                            }
                        )

                        # Update AdCP v1.3+ fields in data
                        if creative.get("snippet"):
                            data["snippet"] = creative.get("snippet")
                            data["snippet_type"] = creative.get("snippet_type")

                        if creative.get("template_variables"):
                            data["template_variables"] = creative.get("template_variables")

                        existing_creative.data = data

                        # Mark JSONB field as modified for SQLAlchemy
                        from sqlalchemy.orm import attributes

                        attributes.flag_modified(existing_creative, "data")

                    else:
                        # Create new creative
                        from src.core.database.models import Creative as DBCreative

                        # Prepare data field with all creative properties
                        data = {
                            "url": creative.get("url"),
                            "click_url": creative.get("click_url"),
                            "width": creative.get("width"),
                            "height": creative.get("height"),
                            "duration": creative.get("duration"),
                        }

                        # Add AdCP v1.3+ fields to data
                        if creative.get("snippet"):
                            data["snippet"] = creative.get("snippet")
                            data["snippet_type"] = creative.get("snippet_type")

                        if creative.get("template_variables"):
                            data["template_variables"] = creative.get("template_variables")

                        db_creative = DBCreative(
                            tenant_id=tenant["tenant_id"],
                            creative_id=creative.get("creative_id") or str(uuid.uuid4()),
                            name=creative.get("name"),
                            format=creative.get("format"),
                            principal_id=principal_id,
                            status="pending",
                            created_at=datetime.now(UTC),
                            data=data,
                        )

                        session.add(db_creative)
                        session.flush()  # Get the ID

                        # Update creative_id if it was generated
                        if not creative.get("creative_id"):
                            creative["creative_id"] = db_creative.creative_id

                    # Handle package assignments
                    if assign_to_packages and media_buy:
                        for package_id in assign_to_packages:
                            from src.core.database.models import CreativeAssignment as DBAssignment
                            from src.core.schemas import CreativeAssignment

                            assignment = DBAssignment(
                                tenant_id=tenant["tenant_id"],
                                assignment_id=str(uuid.uuid4()),
                                media_buy_id=media_buy.media_buy_id,
                                package_id=package_id,
                                creative_id=creative.get("creative_id"),
                                weight=100,
                                created_at=datetime.now(UTC),
                            )

                            session.add(assignment)
                            assignments.append(
                                CreativeAssignment(
                                    assignment_id=assignment.assignment_id,
                                    media_buy_id=assignment.media_buy_id,
                                    package_id=assignment.package_id,
                                    creative_id=assignment.creative_id,
                                    weight=assignment.weight,
                                )
                            )

                    # If we reach here, creative processing succeeded
                    synced_creatives.append(creative)

            except Exception as e:
                # Savepoint automatically rolls back this creative only
                failed_creatives.append(
                    {"creative_id": creative.get("creative_id"), "name": creative.get("name"), "error": str(e)}
                )

        # Commit all successful creative operations
        session.commit()

    # Audit logging
    audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
    audit_logger.log_operation(
        operation="sync_creatives",
        principal_name=principal_id,
        principal_id=principal_id,
        adapter_id="N/A",
        success=len(failed_creatives) == 0,
        details={
            "synced_count": len(synced_creatives),
            "failed_count": len(failed_creatives),
            "assignment_count": len(assignments),
            "upsert_mode": upsert,
        },
    )

    # Log activity
    log_tool_activity(context, "sync_creatives", start_time)

    message = f"Synced {len(synced_creatives)} creatives"
    if failed_creatives:
        message += f", {len(failed_creatives)} failed"
    if assignments:
        message += f", {len(assignments)} assignments created"

    # Convert synced creative dictionaries to schema objects for AdCP-compliant response
    synced_creative_schemas = []
    for creative_dict in synced_creatives:
        # Get the database object to populate internal fields
        with get_db_session() as session:
            from src.core.database.models import Creative as DBCreative

            db_creative = (
                session.query(DBCreative)
                .filter_by(tenant_id=tenant["tenant_id"], creative_id=creative_dict.get("creative_id"))
                .first()
            )
            if db_creative:
                # Create schema object with populated internal fields
                # Using aliased field names for construction
                # Handle mutually exclusive media content vs snippet
                schema_data = {
                    "creative_id": db_creative.creative_id,
                    "name": db_creative.name,
                    "format_id": db_creative.format,  # Use correct field name
                    "click_through_url": db_creative.data.get("click_url"),  # From data field
                    "width": db_creative.data.get("width"),
                    "height": db_creative.data.get("height"),
                    "duration": db_creative.data.get("duration"),
                    "status": db_creative.status,
                    "template_variables": db_creative.data.get("template_variables") or {},
                    "principal_id": db_creative.principal_id,
                    "created_at": db_creative.created_at or datetime.now(UTC),
                    "updated_at": db_creative.updated_at or datetime.now(UTC),
                }

                # Handle content_uri - required field even for snippet creatives
                # For snippet creatives, provide an HTML-looking URL to pass validation
                if db_creative.data.get("snippet"):
                    schema_data.update(
                        {
                            "snippet": db_creative.data.get("snippet"),
                            "snippet_type": db_creative.data.get("snippet_type"),
                            # Use HTML snippet-looking URL to pass _is_html_snippet() validation
                            "content_uri": db_creative.data.get("url")
                            or "<script>/* Snippet-based creative */</script>",
                        }
                    )
                else:
                    schema_data["content_uri"] = (
                        db_creative.data.get("url") or "https://placeholder.example.com/missing.jpg"
                    )

                creative_schema = Creative(**schema_data)
                synced_creative_schemas.append(creative_schema)

    return SyncCreativesResponse(
        synced_creatives=synced_creative_schemas,
        failed_creatives=failed_creatives,
        assignments=assignments,
        message=message,
    )


@mcp.tool
def list_creatives(
    media_buy_id: str = None,
    buyer_ref: str = None,
    status: str = None,
    format: str = None,
    tags: list[str] = None,
    created_after: str = None,
    created_before: str = None,
    search: str = None,
    page: int = 1,
    limit: int = 50,
    sort_by: str = "created_date",
    sort_order: str = "desc",
    context: Context = None,
) -> ListCreativesResponse:
    """List and search creative library (AdCP spec endpoint).

    Advanced filtering and search endpoint for the centralized creative library.
    Supports pagination, sorting, and multiple filter criteria.

    Args:
        media_buy_id: Filter by media buy ID (optional)
        buyer_ref: Filter by buyer reference (optional)
        status: Filter by creative status (pending, approved, rejected) (optional)
        format: Filter by creative format (optional)
        tags: Filter by tags (optional)
        created_after: Filter by creation date (ISO string) (optional)
        created_before: Filter by creation date (ISO string) (optional)
        search: Search in creative names and descriptions (optional)
        page: Page number for pagination (default: 1)
        limit: Number of results per page (default: 50, max: 1000)
        sort_by: Sort field (created_date, name, status) (default: created_date)
        sort_order: Sort order (asc, desc) (default: desc)
        context: FastMCP context (automatically provided)

    Returns:
        ListCreativesResponse with filtered creative assets and pagination info
    """
    from src.core.schemas import Creative, ListCreativesRequest

    # Parse datetime strings if provided
    created_after_dt = None
    created_before_dt = None
    if created_after:
        try:
            created_after_dt = datetime.fromisoformat(created_after.replace("Z", "+00:00"))
        except ValueError:
            raise ToolError(f"Invalid created_after date format: {created_after}")
    if created_before:
        try:
            created_before_dt = datetime.fromisoformat(created_before.replace("Z", "+00:00"))
        except ValueError:
            raise ToolError(f"Invalid created_before date format: {created_before}")

    # Create request object from individual parameters (MCP-compliant)
    req = ListCreativesRequest(
        media_buy_id=media_buy_id,
        buyer_ref=buyer_ref,
        status=status,
        format=format,
        tags=tags or [],
        created_after=created_after_dt,
        created_before=created_before_dt,
        search=search,
        page=page,
        limit=min(limit, 1000),  # Enforce max limit
        sort_by=sort_by,
        sort_order=sort_order,
    )

    start_time = time.time()

    # Authentication
    principal_id = _get_principal_id_from_context(context)

    # Get tenant information
    tenant = get_current_tenant()
    if not tenant:
        raise ToolError("No tenant context available")

    creatives = []
    total_count = 0

    with get_db_session() as session:
        from src.core.database.models import Creative as DBCreative
        from src.core.database.models import CreativeAssignment as DBAssignment
        from src.core.database.models import MediaBuy

        # Build query
        query = session.query(DBCreative).filter_by(tenant_id=tenant["tenant_id"])

        # Apply filters
        if req.media_buy_id:
            # Filter by media buy assignments
            query = query.join(DBAssignment, DBCreative.creative_id == DBAssignment.creative_id).filter(
                DBAssignment.media_buy_id == req.media_buy_id
            )

        if req.buyer_ref:
            # Filter by buyer_ref through media buy
            query = (
                query.join(DBAssignment, DBCreative.creative_id == DBAssignment.creative_id)
                .join(MediaBuy, DBAssignment.media_buy_id == MediaBuy.media_buy_id)
                .filter(MediaBuy.buyer_ref == req.buyer_ref)
            )

        if req.status:
            query = query.filter(DBCreative.status == req.status)

        if req.format:
            query = query.filter(DBCreative.format == req.format)

        if req.tags:
            # Simple tag filtering - in production, might use JSON operators
            for tag in req.tags:
                query = query.filter(DBCreative.name.contains(tag))  # Simplified

        if req.created_after:
            query = query.filter(DBCreative.created_at >= req.created_after)

        if req.created_before:
            query = query.filter(DBCreative.created_at <= req.created_before)

        if req.search:
            # Search in name and description
            search_term = f"%{req.search}%"
            query = query.filter(DBCreative.name.ilike(search_term))

        # Get total count before pagination
        total_count = query.count()

        # Apply sorting
        if req.sort_by == "name":
            sort_column = DBCreative.name
        elif req.sort_by == "status":
            sort_column = DBCreative.status
        else:  # Default to created_date
            sort_column = DBCreative.created_at

        if req.sort_order == "asc":
            query = query.order_by(sort_column.asc())
        else:
            query = query.order_by(sort_column.desc())

        # Apply pagination
        offset = (req.page - 1) * req.limit
        db_creatives = query.offset(offset).limit(req.limit).all()

        # Convert to schema objects
        for db_creative in db_creatives:
            # Create schema object with correct field names and data field access
            schema_data = {
                "creative_id": db_creative.creative_id,
                "name": db_creative.name,
                "format_id": db_creative.format,  # Use correct field name
                "click_through_url": db_creative.data.get("click_url") if db_creative.data else None,  # From data field
                "width": db_creative.data.get("width") if db_creative.data else None,
                "height": db_creative.data.get("height") if db_creative.data else None,
                "duration": db_creative.data.get("duration") if db_creative.data else None,
                "status": db_creative.status,
                "template_variables": db_creative.data.get("template_variables", {}) if db_creative.data else {},
                "principal_id": db_creative.principal_id,
                "created_at": db_creative.created_at or datetime.now(UTC),
                "updated_at": db_creative.updated_at or datetime.now(UTC),
            }

            # Handle content_uri - required field even for snippet creatives
            # For snippet creatives, provide an HTML-looking URL to pass validation
            snippet = db_creative.data.get("snippet") if db_creative.data else None
            if snippet:
                schema_data.update(
                    {
                        "snippet": snippet,
                        "snippet_type": db_creative.data.get("snippet_type") if db_creative.data else None,
                        # Use HTML snippet-looking URL to pass _is_html_snippet() validation
                        "content_uri": (
                            db_creative.data.get("url") or "<script>/* Snippet-based creative */</script>"
                            if db_creative.data
                            else "<script>/* Snippet-based creative */</script>"
                        ),
                    }
                )
            else:
                schema_data["content_uri"] = (
                    db_creative.data.get("url") or "https://placeholder.example.com/missing.jpg"
                    if db_creative.data
                    else "https://placeholder.example.com/missing.jpg"
                )

            creative = Creative(**schema_data)
            creatives.append(creative)

    # Calculate pagination info
    has_more = (req.page * req.limit) < total_count

    # Audit logging
    audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
    audit_logger.log_operation(
        operation="list_creatives",
        principal_name=principal_id,
        principal_id=principal_id,
        adapter_id="N/A",
        success=True,
        details={
            "result_count": len(creatives),
            "total_count": total_count,
            "page": req.page,
            "filters_applied": {
                "media_buy_id": req.media_buy_id,
                "status": req.status,
                "format": req.format,
                "search": req.search,
            },
        },
    )

    # Log activity
    log_tool_activity(context, "list_creatives", start_time)

    message = f"Found {len(creatives)} creatives"
    if total_count > len(creatives):
        message += f" (page {req.page} of {total_count} total)"

    return ListCreativesResponse(
        creatives=creatives, total_count=total_count, page=req.page, limit=req.limit, has_more=has_more, message=message
    )


@mcp.tool
async def get_signals(req: GetSignalsRequest, context: Context = None) -> GetSignalsResponse:
    """Optional endpoint for discovering available signals (audiences, contextual, etc.)

    Args:
        req: Request containing query parameters for signal discovery
        context: FastMCP context (automatically provided)

    Returns:
        GetSignalsResponse containing matching signals
    """

    _get_principal_id_from_context(context)

    # Get tenant information
    tenant = get_current_tenant()
    if not tenant:
        raise ToolError("No tenant context available")

    # Mock implementation - in production, this would query from a signal provider
    # or the ad server's available audience segments
    signals = []

    # Sample signals for demonstration
    sample_signals = [
        Signal(
            signal_id="auto_intenders_q1_2025",
            name="Auto Intenders Q1 2025",
            description="Users actively researching new vehicles in Q1 2025",
            type="audience",
            category="automotive",
            reach=2.5,
            cpm_uplift=3.0,
        ),
        Signal(
            signal_id="luxury_travel_enthusiasts",
            name="Luxury Travel Enthusiasts",
            description="High-income individuals interested in premium travel experiences",
            type="audience",
            category="travel",
            reach=1.2,
            cpm_uplift=5.0,
        ),
        Signal(
            signal_id="sports_content",
            name="Sports Content Pages",
            description="Target ads on sports-related content",
            type="contextual",
            category="sports",
            reach=15.0,
            cpm_uplift=1.5,
        ),
        Signal(
            signal_id="finance_content",
            name="Finance & Business Content",
            description="Target ads on finance and business content",
            type="contextual",
            category="finance",
            reach=8.0,
            cpm_uplift=2.0,
        ),
        Signal(
            signal_id="urban_millennials",
            name="Urban Millennials",
            description="Millennials living in major metropolitan areas",
            type="audience",
            category="demographic",
            reach=5.0,
            cpm_uplift=1.8,
        ),
        Signal(
            signal_id="pet_owners",
            name="Pet Owners",
            description="Households with dogs or cats",
            type="audience",
            category="lifestyle",
            reach=35.0,
            cpm_uplift=1.2,
        ),
    ]

    # Filter based on request parameters
    for signal in sample_signals:
        # Apply query filter
        if req.query:
            query_lower = req.query.lower()
            if (
                query_lower not in signal.name.lower()
                and query_lower not in signal.description.lower()
                and query_lower not in signal.category.lower()
            ):
                continue

        # Apply type filter
        if req.type and signal.type != req.type:
            continue

        # Apply category filter
        if req.category and signal.category != req.category:
            continue

        signals.append(signal)

    # Apply limit
    if req.limit:
        signals = signals[: req.limit]

    # Set status based on operation result
    status = TaskStatus.from_operation_state(
        operation_type="discovery", has_errors=False, requires_approval=False, requires_auth=False
    )

    return GetSignalsResponse(signals=signals, status=status)


@mcp.tool
async def activate_signal(
    signal_id: str,
    campaign_id: str = None,
    media_buy_id: str = None,
    context: Context = None,
) -> ActivateSignalResponse:
    """Activate a signal for use in campaigns.

    Args:
        signal_id: Signal ID to activate
        campaign_id: Optional campaign ID to activate signal for
        media_buy_id: Optional media buy ID to activate signal for
        context: FastMCP context (automatically provided)

    Returns:
        ActivateSignalResponse with activation status
    """
    start_time = time.time()

    # Authentication required for signal activation
    principal_id = _get_principal_id_from_context(context)

    # Get tenant information
    tenant = get_current_tenant()
    if not tenant:
        raise ToolError("No tenant context available")

    # Get the Principal object with ad server mappings
    principal = get_principal_object(principal_id)

    # Apply testing hooks
    testing_ctx = get_testing_context(context)
    campaign_info = {"endpoint": "activate_signal", "signal_id": signal_id}
    apply_testing_hooks(testing_ctx, campaign_info)

    try:
        # In a real implementation, this would:
        # 1. Validate the signal exists and is available
        # 2. Check if the principal has permission to activate the signal
        # 3. Communicate with the signal provider's API to activate the signal
        # 4. Update the campaign or media buy configuration to include the signal

        # Mock implementation for demonstration
        activation_success = True
        requires_approval = signal_id.startswith("premium_")  # Mock rule: premium signals need approval

        if requires_approval:
            # Create a human task for approval
            status = TaskStatus.INPUT_REQUIRED
            message = f"Signal {signal_id} requires manual approval before activation"
            activation_details = {
                "approval_required": True,
                "estimated_approval_time_hours": 24,
                "contact": "signals-approval@example.com",
            }
        elif activation_success:
            status = TaskStatus.WORKING  # Activation in progress
            message = f"Signal {signal_id} activation initiated successfully"
            activation_details = {
                "activation_started": datetime.now(UTC).isoformat(),
                "estimated_completion_minutes": 15,
                "platform_segment_id": f"seg_{signal_id}_{uuid.uuid4().hex[:8]}",
            }
        else:
            status = TaskStatus.FAILED
            message = f"Failed to activate signal {signal_id}"
            activation_details = {"error": "Signal provider unavailable"}

        # Log activity
        log_tool_activity(context, "activate_signal", start_time)

        return ActivateSignalResponse(
            signal_id=signal_id, status=status, message=message, activation_details=activation_details
        )

    except Exception as e:
        logger.error(f"Error activating signal {signal_id}: {e}")
        return ActivateSignalResponse(
            signal_id=signal_id,
            status=TaskStatus.FAILED,
            message=f"Failed to activate signal: {str(e)}",
            errors=[{"code": "ACTIVATION_ERROR", "message": str(e)}],
        )


@mcp.tool
def list_authorized_properties(
    req: ListAuthorizedPropertiesRequest = None, context: Context = None
) -> ListAuthorizedPropertiesResponse:
    """List all properties this agent is authorized to represent (AdCP spec endpoint).

    Discovers advertising properties (websites, apps, podcasts, etc.) that this
    sales agent is authorized to sell advertising on behalf of publishers.

    Args:
        req: Request parameters including optional tag filters
        context: FastMCP context for authentication

    Returns:
        ListAuthorizedPropertiesResponse with properties and tag metadata
    """
    start_time = time.time()

    # Handle missing request object (allows empty calls)
    if req is None:
        req = ListAuthorizedPropertiesRequest()

    # Get tenant and principal from context
    tenant = get_current_tenant()
    if not tenant:
        raise ToolError("AUTHENTICATION_ERROR", "Could not resolve tenant from context")

    tenant_id = tenant["tenant_id"]
    principal_id = _get_principal_id_from_context(context)

    # Apply testing hooks
    headers = context.meta.get("headers", {}) if context and context.meta else {}
    testing_context = get_testing_context(headers)
    campaign_info = {"endpoint": "list_authorized_properties", "tenant_id": tenant_id}
    apply_testing_hooks(testing_context, campaign_info, headers)

    log_tool_activity(context, "list_authorized_properties", start_time)

    try:
        with get_db_session() as session:
            # Query authorized properties for this tenant
            query = session.query(AuthorizedProperty).filter(AuthorizedProperty.tenant_id == tenant_id)

            # Apply tag filtering if requested
            if req.tags:
                # Filter properties that have any of the requested tags
                tag_filters = []
                for tag in req.tags:
                    tag_filters.append(AuthorizedProperty.tags.contains([tag]))
                query = query.filter(sa.or_(*tag_filters))

            # Only include verified properties
            query = query.filter(AuthorizedProperty.verification_status == "verified")

            authorized_properties = query.all()

            # Convert database models to Pydantic models
            properties = []
            all_tags = set()

            for prop in authorized_properties:
                # Extract identifiers from JSON
                identifiers = [
                    PropertyIdentifier(type=ident["type"], value=ident["value"]) for ident in (prop.identifiers or [])
                ]

                # Extract tags
                prop_tags = prop.tags or []
                all_tags.update(prop_tags)

                property_obj = Property(
                    property_type=prop.property_type,
                    name=prop.name,
                    identifiers=identifiers,
                    tags=prop_tags,
                    publisher_domain=prop.publisher_domain,
                )
                properties.append(property_obj)

            # Get tag metadata for all referenced tags
            tag_metadata = {}
            if all_tags:
                property_tags = (
                    session.query(PropertyTag)
                    .filter(PropertyTag.tenant_id == tenant_id, PropertyTag.tag_id.in_(all_tags))
                    .all()
                )

                for tag in property_tags:
                    tag_metadata[tag.tag_id] = PropertyTagMetadata(name=tag.name, description=tag.description)

            # Create response
            response = ListAuthorizedPropertiesResponse(
                adcp_version=req.adcp_version, properties=properties, tags=tag_metadata, errors=[]
            )

            # Log audit
            audit_logger = get_audit_logger()
            audit_logger.log_operation(
                operation="list_authorized_properties",
                tenant_id=tenant_id,
                principal_id=principal_id,
                success=True,
                details={
                    "properties_count": len(properties),
                    "requested_tags": req.tags,
                    "response_tags_count": len(tag_metadata),
                },
            )

            return response

    except Exception as e:
        logger.error(f"Error listing authorized properties: {str(e)}")

        # Log audit for failure
        audit_logger = get_audit_logger()
        audit_logger.log_operation(
            operation="list_authorized_properties",
            tenant_id=tenant_id,
            principal_id=principal_id,
            success=False,
            error_message=str(e),
        )

        raise ToolError("PROPERTIES_ERROR", f"Failed to list authorized properties: {str(e)}")


@mcp.tool
def create_media_buy(
    po_number: str,
    buyer_ref: str = None,
    packages: list = None,
    start_time: str = None,
    end_time: str = None,
    budget: dict = None,
    product_ids: list = None,
    start_date: str = None,
    end_date: str = None,
    total_budget: float = None,
    targeting_overlay: dict = None,
    pacing: str = "even",
    daily_budget: float = None,
    creatives: list = None,
    required_axe_signals: list = None,
    enable_creative_macro: bool = False,
    strategy_id: str = None,
    context: Context = None,
) -> CreateMediaBuyResponse:
    """Create a media buy with the specified parameters.

    Args:
        po_number: Purchase order number (required)
        buyer_ref: Buyer reference for tracking
        packages: Array of packages with products and budgets
        start_time: Campaign start time (ISO 8601)
        end_time: Campaign end time (ISO 8601)
        budget: Overall campaign budget
        product_ids: Legacy: Product IDs (converted to packages)
        start_date: Legacy: Start date (converted to start_time)
        end_date: Legacy: End date (converted to end_time)
        total_budget: Legacy: Total budget (converted to Budget object)
        targeting_overlay: Targeting overlay configuration
        pacing: Pacing strategy (even, asap, daily_budget)
        daily_budget: Daily budget limit
        creatives: Creative assets for the campaign
        required_axe_signals: Required targeting signals
        enable_creative_macro: Enable AXE to provide creative_macro signal
        strategy_id: Optional strategy ID for linking operations
        context: FastMCP context (automatically provided)

    Returns:
        CreateMediaBuyResponse with media buy details
    """
    request_start_time = time.time()

    # Create request object from individual parameters (MCP-compliant)
    req = CreateMediaBuyRequest(
        po_number=po_number,
        buyer_ref=buyer_ref,
        packages=packages,
        start_time=start_time,
        end_time=end_time,
        budget=budget,
        product_ids=product_ids,
        start_date=start_date,
        end_date=end_date,
        total_budget=total_budget,
        targeting_overlay=targeting_overlay,
        pacing=pacing,
        daily_budget=daily_budget,
        creatives=creatives,
        required_axe_signals=required_axe_signals,
        enable_creative_macro=enable_creative_macro,
        strategy_id=strategy_id,
    )

    # Extract testing context first
    testing_ctx = get_testing_context(context)

    # Authentication and tenant setup
    principal_id = _get_principal_id_from_context(context)
    tenant = get_current_tenant()

    # Context management and workflow step creation - create workflow step FIRST
    ctx_manager = get_context_manager()
    ctx_id = context.headers.get("x-context-id") if hasattr(context, "headers") else None
    persistent_ctx = None
    step = None

    # Create workflow step immediately for tracking all operations
    if not persistent_ctx:
        # Check if we have an existing context ID
        if ctx_id:
            persistent_ctx = ctx_manager.get_context(ctx_id)

        # Create new context if needed
        if not persistent_ctx:
            persistent_ctx = ctx_manager.create_context(tenant_id=tenant["tenant_id"], principal_id=principal_id)

    # Create workflow step for tracking this operation
    step = ctx_manager.create_workflow_step(
        context_id=persistent_ctx.context_id,
        step_type="media_buy_creation",
        owner="system",
        status="in_progress",
        tool_name="create_media_buy",
        request_data=req.model_dump(mode="json"),
    )

    try:
        # Validate input parameters
        # 1. Budget validation
        total_budget = req.get_total_budget()
        if total_budget <= 0:
            error_msg = f"Invalid budget: {total_budget}. Budget must be positive."
            raise ValueError(error_msg)

        # 2. DateTime validation
        from datetime import datetime

        now = datetime.now(UTC)

        if req.start_time < now:
            error_msg = f"Invalid start time: {req.start_time}. Start time cannot be in the past."
            raise ValueError(error_msg)

        if req.end_time <= req.start_time:
            error_msg = f"Invalid time range: end time ({req.end_time}) must be after start time ({req.start_time})."
            raise ValueError(error_msg)

        # 3. Package/Product validation
        product_ids = req.get_product_ids()
        if not product_ids:
            error_msg = "At least one product is required."
            raise ValueError(error_msg)

        if req.packages:
            for package in req.packages:
                if not package.products:
                    error_msg = f"Package {package.buyer_ref} must contain at least one product."
                    raise ValueError(error_msg)

        # Validate targeting doesn't use managed-only dimensions
        if req.targeting_overlay:
            from src.services.targeting_capabilities import validate_overlay_targeting

            violations = validate_overlay_targeting(req.targeting_overlay.model_dump(exclude_none=True))
            if violations:
                error_msg = f"Targeting validation failed: {'; '.join(violations)}"
                raise ValueError(error_msg)

    except (ValueError, PermissionError) as e:
        # Update workflow step as failed
        ctx_manager.update_workflow_step(step.step_id, status="failed", error=str(e))

        # Return proper error response instead of raising ToolError
        return CreateMediaBuyResponse(
            media_buy_id="",
            status=TaskStatus.FAILED,
            detail=str(e),
            creative_deadline=None,
            message=f"Media buy creation failed: {str(e)}",
            errors=[{"code": "validation_error", "message": str(e)}],
        )

    # Get the Principal object (needed for adapter)
    principal = get_principal_object(principal_id)
    if not principal:
        error_msg = f"Principal {principal_id} not found"
        ctx_manager.update_workflow_step(step.step_id, status="failed", error=error_msg)
        return CreateMediaBuyResponse(
            media_buy_id="",
            status=TaskStatus.FAILED,
            detail=error_msg,
            creative_deadline=None,
            message=f"Media buy creation failed: {error_msg}",
            errors=[{"code": "authentication_error", "message": error_msg}],
        )

    try:
        # Get the appropriate adapter with testing context
        adapter = get_adapter(principal, dry_run=DRY_RUN_MODE or testing_ctx.dry_run, testing_context=testing_ctx)

        # Check if manual approval is required
        manual_approval_required = (
            adapter.manual_approval_required if hasattr(adapter, "manual_approval_required") else False
        )
        manual_approval_operations = (
            adapter.manual_approval_operations if hasattr(adapter, "manual_approval_operations") else []
        )

        # Check if auto-creation is disabled in tenant config
        auto_create_enabled = tenant.get("auto_create_media_buys", True)
        product_auto_create = True  # Will be set correctly when we get products later

        if manual_approval_required and "create_media_buy" in manual_approval_operations:
            # Update existing workflow step to require approval
            ctx_manager.update_workflow_step(
                step.step_id, status="requires_approval", step_type="approval", owner="publisher"
            )

            # Workflow step already created above - no need for separate task
            pending_media_buy_id = f"pending_{uuid.uuid4().hex[:8]}"

            response_msg = (
                f"Manual approval required. Workflow Step ID: {step.step_id}. Context ID: {persistent_ctx.context_id}"
            )
            ctx_manager.add_message(persistent_ctx.context_id, "assistant", response_msg)

            return CreateMediaBuyResponse(
                media_buy_id=pending_media_buy_id,
                status=TaskStatus.INPUT_REQUIRED,
                detail=response_msg,
                creative_deadline=None,
                message="Your media buy request requires manual approval from the publisher. The request has been queued and will be reviewed shortly.",
            )

        # Get products for the media buy to check product-level auto-creation settings
        catalog = get_product_catalog()
        product_ids = req.get_product_ids()
        products_in_buy = [p for p in catalog if p.product_id in product_ids]
        product_auto_create = all(p.get("auto_create_enabled", True) for p in products_in_buy)

        # Check if either tenant or product disables auto-creation
        if not auto_create_enabled or not product_auto_create:
            reason = "Tenant configuration" if not auto_create_enabled else "Product configuration"

            # Update existing workflow step to require approval
            ctx_manager.update_workflow_step(
                step.step_id, status="requires_approval", step_type="approval", owner="publisher"
            )

            # Workflow step already created above - no need for separate task
            pending_media_buy_id = f"pending_{uuid.uuid4().hex[:8]}"

            response_msg = f"Media buy requires approval due to {reason.lower()}. Workflow Step ID: {step.step_id}. Context ID: {persistent_ctx.context_id}"
            ctx_manager.add_message(persistent_ctx.context_id, "assistant", response_msg)

            return CreateMediaBuyResponse(
                media_buy_id=pending_media_buy_id,
                status=TaskStatus.INPUT_REQUIRED,
                detail=response_msg,
                creative_deadline=None,
                message=f"This media buy requires manual approval due to {reason.lower()}. Your request has been submitted for review.",
            )

        # Continue with synchronized media buy creation

        # Note: products_in_buy was already calculated above for product_auto_create check
        # No need to recalculate

        # Note: Key-value pairs are NOT aggregated here anymore.
        # Each product maintains its own custom_targeting_keys in implementation_config
        # which will be applied separately to its corresponding line item in GAM.
        # The adapter (google_ad_manager.py) handles this per-product targeting at line 491-494

        # Convert products to MediaPackages (simplified for now)
        packages = []
        for product in products_in_buy:
            # Use the first format for now
            first_format_id = product.formats[0] if product.formats else None
            packages.append(
                MediaPackage(
                    package_id=product.product_id,
                    name=product.name,
                    delivery_type=product.delivery_type,
                    cpm=product.cpm if product.cpm else 10.0,  # Default CPM
                    impressions=int(total_budget / (product.cpm if product.cpm else 10.0) * 1000),
                    format_ids=[first_format_id] if first_format_id else [],
                )
            )

        # Create the media buy using the adapter (SYNCHRONOUS operation)
        response = adapter.create_media_buy(req, packages, req.start_time, req.end_time)

        # Store the media buy in memory (for backward compatibility)
        media_buys[response.media_buy_id] = (req, principal_id)

        # Store the media buy in database (context_id is NULL for synchronous operations)
        tenant = get_current_tenant()
        with get_db_session() as session:
            new_media_buy = MediaBuy(
                media_buy_id=response.media_buy_id,
                tenant_id=tenant["tenant_id"],
                principal_id=principal_id,
                buyer_ref=req.buyer_ref,  # AdCP v2.4 buyer reference
                order_name=req.po_number or f"Order-{response.media_buy_id}",
                advertiser_name=principal.name,
                campaign_objective=getattr(req, "campaign_objective", ""),  # Optional field
                kpi_goal=getattr(req, "kpi_goal", ""),  # Optional field
                budget=total_budget,  # Extract total budget
                currency=req.budget.currency if req.budget else "USD",  # AdCP v2.4 currency field
                start_date=req.start_time.date(),  # Legacy field for compatibility
                end_date=req.end_time.date(),  # Legacy field for compatibility
                start_time=req.start_time,  # AdCP v2.4 datetime scheduling
                end_time=req.end_time,  # AdCP v2.4 datetime scheduling
                status=response.status or TaskStatus.WORKING,
                raw_request=req.model_dump(mode="json"),
            )
            session.add(new_media_buy)
            session.commit()

        # Handle creatives if provided
        if req.creatives:
            # Convert Creative objects to format expected by adapter
            assets = []
            for creative in req.creatives:
                try:
                    asset = _convert_creative_to_adapter_asset(creative, req.product_ids)
                    assets.append(asset)
                except Exception as e:
                    console.print(f"[red]Error converting creative {creative.creative_id}: {e}[/red]")
                    # Add a failed status for this creative
                    creative_statuses[creative.creative_id] = CreativeStatus(
                        creative_id=creative.creative_id, status="rejected", detail=f"Conversion error: {str(e)}"
                    )
                    continue
            statuses = adapter.add_creative_assets(response.media_buy_id, assets, datetime.now())
            for status in statuses:
                creative_statuses[status.creative_id] = CreativeStatus(
                    creative_id=status.creative_id,
                    status="approved" if status.status == "approved" else "pending_review",
                    detail="Creative submitted to ad server",
                )

        # Build packages list for response (AdCP v2.4 format)
        response_packages = []
        for i, package in enumerate(req.packages):
            response_packages.append(
                {
                    "package_id": f"{response.media_buy_id}_pkg_{i+1}",
                    "buyer_ref": package.buyer_ref,
                    "products": package.products,
                    "status": TaskStatus.WORKING,
                }
            )

        # Create AdCP v2.4 compliant response
        adcp_response = CreateMediaBuyResponse(
            media_buy_id=response.media_buy_id,
            buyer_ref=req.buyer_ref,
            status=TaskStatus.WORKING,  # Media buy creation in progress (async operation)
            packages=response_packages,
            creative_deadline=response.creative_deadline,
            message="Media buy created successfully and is being activated",
        )

        # Log activity
        log_tool_activity(context, "create_media_buy", start_time)

        # Also log specific media buy activity
        try:
            principal_name = "Unknown"
            with get_db_session() as session:
                principal_db = (
                    session.query(ModelPrincipal)
                    .filter_by(principal_id=principal_id, tenant_id=tenant["tenant_id"])
                    .first()
                )
                if principal_db:
                    principal_name = principal_db.name

            # Calculate duration using new datetime fields
            duration_days = (req.end_time - req.start_time).days + 1

            activity_feed.log_media_buy(
                tenant_id=tenant["tenant_id"],
                principal_name=principal_name,
                media_buy_id=response.media_buy_id,
                budget=total_budget,  # Extract total budget
                duration_days=duration_days,
                action="created",
            )
        except:
            pass

        # Apply testing hooks to response with campaign information
        campaign_info = {"start_date": req.start_time, "end_date": req.end_time, "total_budget": total_budget}

        response_data = adcp_response.model_dump()
        response_data = apply_testing_hooks(response_data, testing_ctx, "create_media_buy", campaign_info)

        # Reconstruct response from modified data
        modified_response = CreateMediaBuyResponse(**response_data)

        # Mark workflow step as completed on success
        ctx_manager.update_workflow_step(step.step_id, status="completed")

        return modified_response

    except Exception as e:
        # Update workflow step as failed on any error during execution
        if step:
            ctx_manager.update_workflow_step(step.step_id, status="failed", error=str(e))

        # Return proper error response instead of raising ToolError
        return CreateMediaBuyResponse(
            media_buy_id="",
            status=TaskStatus.FAILED,
            detail=str(e),
            creative_deadline=None,
            message=f"Media buy creation failed: {str(e)}",
            errors=[{"code": "execution_error", "message": str(e)}],
        )


# Unified update tools
@mcp.tool
def update_media_buy(
    media_buy_id: str,
    buyer_ref: str = None,
    active: bool = None,
    flight_start_date: str = None,
    flight_end_date: str = None,
    budget: float = None,
    currency: str = None,
    targeting_overlay: dict = None,
    start_time: str = None,
    end_time: str = None,
    pacing: str = None,
    daily_budget: float = None,
    packages: list = None,
    creatives: list = None,
    context: Context = None,
) -> UpdateMediaBuyResponse:
    """Update a media buy with campaign-level and/or package-level changes.

    Args:
        media_buy_id: Media buy ID to update (required)
        buyer_ref: Update buyer reference
        active: True to activate, False to pause entire campaign
        flight_start_date: Change start date (if not started)
        flight_end_date: Extend or shorten campaign
        budget: Update total budget
        currency: Update currency (ISO 4217)
        targeting_overlay: Update global targeting
        start_time: Update start datetime
        end_time: Update end datetime
        pacing: Pacing strategy (even, asap, daily_budget)
        daily_budget: Daily spend cap across all packages
        packages: Package-specific updates
        creatives: Add new creatives
        context: FastMCP context (automatically provided)

    Returns:
        UpdateMediaBuyResponse with updated media buy details
    """
    # Create request object from individual parameters (MCP-compliant)
    req = UpdateMediaBuyRequest(
        media_buy_id=media_buy_id,
        buyer_ref=buyer_ref,
        active=active,
        flight_start_date=flight_start_date,
        flight_end_date=flight_end_date,
        budget=budget,
        currency=currency,
        targeting_overlay=targeting_overlay,
        start_time=start_time,
        end_time=end_time,
        pacing=pacing,
        daily_budget=daily_budget,
        packages=packages,
        creatives=creatives,
    )

    _verify_principal(req.media_buy_id, context)
    _, principal_id = media_buys[req.media_buy_id]
    tenant = get_current_tenant()

    # Create or get persistent context
    ctx_manager = get_context_manager()
    ctx_id = context.headers.get("x-context-id") if hasattr(context, "headers") else None
    persistent_ctx = ctx_manager.get_or_create_context(
        tenant_id=tenant["tenant_id"],
        principal_id=principal_id,
        context_id=ctx_id,
        is_async=True,
    )

    # Create workflow step for this tool call
    step = ctx_manager.create_workflow_step(
        context_id=persistent_ctx.context_id,
        step_type="tool_call",
        owner="principal",
        status="in_progress",
        tool_name="update_media_buy",
        request_data=req.model_dump(mode="json"),  # Convert dates to strings
    )

    principal = get_principal_object(principal_id)
    if not principal:
        error_msg = f"Principal {principal_id} not found"
        ctx_manager.update_workflow_step(step.step_id, status="failed", error=error_msg)
        return UpdateMediaBuyResponse(
            status="failed",
            message=f"Update failed: {error_msg}",
            errors=[{"code": "principal_not_found", "message": error_msg}],
        )

    adapter = get_adapter(principal, dry_run=DRY_RUN_MODE)
    today = req.today or date.today()

    # Check if manual approval is required
    manual_approval_required = (
        adapter.manual_approval_required if hasattr(adapter, "manual_approval_required") else False
    )
    manual_approval_operations = (
        adapter.manual_approval_operations if hasattr(adapter, "manual_approval_operations") else []
    )

    if manual_approval_required and "update_media_buy" in manual_approval_operations:
        # Workflow step already created above - update its status
        ctx_manager.update_workflow_step(
            step.step_id,
            status="requires_approval",
            add_comment={"user": "system", "comment": "Publisher requires manual approval for all media buy updates"},
        )

        return UpdateMediaBuyResponse(
            status="pending_manual",
            detail=f"Manual approval required. Workflow Step ID: {step.step_id}",
        )

    # Handle campaign-level updates
    if req.active is not None:
        action = "resume_media_buy" if req.active else "pause_media_buy"
        result = adapter.update_media_buy(
            media_buy_id=req.media_buy_id,
            action=action,
            package_id=None,
            budget=None,
            today=datetime.combine(today, datetime.min.time()),
        )
        if result.status == "failed":
            return result

    # Handle package-level updates
    if req.packages:
        for pkg_update in req.packages:
            # Handle active/pause state
            if pkg_update.active is not None:
                action = "resume_package" if pkg_update.active else "pause_package"
                result = adapter.update_media_buy(
                    media_buy_id=req.media_buy_id,
                    action=action,
                    package_id=pkg_update.package_id,
                    budget=None,
                    today=datetime.combine(today, datetime.min.time()),
                )
                if result.status == "failed":
                    ctx_manager.update_workflow_step(
                        step.step_id,
                        status="failed",
                        error_message=result.detail or "Update failed",
                    )
                    return result

            # Handle budget updates
            if pkg_update.impressions is not None:
                result = adapter.update_media_buy(
                    media_buy_id=req.media_buy_id,
                    action="update_package_impressions",
                    package_id=pkg_update.package_id,
                    budget=pkg_update.impressions,
                    today=datetime.combine(today, datetime.min.time()),
                )
                if result.status == "failed":
                    ctx_manager.update_workflow_step(
                        step.step_id,
                        status="failed",
                        error_message=result.detail or "Update failed",
                    )
                    return result
            elif pkg_update.budget is not None:
                result = adapter.update_media_buy(
                    media_buy_id=req.media_buy_id,
                    action="update_package_budget",
                    package_id=pkg_update.package_id,
                    budget=int(pkg_update.budget),
                    today=datetime.combine(today, datetime.min.time()),
                )
                if result.status == "failed":
                    ctx_manager.update_workflow_step(
                        step.step_id,
                        status="failed",
                        error_message=result.detail or "Update failed",
                    )
                    return result

    # Handle budget updates (support both Budget object and float)
    if req.budget is not None:
        if isinstance(req.budget, dict):
            # Handle Budget object
            total_budget = req.budget.get("total", 0)
            currency = req.budget.get("currency", "USD")
        elif hasattr(req.budget, "total"):
            # Handle Budget model instance
            total_budget = req.budget.total
            currency = req.budget.currency
        else:
            # Handle float
            total_budget = req.budget
            currency = req.currency or "USD"

        if total_budget <= 0:
            error_msg = f"Invalid budget: {total_budget}. Budget must be positive."
            ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
            return UpdateMediaBuyResponse(status="failed", detail=error_msg)

        # Store budget update in media buy
        if req.media_buy_id in media_buys:
            buy_data = media_buys[req.media_buy_id]
            if isinstance(buy_data, tuple) and len(buy_data) >= 2:
                # Update with new budget info
                media_buys[req.media_buy_id] = (
                    {
                        "budget": total_budget,
                        "currency": currency,
                        "buyer_ref": req.buyer_ref or buy_data[0].get("buyer_ref"),
                    },
                    buy_data[1],  # Keep principal_id
                )

    # Validate update parameters (backwards compatibility)
    if req.total_budget is not None and req.total_budget <= 0:
        error_msg = f"Invalid budget: {req.total_budget}. Budget must be positive."
        ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
        return UpdateMediaBuyResponse(status="failed", detail=error_msg)

    buy_request, _ = media_buys[req.media_buy_id]
    if req.total_budget is not None:
        buy_request.total_budget = req.total_budget
    if req.targeting_overlay is not None:
        # Validate targeting doesn't use managed-only dimensions
        from src.services.targeting_capabilities import validate_overlay_targeting

        violations = validate_overlay_targeting(req.targeting_overlay.model_dump(exclude_none=True))
        if violations:
            error_msg = f"Targeting validation failed: {'; '.join(violations)}"
            ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
            return UpdateMediaBuyResponse(status="failed", detail=error_msg)
        buy_request.targeting_overlay = req.targeting_overlay
    if req.creative_assignments:
        creative_assignments[req.media_buy_id] = req.creative_assignments

    # Update workflow step with success
    ctx_manager.update_workflow_step(
        step.step_id,
        status="completed",
        response_data={
            "status": "accepted",
            "updates_applied": {
                "campaign_level": req.active is not None,
                "package_count": len(req.packages) if req.packages else 0,
                "total_budget": req.total_budget is not None,
                "targeting": req.targeting_overlay is not None,
            },
        },
    )

    return UpdateMediaBuyResponse(
        status="accepted",
        implementation_date=datetime.combine(today, datetime.min.time()),
        detail="Media buy updated successfully",
    )


def _get_media_buy_delivery_impl(req: GetMediaBuyDeliveryRequest, context: Context) -> GetMediaBuyDeliveryResponse:
    """Get delivery data for one or more media buys.

    Supports:
    - Single buy: media_buy_ids=["buy_123"]
    - Multiple buys: media_buy_ids=["buy_123", "buy_456"]
    - All active buys: filter="active" (default)
    - All buys: filter="all"
    """
    # Extract testing context for time simulation and event jumping
    testing_ctx = get_testing_context(context)

    principal_id = _get_principal_id_from_context(context)

    # Get the Principal object
    principal = get_principal_object(principal_id)
    if not principal:
        return GetMediaBuysResponse(
            media_buys=[],
            status="failed",
            message=f"Principal {principal_id} not found",
            errors=[{"code": "principal_not_found", "message": f"Principal {principal_id} not found"}],
        )

    # Get the appropriate adapter
    adapter = get_adapter(principal, dry_run=DRY_RUN_MODE)

    # Determine which media buys to fetch
    target_media_buys = []

    if req.media_buy_ids:
        # Specific media buy IDs requested
        for media_buy_id in req.media_buy_ids:
            if media_buy_id in media_buys:
                buy_request, buy_principal_id = media_buys[media_buy_id]
                if buy_principal_id == principal_id:
                    target_media_buys.append((media_buy_id, buy_request))
                else:
                    console.print(f"[yellow]Skipping {media_buy_id} - not owned by principal[/yellow]")
            else:
                console.print(f"[yellow]Media buy {media_buy_id} not found[/yellow]")
    else:
        # Use status_filter to determine which buys to fetch
        for media_buy_id, (buy_request, buy_principal_id) in media_buys.items():
            if buy_principal_id == principal_id:
                # Apply status filter
                if req.status_filter == "all":
                    target_media_buys.append((media_buy_id, buy_request))
                elif req.status_filter == "completed":
                    if req.today > buy_request.flight_end_date:
                        target_media_buys.append((media_buy_id, buy_request))
                else:  # "active" (default)
                    if buy_request.flight_start_date <= req.today <= buy_request.flight_end_date:
                        target_media_buys.append((media_buy_id, buy_request))

    # Collect delivery data for each media buy
    deliveries = []
    total_spend = 0.0
    total_impressions = 0
    active_count = 0

    for media_buy_id, buy_request in target_media_buys:
        # Create a ReportingPeriod for the adapter
        reporting_period = ReportingPeriod(
            start=datetime.combine(req.today - timedelta(days=1), datetime.min.time()),
            end=datetime.combine(req.today, datetime.min.time()),
            start_date=req.today - timedelta(days=1),
            end_date=req.today,
        )

        try:
            # Apply time simulation from testing context
            simulation_datetime = datetime.combine(req.today, datetime.min.time())
            if testing_ctx.mock_time:
                simulation_datetime = testing_ctx.mock_time
            elif testing_ctx.jump_to_event:
                # Calculate time based on event
                simulation_datetime = TimeSimulator.jump_to_event_time(
                    testing_ctx.jump_to_event,
                    datetime.combine(buy_request.flight_start_date, datetime.min.time()),
                    datetime.combine(buy_request.flight_end_date, datetime.min.time()),
                )

            # Get delivery data from the adapter
            delivery_response = adapter.get_media_buy_delivery(media_buy_id, reporting_period, simulation_datetime)

            # Apply testing hooks for enhanced simulation
            if any(
                [testing_ctx.dry_run, testing_ctx.mock_time, testing_ctx.jump_to_event, testing_ctx.test_session_id]
            ):
                # Calculate campaign progress based on simulated time
                start_dt = datetime.combine(buy_request.flight_start_date, datetime.min.time())
                end_dt = datetime.combine(buy_request.flight_end_date, datetime.min.time())
                progress = TimeSimulator.calculate_campaign_progress(start_dt, end_dt, simulation_datetime)

                # Generate simulated metrics
                simulated_metrics = DeliverySimulator.calculate_simulated_metrics(
                    buy_request.total_budget, progress, testing_ctx
                )

                spend = simulated_metrics["spend"]
                impressions = simulated_metrics["impressions"]
                status = simulated_metrics["status"]

                # Calculate days based on simulated time
                days_elapsed = max(0, (simulation_datetime.date() - buy_request.flight_start_date).days)
                total_days = (buy_request.flight_end_date - buy_request.flight_start_date).days

                # Determine pacing from simulation
                expected_spend = (buy_request.total_budget / total_days) * days_elapsed if total_days > 0 else 0
                if spend > expected_spend * 1.1:
                    pacing = "ahead"
                elif spend < expected_spend * 0.9:
                    pacing = "behind"
                else:
                    pacing = "on_track"

            else:
                # Normal adapter response processing
                spend = delivery_response.totals.spend if hasattr(delivery_response, "totals") else 0
                impressions = delivery_response.totals.impressions if hasattr(delivery_response, "totals") else 0

                # Calculate days elapsed
                days_elapsed = (req.today - buy_request.flight_start_date).days
                total_days = (buy_request.flight_end_date - buy_request.flight_start_date).days

                # Determine pacing
                expected_spend = (buy_request.total_budget / total_days) * days_elapsed if total_days > 0 else 0
                if spend > expected_spend * 1.1:
                    pacing = "ahead"
                elif spend < expected_spend * 0.9:
                    pacing = "behind"
                else:
                    pacing = "on_track"

                # Determine status
                if req.today < buy_request.flight_start_date:
                    status = "pending_start"
                elif req.today > buy_request.flight_end_date:
                    status = "completed"
                else:
                    status = "delivering"

            if status == "delivering" or status == "active":
                active_count += 1

            # Add to deliveries list
            deliveries.append(
                MediaBuyDeliveryData(
                    media_buy_id=media_buy_id,
                    status=status,
                    spend=spend,
                    impressions=impressions,
                    pacing=pacing,
                    days_elapsed=days_elapsed,
                    total_days=total_days,
                )
            )

            # Update totals
            total_spend += spend
            total_impressions += impressions

        except Exception as e:
            console.print(f"[red]Error getting delivery for {media_buy_id}: {e}[/red]")
            # Continue with other media buys

    # Apply testing hooks to response with campaign information
    campaign_info = None
    if target_media_buys:
        # Use the first media buy for campaign timing info
        first_buy = target_media_buys[0][1]  # (media_buy_id, buy_request)
        campaign_info = {
            "start_date": datetime.combine(first_buy.flight_start_date, datetime.min.time()),
            "end_date": datetime.combine(first_buy.flight_end_date, datetime.min.time()),
            "total_budget": (
                first_buy.get_total_budget()
                if hasattr(first_buy, "get_total_budget")
                else getattr(first_buy, "total_budget", 0)
            ),
        }

    response_data = {
        "deliveries": [d.model_dump() for d in deliveries],
        "total_spend": total_spend,
        "total_impressions": total_impressions,
        "active_count": active_count,
        "summary_date": req.today,
    }
    response_data = apply_testing_hooks(response_data, testing_ctx, "get_media_buy_delivery", campaign_info)

    # Reconstruct deliveries from modified data
    modified_deliveries = [MediaBuyDeliveryData(**d) for d in response_data["deliveries"]]

    return GetMediaBuyDeliveryResponse(
        deliveries=modified_deliveries,
        total_spend=response_data["total_spend"],
        total_impressions=response_data["total_impressions"],
        active_count=response_data["active_count"],
        summary_date=response_data["summary_date"],
    )


@mcp.tool
def get_media_buy_delivery(
    today: date,
    media_buy_ids: list[str] = None,
    buyer_refs: list[str] = None,
    status_filter: str = "active",
    strategy_id: str = None,
    context: Context = None,
) -> GetMediaBuyDeliveryResponse:
    """Get delivery data for media buys.

    Args:
        today: Reference date for calculating delivery metrics
        media_buy_ids: Specific media buy IDs to fetch (optional)
        buyer_refs: Alternative: specify buyer references instead of media buy IDs (optional)
        status_filter: Filter for which buys to fetch when IDs/refs not provided ('active', 'all', 'completed')
        strategy_id: Optional strategy ID for consistent simulation/testing context
        context: FastMCP context (automatically provided)

    Returns:
        GetMediaBuyDeliveryResponse with delivery data for the requested media buys
    """
    # Create request object from individual parameters (MCP-compliant)
    req = GetMediaBuyDeliveryRequest(
        media_buy_ids=media_buy_ids,
        buyer_refs=buyer_refs,
        status_filter=status_filter,
        today=today,
        strategy_id=strategy_id,
    )

    return _get_media_buy_delivery_impl(req, context)


# --- Admin Tools ---


def _require_admin(context: Context) -> None:
    """Verify the request is from an admin user."""
    principal_id = get_principal_from_context(context)
    if principal_id != "admin":
        raise PermissionError("This operation requires admin privileges")


@mcp.tool
def update_performance_index(
    media_buy_id: str, performance_data: list[dict[str, Any]], context: Context = None
) -> UpdatePerformanceIndexResponse:
    """Update performance index data for a media buy.

    Args:
        media_buy_id: ID of the media buy to update
        performance_data: List of performance data objects
        context: FastMCP context (automatically provided)

    Returns:
        UpdatePerformanceIndexResponse with operation status
    """
    # Create request object from individual parameters (MCP-compliant)
    # Convert dict performance_data to ProductPerformance objects
    from src.core.schemas import ProductPerformance

    performance_objects = [ProductPerformance(**perf) for perf in performance_data]
    req = UpdatePerformanceIndexRequest(media_buy_id=media_buy_id, performance_data=performance_objects)

    _verify_principal(req.media_buy_id, context)
    buy_request, principal_id = media_buys[req.media_buy_id]

    # Get the Principal object
    principal = get_principal_object(principal_id)
    if not principal:
        return UpdatePerformanceIndexResponse(
            status="failed",
            message=f"Principal {principal_id} not found",
            errors=[{"code": "principal_not_found", "message": f"Principal {principal_id} not found"}],
        )

    # Get the appropriate adapter
    adapter = get_adapter(principal, dry_run=DRY_RUN_MODE)

    # Convert ProductPerformance to PackagePerformance for the adapter
    package_performance = [
        PackagePerformance(package_id=perf.product_id, performance_index=perf.performance_index)
        for perf in req.performance_data
    ]

    # Call the adapter's update method
    success = adapter.update_media_buy_performance_index(req.media_buy_id, package_performance)

    # Log the performance update
    console.print(f"[bold green]Performance Index Update for {req.media_buy_id}:[/bold green]")
    for perf in req.performance_data:
        status_emoji = "📈" if perf.performance_index > 1.0 else "📉" if perf.performance_index < 1.0 else "➡️"
        console.print(
            f"  {status_emoji} {perf.product_id}: {perf.performance_index:.2f} (confidence: {perf.confidence_score or 'N/A'})"
        )

    # Simulate optimization based on performance
    if any(p.performance_index < 0.8 for p in req.performance_data):
        console.print("  [yellow]⚠️  Low performance detected - optimization recommended[/yellow]")

    return UpdatePerformanceIndexResponse(
        status="success" if success else "failed",
        detail=f"Performance index updated for {len(req.performance_data)} products",
    )


# --- Human-in-the-Loop Task Queue Tools ---


# @mcp.tool  # DEPRECATED - removed from MCP interface
def create_workflow_step_for_task(req, context):
    """DEPRECATED - Use context_mgr.create_workflow_step() directly."""
    raise ToolError("DEPRECATED", "This function has been deprecated. Use workflow steps directly.")
    # Original implementation removed - see git history if needed
    principal_id = get_principal_from_context(context)
    if not principal_id:
        raise ToolError("AUTHENTICATION_REQUIRED", "You must provide a valid x-adcp-auth header")

    # Get or create context for this workflow
    tenant = get_current_tenant()
    ctx_id = context.headers.get("x-context-id") if hasattr(context, "headers") else None

    # For human tasks, we always need a context
    if ctx_id:
        persistent_ctx = context_mgr.get_context(ctx_id)
    else:
        persistent_ctx = context_mgr.create_context(tenant_id=tenant["tenant_id"], principal_id=principal_id)
        ctx_id = persistent_ctx.context_id

    # Calculate due date
    due_by = None
    if req.due_in_hours:
        due_by = datetime.now() + timedelta(hours=req.due_in_hours)
    elif req.priority == "urgent":
        due_by = datetime.now() + timedelta(hours=4)
    elif req.priority == "high":
        due_by = datetime.now() + timedelta(hours=24)
    elif req.priority == "medium":
        due_by = datetime.now() + timedelta(hours=48)

    # Determine owner based on task type
    owner = "publisher"  # Most human tasks need publisher action
    if req.task_type in ["compliance_review", "manual_approval"]:
        owner = "publisher"
    elif req.task_type == "creative_approval":
        owner = "principal" if req.context_data and req.context_data.get("principal_action_needed") else "publisher"

    # Build object mappings if we have related objects
    object_mappings = []
    if req.media_buy_id:
        object_mappings.append(
            {
                "object_type": "media_buy",
                "object_id": req.media_buy_id,
                "action": "approval_required",
            }
        )
    if req.creative_id:
        object_mappings.append(
            {
                "object_type": "creative",
                "object_id": req.creative_id,
                "action": "approval_required",
            }
        )

    # Create workflow step
    step = context_mgr.create_workflow_step(
        context_id=ctx_id,
        is_async=True,
        step_type="approval",
        owner=owner,
        status="requires_approval",
        tool_name=req.operation,
        request_data={
            "task_type": req.task_type,
            "principal_id": principal_id,
            "adapter_name": req.adapter_name,
            "priority": req.priority,
            "media_buy_id": req.media_buy_id,
            "creative_id": req.creative_id,
            "operation": req.operation,
            "error_detail": req.error_detail,
            "context_data": req.context_data,
            "due_by": due_by.isoformat() if due_by else None,
        },
        assigned_to=req.assigned_to,
        object_mappings=object_mappings,
        initial_comment=req.error_detail or "Manual approval required",
    )

    task_id = step.step_id

    # Log task creation
    audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
    audit_logger.log_operation(
        operation="create_human_task",
        principal_name=principal_id,
        principal_id=principal_id,
        adapter_id="task_queue",
        success=True,
        details={
            "task_id": task_id,
            "task_type": req.task_type,
            "priority": req.priority,
        },
    )

    # Log high priority tasks
    if req.priority in ["high", "urgent"]:
        console.print(f"[bold red]🚨 HIGH PRIORITY TASK CREATED: {task_id}[/bold red]")
        console.print(f"   Type: {req.task_type}")
        console.print(f"   Error: {req.error_detail}")

    # Send webhook notification for urgent tasks (if configured)
    tenant = get_current_tenant()
    webhook_url = tenant.get("hitl_webhook_url")
    if webhook_url and req.priority == "urgent":
        try:
            import requests

            requests.post(
                webhook_url,
                json={
                    "task_id": task_id,
                    "type": req.task_type,
                    "priority": req.priority,
                    "principal": principal_id,
                    "error": req.error_detail,
                    "tenant": tenant["tenant_id"],
                },
                timeout=5,
            )
        except:
            pass  # Don't fail task creation if webhook fails

    # Task is now handled entirely through WorkflowStep - no separate Task table needed
    console.print(f"[green]✅ Created workflow step {task_id}[/green]")

    # Send Slack notification for new tasks
    try:
        # Build notifier config from tenant fields
        notifier_config = {
            "features": {
                "slack_webhook_url": tenant.get("slack_webhook_url"),
                "slack_audit_webhook_url": tenant.get("slack_audit_webhook_url"),
            }
        }
        slack_notifier = get_slack_notifier(notifier_config)
        slack_notifier.notify_new_task(
            task_id=task_id,
            task_type=req.task_type,
            principal_name=principal_id,
            media_buy_id=req.media_buy_id,
            details={
                "priority": req.priority,
                "error": req.error_detail,
                "operation": req.operation,
                "adapter": req.adapter_name,
            },
            tenant_name=tenant["name"],
        )
    except Exception as e:
        console.print(f"[yellow]Failed to send Slack notification: {e}[/yellow]")

    return CreateHumanTaskResponse(task_id=task_id, status="pending", due_by=due_by)


# Removed get_pending_workflows - replaced by admin dashboard workflow views


# Removed assign_task - assignment handled through admin UI workflow management


# @mcp.tool  # DEPRECATED - removed from MCP interface
def complete_task(req, context):
    """Complete a human task with resolution details."""
    # DEPRECATED: This function has been deprecated in favor of workflow steps
    raise ToolError(
        "DEPRECATED", "Task system has been replaced with workflow steps. Use Admin UI workflow management."
    )

    with get_db_session() as db_session:
        db_task = db_session.query(Task).filter_by(task_id=req.task_id, tenant_id=tenant["tenant_id"]).first()

        if not db_task:
            raise ToolError("NOT_FOUND", f"Task {req.task_id} not found")

        # Update database fields
        db_task.status = "completed" if req.resolution in ["approved", "completed"] else "failed"
        db_task.resolution = req.resolution
        db_task.resolution_notes = req.resolution_detail
        db_task.resolved_by = req.resolved_by
        db_task.completed_at = datetime.now(UTC)
        db_task.updated_at = datetime.now(UTC)

        # Preserve original metadata while adding resolution info
        original_metadata = db_task.task_metadata or {}
        db_task.task_metadata = {
            **original_metadata,  # Keep original fields
            "resolution": req.resolution,
            "resolution_detail": req.resolution_detail,
        }
        db_session.commit()

        # Create HumanTask object for backward compatibility (e.g., for Slack notifications)
        task = HumanTask(
            task_id=db_task.task_id,
            task_type=db_task.task_type,
            priority=original_metadata.get("priority", "medium"),
            media_buy_id=db_task.media_buy_id,
            creative_id=original_metadata.get("creative_id"),
            operation=original_metadata.get("operation"),
            principal_id=original_metadata.get("principal_id"),
            adapter_name=original_metadata.get("adapter_name"),
            error_detail=original_metadata.get("error_detail") or db_task.description,
            context_data=db_task.details or {},
            status=db_task.status,
            created_at=db_task.created_at,
            due_by=db_task.due_date,
            assigned_to=db_task.assigned_to,
            resolution=db_task.resolution,
            resolution_detail=db_task.resolution_notes,
            resolved_by=db_task.resolved_by,
            completed_at=db_task.completed_at,
        )

    audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
    audit_logger.log_operation(
        operation="complete_task",
        principal_name="admin",
        principal_id=principal_id,
        adapter_id="task_queue",
        success=True,
        details={
            "task_id": req.task_id,
            "resolution": req.resolution,
            "resolved_by": req.resolved_by,
        },
    )

    # Send Slack notification for task completion
    try:
        # Build notifier config from tenant fields
        notifier_config = {
            "features": {
                "slack_webhook_url": tenant.get("slack_webhook_url"),
                "slack_audit_webhook_url": tenant.get("slack_audit_webhook_url"),
            }
        }
        slack_notifier = get_slack_notifier(notifier_config)
        slack_notifier.notify_task_completed(
            task_id=req.task_id,
            task_type=task.task_type,
            completed_by=req.resolved_by,
            success=task.status == "completed",
            error_message=req.resolution_detail if task.status == "failed" else None,
        )
    except Exception as e:
        console.print(f"[yellow]Failed to send Slack notification: {e}[/yellow]")

    # Handle specific task types
    if task.task_type == "creative_approval" and task.creative_id:
        if req.resolution == "approved":
            # Update creative status
            if task.creative_id in creative_statuses:
                creative_statuses[task.creative_id].status = "approved"
                creative_statuses[task.creative_id].detail = "Manually approved by " + req.resolved_by
                console.print(f"[green]✅ Creative {task.creative_id} approved[/green]")

    elif task.task_type == "manual_approval" and task.operation:
        if req.resolution == "approved":
            # Execute the deferred operation
            console.print(f"[green]✅ Executing deferred operation: {task.operation}[/green]")

            # Get principal for the operation
            principal = get_principal_object(task.principal_id)
            if principal:
                adapter = get_adapter(principal, dry_run=DRY_RUN_MODE)

                if task.operation == "create_media_buy":
                    # Reconstruct and execute the create_media_buy request
                    original_req = CreateMediaBuyRequest(**task.context_data["request"])

                    # Get products for the media buy
                    catalog = get_product_catalog()
                    products_in_buy = [p for p in catalog if p.product_id in original_req.product_ids]

                    # Convert products to MediaPackages
                    packages = []
                    for product in products_in_buy:
                        first_format_id = product.formats[0] if product.formats else None
                        packages.append(
                            MediaPackage(
                                package_id=product.product_id,
                                name=product.name,
                                delivery_type=product.delivery_type,
                                cpm=product.cpm if product.cpm else 10.0,
                                impressions=int(
                                    original_req.total_budget / (product.cpm if product.cpm else 10.0) * 1000
                                ),
                                format_ids=[first_format_id] if first_format_id else [],
                            )
                        )

                    # Execute the actual creation
                    start_time = datetime.combine(original_req.start_date, datetime.min.time())
                    end_time = datetime.combine(original_req.end_date, datetime.max.time())
                    response = adapter.create_media_buy(original_req, packages, start_time, end_time)

                    # Store the media buy in memory (for backward compatibility)
                    media_buys[response.media_buy_id] = (
                        original_req,
                        task.principal_id,
                    )

                    # Store the media buy in database
                    tenant = get_current_tenant()
                    with get_db_session() as session:
                        principal = get_principal_object(task.principal_id)
                        new_media_buy = MediaBuy(
                            media_buy_id=response.media_buy_id,
                            tenant_id=tenant["tenant_id"],
                            principal_id=task.principal_id,
                            order_name=original_req.po_number or f"Order-{response.media_buy_id}",
                            advertiser_name=principal.name if principal else "Unknown",
                            campaign_objective=getattr(original_req, "campaign_objective", ""),  # Optional field
                            kpi_goal=getattr(original_req, "kpi_goal", ""),  # Optional field
                            budget=original_req.total_budget,
                            start_date=original_req.start_date.isoformat(),
                            end_date=original_req.end_date.isoformat(),
                            status=response.status or "active",
                            raw_request=original_req.model_dump(mode="json"),
                        )
                        session.add(new_media_buy)
                        session.commit()
                    console.print(f"[green]Media buy {response.media_buy_id} created after manual approval[/green]")

                elif task.operation == "update_media_buy":
                    # Reconstruct and execute the update_media_buy request
                    original_req = UpdateMediaBuyRequest(**task.context_data["request"])
                    today = original_req.today or date.today()

                    # Execute the updates
                    if original_req.active is not None:
                        action = "resume_media_buy" if original_req.active else "pause_media_buy"
                        adapter.update_media_buy(
                            media_buy_id=original_req.media_buy_id,
                            action=action,
                            package_id=None,
                            budget=None,
                            today=datetime.combine(today, datetime.min.time()),
                        )

                    # Handle package updates
                    if original_req.packages:
                        for pkg_update in original_req.packages:
                            if pkg_update.active is not None:
                                action = "resume_package" if pkg_update.active else "pause_package"
                                adapter.update_media_buy(
                                    media_buy_id=original_req.media_buy_id,
                                    action=action,
                                    package_id=pkg_update.package_id,
                                    budget=None,
                                    today=datetime.combine(today, datetime.min.time()),
                                )

                    console.print(f"[green]Media buy {original_req.media_buy_id} updated after manual approval[/green]")
        else:
            console.print(f"[red]❌ Manual approval rejected for {task.operation}[/red]")

    return {
        "status": "success",
        "detail": f"Task {req.task_id} completed with resolution: {req.resolution}",
    }


# @mcp.tool  # DEPRECATED - removed from MCP interface
def verify_task(req, context):
    """Verify if a task was completed correctly by checking actual state."""
    # Get task from database
    tenant = get_current_tenant()
    task = get_task_from_db(req.task_id, tenant["tenant_id"])
    if not task:
        raise ToolError("NOT_FOUND", f"Task {req.task_id} not found")
    actual_state = {}
    expected_state = req.expected_outcome or {}
    discrepancies = []
    verified = True

    # Verify based on task type and operation
    if task.task_type == "manual_approval" and task.operation == "update_media_buy":
        # Extract expected changes from task context
        if task.context_data and "request" in task.context_data:
            update_req = task.context_data["request"]
            media_buy_id = update_req.get("media_buy_id")

            if media_buy_id and media_buy_id in media_buys:
                # Get current state
                buy_request, principal_id = media_buys[media_buy_id]

                # Check daily budget if it was being updated
                if "daily_budget" in update_req:
                    expected_budget = update_req["daily_budget"]
                    actual_budget = getattr(buy_request, "daily_budget", None)

                    actual_state["daily_budget"] = actual_budget
                    expected_state["daily_budget"] = expected_budget

                    if actual_budget != expected_budget:
                        discrepancies.append(f"Daily budget is ${actual_budget}, expected ${expected_budget}")
                        verified = False

                # Check active status
                if "active" in update_req:
                    # Would need to check adapter status
                    # For now, assume task completion means it worked
                    actual_state["active"] = task.status == "completed"
                    expected_state["active"] = update_req["active"]

                # Check package updates
                if "packages" in update_req:
                    for pkg_update in update_req["packages"]:
                        if "budget" in pkg_update:
                            # Would need to query adapter for actual package budget
                            expected_state[f"package_{pkg_update['package_id']}_budget"] = pkg_update["budget"]
                            # For demo, assume it matches if task completed
                            actual_state[f"package_{pkg_update['package_id']}_budget"] = (
                                pkg_update["budget"] if task.status == "completed" else 0
                            )

    elif task.task_type == "creative_approval":
        # Check if creative was actually approved
        creative_id = task.creative_id
        if creative_id and creative_id in creative_statuses:
            actual_status = creative_statuses[creative_id].status
            actual_state["creative_status"] = actual_status
            expected_state["creative_status"] = "approved"

            if actual_status != "approved" and task.resolution == "approved":
                discrepancies.append(f"Creative {creative_id} status is {actual_status}, expected approved")
                verified = False

    return VerifyTaskResponse(
        task_id=req.task_id,
        verified=verified,
        actual_state=actual_state,
        expected_state=expected_state,
        discrepancies=discrepancies,
    )


# @mcp.tool  # DEPRECATED - removed from MCP interface
def mark_task_complete(req, context):
    """Mark a task as complete with automatic verification."""
    # Admin only
    principal_id = get_principal_from_context(context)
    tenant = get_current_tenant()
    if principal_id != f"{tenant['tenant_id']}_admin":
        raise ToolError("PERMISSION_DENIED", "Only administrators can mark tasks complete")

    # First verify the task
    verify_req = VerifyTaskRequest(task_id=req.task_id)
    verification = verify_task(verify_req, context)

    if not verification.verified and not req.override_verification:
        return {
            "status": "verification_failed",
            "verified": False,
            "discrepancies": verification.discrepancies,
            "message": "Task verification failed. Use override_verification=true to force completion.",
        }

    # Update task in database directly
    from src.core.database.database_session import get_db_session

    # DEPRECATED: Task model removed in favor of workflow system
    raise ToolError("DEPRECATED", "Task system has been replaced with workflow steps.")

    with get_db_session() as db_session:
        db_task = db_session.query(Task).filter_by(task_id=req.task_id, tenant_id=tenant["tenant_id"]).first()

        if not db_task:
            raise ToolError("NOT_FOUND", f"Task {req.task_id} not found")

        # Mark as complete
        resolution_detail = f"Marked complete by {req.completed_by}"
        if not verification.verified:
            resolution_detail += " (verification overridden)"

        db_task.status = "completed"
        db_task.resolution = "completed"
        db_task.resolution_notes = resolution_detail
        db_task.resolved_by = req.completed_by
        db_task.completed_at = datetime.now(UTC)
        db_task.updated_at = datetime.now(UTC)

        # Update metadata with verification results
        original_metadata = db_task.task_metadata or {}
        db_task.task_metadata = {
            **original_metadata,
            "resolution": "completed",
            "resolution_detail": resolution_detail,
            "verification": verification.model_dump(),
        }
        db_session.commit()

    audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
    audit_logger.log_operation(
        operation="mark_task_complete",
        principal_name="admin",
        principal_id=principal_id,
        adapter_id="task_queue",
        success=True,
        details={
            "task_id": req.task_id,
            "verified": verification.verified,
            "override": req.override_verification,
            "completed_by": req.completed_by,
        },
    )

    return {
        "status": "success",
        "task_id": req.task_id,
        "verified": verification.verified,
        "verification_details": {
            "actual_state": verification.actual_state,
            "expected_state": verification.expected_state,
            "discrepancies": verification.discrepancies,
        },
        "message": f"Task marked complete by {req.completed_by}",
    }


# Dry run logs are now handled by the adapters themselves


def get_product_catalog() -> list[Product]:
    """Get products for the current tenant."""
    tenant = get_current_tenant()

    with get_db_session() as session:
        products = session.query(ModelProduct).filter_by(tenant_id=tenant["tenant_id"]).all()

        loaded_products = []
        for product in products:
            # Convert ORM model to Pydantic schema
            # Parse JSON fields that might be strings (SQLite) or dicts (PostgreSQL)
            def safe_json_parse(value):
                if isinstance(value, str):
                    try:
                        return json.loads(value)
                    except (json.JSONDecodeError, TypeError):
                        return value
                return value

            # Parse formats - now stored as strings by the validator
            format_ids = safe_json_parse(product.formats) or []
            # Ensure it's a list of strings (validator guarantees this)
            if not isinstance(format_ids, list):
                format_ids = []

            product_data = {
                "product_id": product.product_id,
                "name": product.name,
                "description": product.description,
                "formats": format_ids,
                "delivery_type": product.delivery_type,
                "is_fixed_price": product.is_fixed_price,
                "cpm": float(product.cpm) if product.cpm else None,
                "min_spend": float(product.min_spend) if hasattr(product, "min_spend") and product.min_spend else None,
                "measurement": (
                    safe_json_parse(product.measurement)
                    if hasattr(product, "measurement") and product.measurement
                    else None
                ),
                "creative_policy": (
                    safe_json_parse(product.creative_policy)
                    if hasattr(product, "creative_policy") and product.creative_policy
                    else None
                ),
                "is_custom": product.is_custom,
                "expires_at": product.expires_at,
                # Note: brief_relevance is populated dynamically when brief is provided
                "implementation_config": safe_json_parse(product.implementation_config),
            }
            loaded_products.append(Product(**product_data))

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
    principal_id = get_principal_from_context(context)
    tenant_config = get_current_tenant()

    if not tenant_config:
        raise ToolError("No tenant configuration found")

    return StrategyManager(tenant_id=tenant_config.get("tenant_id"), principal_id=principal_id)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request):
    """Health check endpoint."""
    return JSONResponse({"status": "healthy", "service": "mcp"})


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

    debug_info = {"step": "initial", "virtual_host": virtual_host, "apx_host": apx_host, "host_header": host_header}

    if virtual_host:
        debug_info["step"] = "virtual_host_found"

        # First try to look up tenant by exact virtual host match
        tenant = get_tenant_by_virtual_host(virtual_host)
        debug_info["exact_tenant_lookup"] = tenant is not None

        # If no exact match, check for domain-based routing patterns
        if not tenant and ".sales-agent.scope3.com" in virtual_host and not virtual_host.startswith("admin."):
            debug_info["step"] = "subdomain_fallback"
            subdomain = virtual_host.split(".sales-agent.scope3.com")[0]
            debug_info["extracted_subdomain"] = subdomain

            # This is the fallback logic we don't need for test-agent
            try:
                with get_db_session() as db_session:
                    tenant_obj = db_session.query(Tenant).filter_by(subdomain=subdomain, is_active=True).first()
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


# Add admin UI routes when running unified
unified_mode = os.environ.get("ADCP_UNIFIED_MODE")
logger.info(f"STARTUP: ADCP_UNIFIED_MODE = '{unified_mode}' (type: {type(unified_mode)})")
if unified_mode:
    from fastapi.middleware.wsgi import WSGIMiddleware
    from fastapi.responses import HTMLResponse, RedirectResponse

    from src.admin.app import create_app

    # Create Flask app and get the app instance
    flask_admin_app, _ = create_app()

    # Create WSGI middleware for Flask app
    admin_wsgi = WSGIMiddleware(flask_admin_app)

    logger.info("STARTUP: Registering unified mode routes...")

    logger.info("STARTUP: ADCP_UNIFIED_MODE enabled, registering routes...")

    async def handle_landing_page(request: Request):
        """Common landing page logic for both root and /landing routes."""
        headers = dict(request.headers)
        apx_host = headers.get("apx-incoming-host") or headers.get("Apx-Incoming-Host")

        # Check if this is an external domain request
        if apx_host and apx_host.endswith(".adcontextprotocol.org"):
            # Look up tenant by virtual host
            tenant = get_tenant_by_virtual_host(apx_host)

            if tenant:
                # Generate tenant landing page
                try:
                    html_content = generate_tenant_landing_page(tenant, apx_host)
                    return HTMLResponse(content=html_content)
                except Exception as e:
                    logger.error(f"Error generating landing page: {e}", exc_info=True)
                    return HTMLResponse(
                        content=f"""
                    <html>
                    <body>
                    <h1>Welcome to {tenant.get('name', 'AdCP Sales Agent')}</h1>
                    <p>This is a sales agent for advertising inventory.</p>
                    <p>Domain: {apx_host}</p>
                    </body>
                    </html>
                    """
                    )

        # Check if this is a subdomain request
        if apx_host and ".sales-agent.scope3.com" in apx_host:
            # Extract subdomain from apx_host
            subdomain = apx_host.split(".sales-agent.scope3.com")[0]

            # Look up tenant by subdomain
            try:
                with get_db_session() as db_session:
                    tenant_obj = db_session.query(Tenant).filter_by(subdomain=subdomain, is_active=True).first()
                    if tenant_obj:
                        tenant = {
                            "tenant_id": tenant_obj.tenant_id,
                            "name": tenant_obj.name,
                            "subdomain": tenant_obj.subdomain,
                            "virtual_host": tenant_obj.virtual_host,
                        }
                        # Generate tenant landing page for subdomain
                        try:
                            html_content = generate_tenant_landing_page(tenant, apx_host)
                            return HTMLResponse(content=html_content)
                        except Exception as e:
                            logger.error(f"Error generating subdomain landing page: {e}", exc_info=True)
                            return HTMLResponse(
                                content=f"""
                            <html>
                            <body>
                            <h1>Welcome to {tenant.get('name', 'AdCP Sales Agent')}</h1>
                            <p>Subdomain: {apx_host}</p>
                            </body>
                            </html>
                            """
                            )
            except Exception as e:
                logger.error(f"Error looking up subdomain {subdomain}: {e}")

        # Fallback for unrecognized domains
        return HTMLResponse(
            content=f"""
        <html>
        <body>
        <h1>🎉 LANDING PAGE WORKING!</h1>
        <p>Domain: {apx_host}</p>
        <p>Success! The landing page is working.</p>
        </body>
        </html>
        """
        )

    @mcp.custom_route("/", methods=["GET"])
    async def root(request: Request):
        """Root route handler for all domains."""
        return await handle_landing_page(request)

    @mcp.custom_route("/landing", methods=["GET"])
    async def landing_page(request: Request):
        """Landing page route for external domains."""
        return await handle_landing_page(request)

    logger.info("STARTUP: Registered root route")

    @mcp.custom_route(
        "/admin/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
    )
    async def admin_handler(request: Request, path: str = ""):
        """Handle admin UI requests."""
        # Forward to Flask app
        scope = request.scope.copy()
        scope["path"] = f"/{path}" if path else "/"

        receive = request.receive
        send = request._send

        await admin_wsgi(scope, receive, send)

    @mcp.custom_route(
        "/tenant/{tenant_id}/admin/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
    )
    async def tenant_admin_handler(request: Request, tenant_id: str, path: str = ""):
        """Handle tenant-specific admin requests."""
        # Forward to Flask app with tenant context
        scope = request.scope.copy()
        scope["path"] = f"/tenant/{tenant_id}/{path}" if path else f"/tenant/{tenant_id}"

        receive = request.receive
        send = request._send

        await admin_wsgi(scope, receive, send)

    @mcp.custom_route("/tenant/{tenant_id}", methods=["GET"])
    async def tenant_root(request: Request, tenant_id: str):
        """Redirect to tenant admin."""
        return RedirectResponse(url=f"/tenant/{tenant_id}/admin/")
