"""List Authorized Properties tool implementation.

Handles property discovery including:
- Publisher domain enumeration
- Property tag filtering
- Advertising policy disclosure
- Virtual host routing
"""

import logging
import time

from adcp import ListAuthorizedPropertiesRequest
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from sqlalchemy import select

from src.core.audit_logger import get_audit_logger
from src.core.auth import get_principal_from_context
from src.core.config_loader import get_current_tenant, set_current_tenant
from src.core.database.database_session import get_db_session
from src.core.database.models import PublisherPartner
from src.core.helpers import log_tool_activity
from src.core.schema_adapters import ListAuthorizedPropertiesResponse
from src.core.testing_hooks import get_testing_context
from src.core.tool_context import ToolContext
from src.core.validation_helpers import safe_parse_json_field

logger = logging.getLogger(__name__)


def _list_authorized_properties_impl(
    req: ListAuthorizedPropertiesRequest | None = None, context: Context | ToolContext | None = None
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
    # Authentication is OPTIONAL for discovery endpoints (returns public inventory)
    # require_valid_token=False means invalid tokens are treated like missing tokens (discovery endpoint behavior)
    principal_id, tenant = get_principal_from_context(
        context,
        require_valid_token=False,  # type: ignore[arg-type]
    )  # May return (None, tenant) for public discovery

    # Set tenant context if returned
    if tenant:
        set_current_tenant(tenant)
    else:
        tenant = get_current_tenant()

    if not tenant:
        raise ToolError(
            "TENANT_ERROR",
            "Could not resolve tenant from request context (no subdomain, virtual host, or x-adcp-tenant header found)",
        )

    tenant_id = tenant["tenant_id"]

    # Apply testing hooks
    from src.core.testing_hooks import AdCPTestContext
    from src.core.tool_context import ToolContext

    if isinstance(context, ToolContext):
        # ToolContext has testing_context field directly
        testing_context = AdCPTestContext(**context.testing_context) if context.testing_context else AdCPTestContext()
    else:
        # FastMCP Context - use get_testing_context
        testing_context = get_testing_context(context) if context else AdCPTestContext()

    # Note: apply_testing_hooks signature is (data, testing_ctx, operation, campaign_info)
    # For list_authorized_properties, we don't modify data, so we can skip this call
    # The testing_context is used later if needed

    # Activity logging imported at module level
    if context is not None:
        log_tool_activity(context, "list_authorized_properties", start_time)

    try:
        with get_db_session() as session:
            # Query verified publisher partners for this tenant
            # list_authorized_properties returns publisher domains we're authorized to represent
            # This comes from our PublisherPartner table (single source of truth)
            stmt = select(PublisherPartner).where(
                PublisherPartner.tenant_id == tenant_id, PublisherPartner.is_verified == True  # noqa: E712
            )
            verified_publishers = session.scalars(stmt).all()

            # Extract publisher domains
            publisher_domains = sorted([p.publisher_domain for p in verified_publishers])

            # If no publishers configured, return error - NO FALLBACK BEHAVIOR
            if not publisher_domains:
                raise ToolError(
                    "NO_PUBLISHERS_CONFIGURED",
                    f"No verified publisher partnerships configured for tenant '{tenant_id}'. "
                    f"Please add publishers via the Admin UI at /admin/tenant/{tenant_id}/inventory#publishers-pane",
                )

            # Generate advertising policies text from tenant configuration
            advertising_policies_text = None
            advertising_policy = safe_parse_json_field(
                tenant.get("advertising_policy"), field_name="advertising_policy", default={}
            )

            if advertising_policy and advertising_policy.get("enabled"):
                # Build human-readable policy text
                policy_parts = []

                # Add baseline categories
                default_categories = advertising_policy.get("default_prohibited_categories", [])
                if default_categories:
                    policy_parts.append(f"**Baseline Protected Categories:** {', '.join(default_categories)}")

                # Add baseline tactics
                default_tactics = advertising_policy.get("default_prohibited_tactics", [])
                if default_tactics:
                    policy_parts.append(f"**Baseline Prohibited Tactics:** {', '.join(default_tactics)}")

                # Add additional categories
                additional_categories = advertising_policy.get("prohibited_categories", [])
                if additional_categories:
                    policy_parts.append(f"**Additional Prohibited Categories:** {', '.join(additional_categories)}")

                # Add additional tactics
                additional_tactics = advertising_policy.get("prohibited_tactics", [])
                if additional_tactics:
                    policy_parts.append(f"**Additional Prohibited Tactics:** {', '.join(additional_tactics)}")

                # Add blocked advertisers
                blocked_advertisers = advertising_policy.get("prohibited_advertisers", [])
                if blocked_advertisers:
                    policy_parts.append(f"**Blocked Advertisers/Domains:** {', '.join(blocked_advertisers)}")

                if policy_parts:
                    advertising_policies_text = "\n\n".join(policy_parts)
                    # Add footer
                    advertising_policies_text += (
                        "\n\n**Policy Enforcement:** Campaigns are analyzed using AI against these policies. "
                        "Violations will result in campaign rejection or require manual review."
                    )

            # Create response with AdCP spec-compliant fields
            # Note: Optional fields (advertising_policies, errors, etc.) should be omitted if not set,
            # not set to None or empty values. AdCPBaseModel.model_dump() uses exclude_none=True by default.
            # Build response dict with only non-None values
            from typing import Any

            response_data: dict[str, Any] = {"publisher_domains": publisher_domains}  # Required per AdCP v2.4 spec

            # Only add optional fields if they have actual values
            if advertising_policies_text:
                response_data["advertising_policies"] = advertising_policies_text

            response = ListAuthorizedPropertiesResponse(**response_data)

            # Carry back application context from request if provided
            if req.context is not None:
                response.context = req.context

            # Log audit
            audit_logger = get_audit_logger("AdCP", tenant_id)
            audit_logger.log_operation(
                operation="list_authorized_properties",
                principal_name=principal_id or "anonymous",
                principal_id=principal_id or "anonymous",
                adapter_id="mcp_server",
                success=True,
                details={
                    "publisher_count": len(publisher_domains),
                    "publisher_domains": publisher_domains,
                },
            )

            return response

    except Exception as e:
        logger.error(f"Error listing authorized properties: {str(e)}")

        # Log audit for failure
        audit_logger = get_audit_logger("AdCP", tenant_id)
        principal_name = principal_id if principal_id else "anonymous"
        audit_logger.log_operation(
            operation="list_authorized_properties",
            principal_name=principal_name,
            principal_id=principal_name,
            adapter_id="mcp_server",
            success=False,
            error=str(e),
        )

        raise ToolError("PROPERTIES_ERROR", f"Failed to list authorized properties: {str(e)}")


def list_authorized_properties(
    req: ListAuthorizedPropertiesRequest | None = None,
    webhook_url: str | None = None,
    ctx: Context | ToolContext | None = None,
    context: dict | None = None,  # payload-level context
):
    """List all properties this agent is authorized to represent (AdCP spec endpoint).

    MCP tool wrapper that delegates to the shared implementation.

    Args:
        req: Request parameters including optional tag filters
        webhook_url: URL for async task completion notifications (AdCP spec, optional)
        context: Application level context per adcp spec
        ctx: FastMCP context for authentication

    Returns:
        ToolResult with human-readable text and structured data
    """
    # FIX: Create MinimalContext with headers from FastMCP request (like A2A does)
    # This ensures tenant detection works the same way for both MCP and A2A
    import logging
    import sys

    logger = logging.getLogger(__name__)
    tool_context: Context | ToolContext | None = None

    if ctx:
        try:
            # Log ALL headers received for debugging virtual host issues
            logger.debug("🔍 MCP list_authorized_properties called")
            logger.debug(f"🔍 context type={type(ctx)}")

            # Access raw Starlette request headers via context.request_context.request
            # ToolContext doesn't have request_context (A2A path doesn't use Starlette)
            request = None
            if isinstance(ctx, Context) and hasattr(ctx, "request_context"):
                request = ctx.request_context.request
            logger.debug(f"🔍 request type={type(request) if request else None}")

            if request and hasattr(request, "headers"):
                headers = dict(request.headers)
                logger.debug(f"🔍 Received {len(headers)} headers:")
                for key, value in headers.items():
                    logger.debug(f"🔍   {key}: {value}")

                logger.debug(
                    f"🔍 Key headers: Host={headers.get('host')}, Apx-Incoming-Host={headers.get('apx-incoming-host')}"
                )

                # Create MinimalContext matching A2A pattern
                # Note: Using Any type to allow duck-typed context
                from typing import Any

                class MinimalContext:
                    def __init__(self, headers: dict[str, str]):
                        self.meta: dict[str, Any] = {"headers": headers}
                        self.headers: dict[str, str] = headers

                tool_context_temp: Any = MinimalContext(headers)
                tool_context = tool_context_temp
                print("[MCP DEBUG] Created MinimalContext successfully", file=sys.stderr, flush=True)
                logger.info("MCP list_authorized_properties: Created MinimalContext successfully")
            else:
                print("[MCP DEBUG] request has no headers attribute", file=sys.stderr, flush=True)
                logger.warning("MCP list_authorized_properties: request has no headers attribute")
                tool_context = ctx
        except Exception as e:
            # Fallback to passing context as-is
            print(f"[MCP DEBUG] Exception extracting headers: {e}", file=sys.stderr, flush=True)
            logger.error(
                f"MCP list_authorized_properties: Could not extract headers from FastMCP context: {e}", exc_info=True
            )
            tool_context = ctx
    else:
        print("[MCP DEBUG] No context provided", file=sys.stderr, flush=True)
        logger.info("MCP list_authorized_properties: No context provided")
        tool_context = ctx

    response = _list_authorized_properties_impl(req, tool_context)

    # Return ToolResult with human-readable text and structured data
    # The __str__() method provides the human-readable message
    # The model_dump() provides the structured JSON data
    return ToolResult(content=str(response), structured_content=response.model_dump())


def list_authorized_properties_raw(
    req: "ListAuthorizedPropertiesRequest" = None, ctx: Context | ToolContext | None = None
) -> "ListAuthorizedPropertiesResponse":
    """List all properties this agent is authorized to represent (raw function for A2A server use).

    Delegates to shared implementation.

    Args:
        req: Optional request with filter parameters
        context: FastMCP context

    Returns:
        ListAuthorizedPropertiesResponse with authorized properties
    """
    return _list_authorized_properties_impl(req, ctx)
