"""List Authorized Properties tool implementation.

Handles property discovery including:
- Publisher domain enumeration
- Property tag filtering
- Advertising policy disclosure
- Virtual host routing
"""

import logging
import time

import sqlalchemy as sa
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from sqlalchemy import select

logger = logging.getLogger(__name__)

from src.core.audit_logger import get_audit_logger
from src.core.auth import get_principal_from_context
from src.core.config_loader import get_current_tenant, set_current_tenant
from src.core.database.database_session import get_db_session
from src.core.database.models import AuthorizedProperty, PropertyTag
from src.core.helpers import log_tool_activity
from src.core.schema_adapters import ListAuthorizedPropertiesRequest, ListAuthorizedPropertiesResponse
from src.core.schemas import Property, PropertyIdentifier, PropertyTagMetadata
from src.core.validation_helpers import safe_parse_json_field


def _list_authorized_properties_impl(
    req: ListAuthorizedPropertiesRequest | None = None, context: Context | None = None
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
        context, require_valid_token=False
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
        headers = {}
    else:
        # FastMCP Context has meta.headers
        headers = context.meta.get("headers", {}) if context and hasattr(context, "meta") and context.meta else {}
        testing_context = get_testing_context(headers)

    # Note: apply_testing_hooks signature is (data, testing_ctx, operation, campaign_info)
    # For list_authorized_properties, we don't modify data, so we can skip this call
    # The testing_context is used later if needed

    # Activity logging imported at module level

    log_tool_activity(context, "list_authorized_properties", start_time)

    try:
        with get_db_session() as session:
            # Query authorized properties for this tenant
            stmt = select(AuthorizedProperty).where(AuthorizedProperty.tenant_id == tenant_id)

            # Apply tag filtering if requested
            if req.tags:
                # Filter properties that have any of the requested tags
                tag_filters = []
                for tag in req.tags:
                    tag_filters.append(AuthorizedProperty.tags.contains([tag]))
                stmt = stmt.where(sa.or_(*tag_filters))

            # Get all properties for this tenant (no verification status filter)
            # Publishers control what properties they add - verification is informational only
            authorized_properties = session.scalars(stmt).all()

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
                stmt = select(PropertyTag).where(PropertyTag.tenant_id == tenant_id, PropertyTag.tag_id.in_(all_tags))
                property_tags = session.scalars(stmt).all()

                for property_tag in property_tags:
                    tag_metadata[property_tag.tag_id] = PropertyTagMetadata(
                        name=property_tag.name, description=property_tag.description
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

            # Extract unique publisher domains from properties
            publisher_domains = sorted({prop.publisher_domain for prop in properties if prop.publisher_domain})

            # If no properties configured, return error - NO FALLBACK BEHAVIOR
            if not publisher_domains:
                raise ToolError(
                    "NO_PROPERTIES_CONFIGURED",
                    f"No authorized properties configured for tenant '{tenant_id}'. "
                    f"Please add properties via the Admin UI at /admin/tenant/{tenant_id}/authorized-properties",
                )

            # Create response with AdCP spec-compliant fields
            response = ListAuthorizedPropertiesResponse(
                publisher_domains=publisher_domains,  # Required per AdCP v2.4 spec
                advertising_policies=advertising_policies_text,
                errors=[],
            )

            # Log audit
            audit_logger = get_audit_logger("AdCP", tenant_id)
            audit_logger.log_operation(
                operation="list_authorized_properties",
                principal_name=principal_id or "anonymous",
                principal_id=principal_id or "anonymous",
                adapter_id="mcp_server",
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
        audit_logger = get_audit_logger("AdCP", tenant_id)
        audit_logger.log_operation(
            operation="list_authorized_properties",
            principal_name=principal_id,
            principal_id=principal_id,
            adapter_id="mcp_server",
            success=False,
            error=str(e),
        )

        raise ToolError("PROPERTIES_ERROR", f"Failed to list authorized properties: {str(e)}")


def list_authorized_properties(
    req: ListAuthorizedPropertiesRequest | None = None, webhook_url: str | None = None, context: Context | None = None
) -> ListAuthorizedPropertiesResponse:
    """List all properties this agent is authorized to represent (AdCP spec endpoint).

    MCP tool wrapper that delegates to the shared implementation.

    Args:
        req: Request parameters including optional tag filters
        webhook_url: URL for async task completion notifications (AdCP spec, optional)
        context: FastMCP context for authentication

    Returns:
        ListAuthorizedPropertiesResponse with properties and tag metadata
    """
    # FIX: Create MinimalContext with headers from FastMCP request (like A2A does)
    # This ensures tenant detection works the same way for both MCP and A2A
    import logging
    import sys

    logger = logging.getLogger(__name__)
    tool_context = None

    if context:
        try:
            # Log ALL headers received for debugging virtual host issues
            logger.error("🔍 MCP list_authorized_properties called")
            logger.error(f"🔍 context type={type(context)}")

            # Access raw Starlette request headers via context.request_context.request
            request = context.request_context.request
            logger.error(f"🔍 request type={type(request) if request else None}")

            if request and hasattr(request, "headers"):
                headers = dict(request.headers)
                logger.error(f"🔍 Received {len(headers)} headers:")
                for key, value in headers.items():
                    logger.error(f"🔍   {key}: {value}")

                logger.error(
                    f"🔍 Key headers: Host={headers.get('host')}, Apx-Incoming-Host={headers.get('apx-incoming-host')}"
                )

                # Create MinimalContext matching A2A pattern
                class MinimalContext:
                    def __init__(self, headers):
                        self.meta = {"headers": headers}
                        self.headers = headers

                tool_context = MinimalContext(headers)
                print("[MCP DEBUG] Created MinimalContext successfully", file=sys.stderr, flush=True)
                logger.info("MCP list_authorized_properties: Created MinimalContext successfully")
            else:
                print("[MCP DEBUG] request has no headers attribute", file=sys.stderr, flush=True)
                logger.warning("MCP list_authorized_properties: request has no headers attribute")
                tool_context = context
        except Exception as e:
            # Fallback to passing context as-is
            print(f"[MCP DEBUG] Exception extracting headers: {e}", file=sys.stderr, flush=True)
            logger.error(
                f"MCP list_authorized_properties: Could not extract headers from FastMCP context: {e}", exc_info=True
            )
            tool_context = context
    else:
        print("[MCP DEBUG] No context provided", file=sys.stderr, flush=True)
        logger.info("MCP list_authorized_properties: No context provided")
        tool_context = context

    return _list_authorized_properties_impl(req, tool_context)


def list_authorized_properties_raw(
    req: "ListAuthorizedPropertiesRequest" = None, context: Context = None
) -> "ListAuthorizedPropertiesResponse":
    """List all properties this agent is authorized to represent (raw function for A2A server use).

    Delegates to shared implementation.

    Args:
        req: Optional request with filter parameters
        context: FastMCP context

    Returns:
        ListAuthorizedPropertiesResponse with authorized properties
    """
    return _list_authorized_properties_impl(req, context)
