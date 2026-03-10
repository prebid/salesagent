"""List Authorized Properties tool implementation.

Handles property discovery including:
- Publisher domain enumeration
- Property tag filtering
- Advertising policy disclosure
- Virtual host routing
"""

import logging
import time
from typing import Any, cast

from adcp.types.generated_poc.core.context import ContextObject
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult

from src.core.audit_logger import get_audit_logger
from src.core.database.repositories.uow import TenantConfigUoW
from src.core.exceptions import AdCPAdapterError, AdCPAuthenticationError
from src.core.helpers import log_tool_activity
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import ListAuthorizedPropertiesRequest, ListAuthorizedPropertiesResponse
from src.core.testing_hooks import AdCPTestContext
from src.core.tool_context import ToolContext
from src.core.validation_helpers import safe_parse_json_field

logger = logging.getLogger(__name__)


def _list_authorized_properties_impl(
    req: ListAuthorizedPropertiesRequest | None = None, identity: ResolvedIdentity | None = None
) -> ListAuthorizedPropertiesResponse:
    """List all properties this agent is authorized to represent (AdCP spec endpoint).

    Discovers advertising properties (websites, apps, podcasts, etc.) that this
    sales agent is authorized to sell advertising on behalf of publishers.

    Args:
        req: Request parameters including optional tag filters
        identity: Resolved identity for authentication

    Returns:
        ListAuthorizedPropertiesResponse with properties and tag metadata
    """
    start_time = time.time()

    # Handle missing request object (allows empty calls)
    if req is None:
        req = ListAuthorizedPropertiesRequest()

    # Extract principal and tenant from resolved identity
    principal_id = identity.principal_id if identity else None
    tenant = identity.tenant if identity else None

    if not tenant:
        raise AdCPAuthenticationError(
            "Could not resolve tenant from request context (no subdomain, virtual host, or x-adcp-tenant header found)",
            details={"error_code": "TENANT_ERROR"},
        )

    tenant_id = tenant["tenant_id"]

    # Apply testing hooks
    testing_context = identity.testing_context if identity else AdCPTestContext()
    if testing_context is None:
        testing_context = AdCPTestContext()

    # Note: apply_testing_hooks signature is (data, testing_ctx, operation, campaign_info)
    # For list_authorized_properties, we don't modify data, so we can skip this call
    # The testing_context is used later if needed

    # Activity logging
    if identity is not None:
        log_tool_activity(identity, "list_authorized_properties", start_time)

    try:
        with TenantConfigUoW(tenant_id) as uow:
            assert uow.tenant_config is not None
            # Query all publisher partners for this tenant (verified or pending)
            # We return all registered publishers because:
            # 1. Verification may be in progress during publisher setup
            # 2. The sales agent is claiming to represent these publishers
            # 3. Buyers should see the full portfolio even if some are pending verification
            all_publishers = uow.tenant_config.list_publisher_partners()

            # Extract publisher domains (all registered, regardless of verification status)
            publisher_domains = sorted([p.publisher_domain for p in all_publishers])

            # If no publishers configured, return empty list with helpful description
            if not publisher_domains:
                empty_response_data: dict[str, Any] = {"publisher_domains": []}
                empty_response_data["portfolio_description"] = (
                    "No publisher partnerships are currently configured. Publishers can be added via the Admin UI."
                )
                response = ListAuthorizedPropertiesResponse(**empty_response_data)

                # Carry back application context from request if provided
                if req and req.context is not None:
                    response.context = req.context

                return response

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

        raise AdCPAdapterError(
            f"Failed to list authorized properties: {str(e)}",
            details={"error_code": "PROPERTIES_ERROR"},
        )


async def list_authorized_properties(
    req: ListAuthorizedPropertiesRequest | None = None,
    webhook_url: str | None = None,
    ctx: Context | ToolContext | None = None,
    context: ContextObject | None = None,  # payload-level context
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
    # Inject payload-level context into the request object so _impl can echo it back
    # (follows the same pattern as list_creative_formats and all other MCP wrappers)
    if context is not None:
        if req is None:
            req = ListAuthorizedPropertiesRequest(context=context)
        else:
            req = cast(ListAuthorizedPropertiesRequest, req)
            req.context = context

    identity = (await ctx.get_state("identity")) if isinstance(ctx, Context) else None
    response = _list_authorized_properties_impl(cast(ListAuthorizedPropertiesRequest | None, req), identity)

    return ToolResult(content=str(response), structured_content=response)


def list_authorized_properties_raw(
    req: "ListAuthorizedPropertiesRequest" = None,
    ctx: Context | ToolContext | None = None,
    identity: ResolvedIdentity | None = None,
) -> "ListAuthorizedPropertiesResponse":
    """List all properties this agent is authorized to represent (raw function for A2A server use).

    Delegates to shared implementation.

    Args:
        req: Optional request with filter parameters
        ctx: FastMCP context
        identity: Pre-resolved identity (if available)

    Returns:
        ListAuthorizedPropertiesResponse with authorized properties
    """
    if identity is None:
        from src.core.transport_helpers import resolve_identity_from_context

        identity = resolve_identity_from_context(ctx, require_valid_token=False)
    return _list_authorized_properties_impl(req, identity)
