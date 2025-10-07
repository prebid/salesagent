"""Authentication utilities for MCP server."""

from fastmcp.server import Context
from rich.console import Console
from sqlalchemy import select

from src.core.config_loader import set_current_tenant
from src.core.database.database_session import execute_with_retry
from src.core.database.models import Principal, Tenant

console = Console()


def get_principal_from_token(token: str, tenant_id: str | None = None) -> str | None:
    """Looks up a principal_id from the database using a token with retry logic.

    If tenant_id is provided, only looks in that specific tenant.
    If not provided, searches globally by token and sets the tenant context.

    Args:
        token: Authentication token
        tenant_id: Optional tenant ID to restrict search

    Returns:
        Principal ID if found, None otherwise
    """

    def _lookup_principal(session):
        if tenant_id:
            # If tenant_id specified, ONLY look in that tenant
            stmt = select(Principal).filter_by(access_token=token, tenant_id=tenant_id)
            principal = session.scalars(stmt).first()
            if principal:
                return principal.principal_id

            # Also check if it's the admin token for this specific tenant
            stmt = select(Tenant).filter_by(tenant_id=tenant_id, is_active=True)
            tenant = session.scalars(stmt).first()
            if tenant and token == tenant.admin_token:
                # Set tenant context for admin token
                tenant_dict = {
                    "tenant_id": tenant.tenant_id,
                    "name": tenant.name,
                    "subdomain": tenant.subdomain,
                    "ad_server": tenant.ad_server,
                }
                set_current_tenant(tenant_dict)
                return f"admin_{tenant.tenant_id}"
        else:
            # No tenant specified - search globally
            stmt = select(Principal).filter_by(access_token=token)
            principal = session.scalars(stmt).first()
            if principal:
                # Found principal - set tenant context
                stmt = select(Tenant).filter_by(tenant_id=principal.tenant_id, is_active=True)
                tenant = session.scalars(stmt).first()
                if tenant:
                    tenant_dict = {
                        "tenant_id": tenant.tenant_id,
                        "name": tenant.name,
                        "subdomain": tenant.subdomain,
                        "ad_server": tenant.ad_server,
                    }
                    set_current_tenant(tenant_dict)
                    return principal.principal_id

        return None

    try:
        return execute_with_retry(_lookup_principal)
    except Exception as e:
        console.print(f"[red]Database error during principal lookup: {e}[/red]")
        return None


def get_principal_from_context(context: Context | None) -> str | None:
    """Extract principal ID from the FastMCP context using x-adcp-auth header.

    Args:
        context: FastMCP context object

    Returns:
        Principal ID if authenticated, None otherwise
    """
    if not context:
        return None

    try:
        # Extract token from headers
        token = None
        headers_found = {}

        if hasattr(context, "meta") and isinstance(context.meta, dict):
            headers_found = context.meta.get("headers", {})
            console.print(f"[blue]Headers from context.meta: {list(headers_found.keys())}[/blue]")
        elif hasattr(context, "headers"):
            headers_found = context.headers
            console.print(f"[blue]Headers from context.headers: {list(headers_found.keys())}[/blue]")
        else:
            console.print("[red]No headers found in context![/red]")
            return None

        # Case-insensitive header lookup (HTTP headers are case-insensitive)
        token = None
        for key, value in headers_found.items():
            if key.lower() == "x-adcp-auth":
                token = value
                break

        if not token:
            console.print(f"[red]No x-adcp-auth token found. Available headers: {list(headers_found.keys())}[/red]")
            return None

        console.print(f"[green]Found token: {token[:20]}...[/green]")

        # Validate token and get principal ID
        return get_principal_from_token(token)

    except Exception as e:
        console.print(f"[red]Error extracting principal from context: {e}[/red]")
        return None


def get_principal_object(principal_id: str) -> Principal | None:
    """Get the Principal object with platform mappings using retry logic.

    Args:
        principal_id: The principal ID to look up

    Returns:
        Principal object or None if not found
    """
    if not principal_id:
        return None

    def _get_principal_object(session):
        from src.core.schemas import Principal as PrincipalSchema

        # Query the database for the principal
        stmt = select(Principal).filter_by(principal_id=principal_id)
        db_principal = session.scalars(stmt).first()

        if db_principal:
            # Convert to Pydantic model
            return PrincipalSchema(
                principal_id=db_principal.principal_id,
                name=db_principal.name,
                platform_mappings=db_principal.platform_mappings or {},
            )

        return None

    try:
        return execute_with_retry(_get_principal_object)
    except Exception as e:
        console.print(f"[red]Database error during principal object lookup: {e}[/red]")
        return None
