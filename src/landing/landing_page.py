"""Landing page generation for tenant-specific pages."""

import html
import os

from adcp import get_adcp_version
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.core.domain_config import (
    extract_subdomain_from_host,
    get_sales_agent_url,
    get_tenant_url,
    is_sales_agent_domain,
)
from src.core.version import get_version


def _get_jinja_env() -> Environment:
    """Get configured Jinja2 environment for landing page templates."""
    template_dir = os.path.join(os.path.dirname(__file__), "templates")

    return Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _determine_base_url(virtual_host: str | None = None) -> str:
    """Determine the base URL for the current environment.

    Args:
        virtual_host: Virtual host if provided (e.g., from request.host or Apx-Incoming-Host header)

    Returns:
        Base URL for generating endpoint URLs
    """
    # Check if we're in production
    if os.getenv("PRODUCTION") == "true":
        if virtual_host:
            return f"https://{virtual_host}"
        # Fallback to production domain (if configured)
        if url := get_sales_agent_url():
            return url
        # If no SALES_AGENT_DOMAIN configured, fall through to development mode

    # Development/local mode: use virtual_host if provided
    if virtual_host:
        # Use http for localhost, https for everything else
        scheme = "http" if "localhost" in virtual_host or virtual_host.startswith("127.") else "https"
        return f"{scheme}://{virtual_host}"

    # Local development fallback (should rarely be reached)
    port = os.getenv("ADCP_SALES_PORT", "8080")
    return f"http://localhost:{port}"


def _extract_tenant_subdomain(tenant: dict, virtual_host: str | None = None) -> str | None:
    """Extract tenant subdomain from tenant data or virtual host.

    Args:
        tenant: Tenant data from database
        virtual_host: Virtual host domain if available

    Returns:
        Tenant subdomain if determinable
    """
    # First try virtual host
    if virtual_host:
        # Extract subdomain from virtual host using domain config
        subdomain = extract_subdomain_from_host(virtual_host)
        if subdomain:
            return subdomain
        elif "." in virtual_host:
            # Generic virtual host, use first part
            return virtual_host.split(".")[0]

    # Fallback to tenant subdomain field
    if tenant.get("subdomain"):
        return tenant["subdomain"]

    # Fallback to tenant_id
    return tenant.get("tenant_id")


def _generate_pending_configuration_page(tenant: dict, virtual_host: str | None = None) -> str:
    """Generate pending configuration page for unconfigured tenants.

    Args:
        tenant: Tenant data from database
        virtual_host: Virtual host domain if applicable

    Returns:
        Simple HTML page indicating pending configuration
    """
    tenant_name = html.escape(tenant.get("name", "Unknown Publisher"))
    tenant_id = tenant.get("tenant_id", "default")
    base_url = _determine_base_url(virtual_host)
    admin_url = f"{base_url}/tenant/{tenant_id}"

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <title>{tenant_name} - Pending Configuration</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                margin: 0;
                padding: 2rem;
            }}
            .container {{
                background: white;
                border-radius: 8px;
                padding: 3rem 2rem;
                max-width: 600px;
                text-align: center;
                box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            }}
            .icon {{
                font-size: 4rem;
                margin-bottom: 1rem;
            }}
            h1 {{
                color: #2c3e50;
                margin-bottom: 0.5rem;
                font-size: 2rem;
            }}
            .subtitle {{
                color: #7f8c8d;
                font-size: 1.1rem;
                margin-bottom: 2rem;
            }}
            .message {{
                background: #f8f9fa;
                border-radius: 6px;
                padding: 1.5rem;
                margin-bottom: 2rem;
                text-align: left;
            }}
            .message p {{
                margin: 0.5rem 0;
                line-height: 1.6;
            }}
            .admin-link {{
                display: inline-block;
                background: #4285F4;
                color: white;
                padding: 1rem 2rem;
                border-radius: 6px;
                text-decoration: none;
                font-weight: 600;
                transition: transform 0.2s, box-shadow 0.2s;
            }}
            .admin-link:hover {{
                transform: translateY(-2px);
                box-shadow: 0 6px 12px rgba(66, 133, 244, 0.3);
            }}
            .footer {{
                margin-top: 2rem;
                color: #95a5a6;
                font-size: 0.9rem;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="icon">⚙️</div>
            <h1>Pending Configuration</h1>
            <p class="subtitle">{tenant_name}</p>

            <div class="message">
                <p><strong>This sales agent is not yet configured.</strong></p>
                <p>To activate this agent, the owner needs to:</p>
                <ul style="text-align: left; margin: 1rem 0;">
                    <li>Connect an ad server (Google Ad Manager, Kevel, etc.)</li>
                    <li>Configure inventory and products</li>
                    <li>Complete initial setup</li>
                </ul>
            </div>

            <a href="{html.escape(admin_url)}" class="admin-link">
                Sign In to Configure →
            </a>

            <div class="footer">
                <p>Powered by <a href="https://adcontextprotocol.org" style="color: #3498db; text-decoration: none;">Ad Context Protocol</a></p>
            </div>
        </div>
    </body>
    </html>
    """


def generate_tenant_landing_page(tenant: dict, virtual_host: str | None = None) -> str:
    """Generate HTML content for tenant landing page.

    Args:
        tenant: Tenant data from database containing name, subdomain, etc.
        virtual_host: Virtual host domain if applicable (e.g., from Apx-Incoming-Host)

    Returns:
        Complete HTML page as string

    Raises:
        Exception: If template rendering fails
    """
    # Check if tenant is configured (has ad server connection)
    from src.core.tenant_status import is_tenant_ad_server_configured

    tenant_id = tenant.get("tenant_id")
    is_configured = is_tenant_ad_server_configured(tenant_id) if tenant_id else False

    # If not configured, show pending configuration page
    if not is_configured:
        return _generate_pending_configuration_page(tenant, virtual_host)

    # Get base URL for this environment
    base_url = _determine_base_url(virtual_host)

    # Extract tenant subdomain
    tenant_subdomain = _extract_tenant_subdomain(tenant, virtual_host)

    # Generate endpoint URLs
    mcp_url = f"{base_url}/mcp"
    a2a_url = base_url  # A2A endpoint is at the root, not /a2a
    agent_card_url = f"{base_url}/.well-known/agent.json"

    # Admin URL: Depends on deployment mode
    from src.core.config_loader import is_single_tenant_mode

    if is_single_tenant_mode():
        # Single-tenant mode: use full URLs based on virtual_host (passed from request)
        # This ensures users see copy-pasteable URLs like http://localhost:55030/mcp
        if virtual_host:
            # Use http for localhost, https for everything else
            scheme = "http" if "localhost" in virtual_host or virtual_host.startswith("127.") else "https"
            single_tenant_base = f"{scheme}://{virtual_host}"
        else:
            # Fallback to base_url if no virtual_host
            single_tenant_base = base_url
        mcp_url = f"{single_tenant_base}/mcp"
        a2a_url = single_tenant_base  # A2A is at root
        agent_card_url = f"{single_tenant_base}/.well-known/agent.json"
        admin_url = f"{single_tenant_base}/admin/"
    else:
        # Multi-tenant mode: For external domains, use subdomain; otherwise use current domain
        is_external_domain = virtual_host and not is_sales_agent_domain(virtual_host)
        if is_external_domain and tenant_subdomain:
            # External domain: Point admin to tenant subdomain
            if os.getenv("PRODUCTION") == "true":
                admin_url = f"{get_tenant_url(tenant_subdomain)}/admin/"
            else:
                # Local dev: Use localhost with subdomain simulation
                admin_url = f"http://{tenant_subdomain}.localhost:8001/admin/"
        else:
            # Same domain or subdomain: Use base_url
            admin_url = f"{base_url}/admin/"

    # Prepare template context
    template_context = {
        # Tenant information (escaped by Jinja2 auto-escape)
        "tenant_name": tenant.get("name", "Unknown Publisher"),
        "tenant_subdomain": tenant_subdomain,
        # URLs
        "base_url": base_url,
        "mcp_url": mcp_url,
        "a2a_url": a2a_url,
        "agent_card_url": agent_card_url,
        "admin_url": admin_url,
        "adcp_docs_url": "https://adcontextprotocol.org",
        # Virtual host info
        "virtual_host": virtual_host,
        "is_production": os.getenv("PRODUCTION") == "true",
        # Additional context
        "page_title": f"{tenant.get('name', 'Publisher')} Sales Agent",
        "version": get_version(),
        "adcp_version": get_adcp_version(),
    }

    # Load and render template
    env = _get_jinja_env()
    template = env.get_template("tenant_landing.html")

    return template.render(**template_context)


def generate_fallback_landing_page(error_message: str = "Tenant not found") -> str:
    """Generate a fallback landing page when tenant lookup fails.

    Args:
        error_message: Error message to display

    Returns:
        Simple HTML error page
    """
    # Simple fallback HTML without template
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <title>AdCP Sales Agent</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                margin: 0;
                padding: 2rem;
            }}
            .container {{
                background: white;
                border-radius: 8px;
                padding: 2rem;
                max-width: 500px;
                text-align: center;
                box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            }}
            h1 {{ color: #e74c3c; }}
            .admin-link {{
                display: inline-block;
                background: #007bff;
                color: white;
                padding: 0.75rem 1.5rem;
                border-radius: 4px;
                text-decoration: none;
                margin-top: 1rem;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>AdCP Sales Agent</h1>
            <p>{html.escape(error_message)}</p>
            <p>Please check the URL or contact your administrator.</p>
            <a href="/admin/" class="admin-link">Go to Admin Dashboard</a>
        </div>
    </body>
    </html>
    """
