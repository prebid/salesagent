"""Landing page generation for tenant-specific pages."""

import html
import os

from adcp import get_adcp_spec_version
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.core.agent_identity import AGENT_CARD_PATH, AGENT_ENDPOINT_PATHS
from src.core.tenant_context import TenantContext
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


def _tenant_base_url(tenant_row: dict) -> str:
    """The origin every URL on this page is built from: the tenant's STORED one.

    The same string the agent card publishes, through the same accessor
    (``tenant.agent_url`` over the host the tenant declares), so the page and the
    card cannot name different origins for one tenant. Reading the request's ``Host`` here
    would publish whatever spelling the caller sent: a request resolves with or without the
    port, so a tenant stored at ``host:8443`` would be advertised as ``https://host`` by the
    page and ``https://host:8443`` by its card, and one of the two reaches nothing (#1845).

    Args:
        tenant_row: Tenant data from database, as ``serialize_tenant_to_dict`` shapes it

    Returns:
        Scheme plus host, no path and no trailing slash
    """
    # Through the projection, not the raw column: it folds the host to lowercase, which is
    # what the card's own reader does, so both sides publish the byte-identical string.
    return TenantContext.from_dict(tenant_row).agent_url


def _generate_pending_configuration_page(tenant_row: dict) -> str:
    """Generate pending configuration page for unconfigured tenants.

    Args:
        tenant_row: Tenant data from database, as ``serialize_tenant_to_dict`` shapes it

    Returns:
        Simple HTML page indicating pending configuration
    """
    tenant_name = html.escape(tenant_row.get("name", "Unknown Publisher"))
    tenant_id = tenant_row.get("tenant_id", "default")
    admin_url = f"{_tenant_base_url(tenant_row)}/admin/tenant/{tenant_id}"

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


def generate_tenant_landing_page(tenant_row: dict) -> str:
    """Generate HTML content for tenant landing page.

    Every URL on the page names the tenant's stored origin (:func:`_tenant_base_url`), which
    is what the agent card publishes too, so a buyer reading both is told one thing.

    Args:
        tenant_row: Tenant data from database (``serialize_tenant_to_dict``): name, virtual_host, etc.

    Returns:
        Complete HTML page as string

    Raises:
        Exception: If template rendering fails
    """
    # Check if tenant is configured (has ad server connection)
    from src.core.tenant_status import is_tenant_ad_server_configured

    tenant_id = tenant_row.get("tenant_id")
    is_configured = is_tenant_ad_server_configured(tenant_id) if tenant_id else False

    # If not configured, show pending configuration page
    if not is_configured:
        return _generate_pending_configuration_page(tenant_row)

    base_url = _tenant_base_url(tenant_row)

    # Every endpoint, and the admin, on the one origin. A tenant is served at the host it
    # declares whether the deployment carries one tenant or many, so there is no second
    # origin for a deployment mode to choose between (#1845).
    # The paths come from AGENT_ENDPOINT_PATHS, which is what src/app.py mounts and what the
    # agent card publishes. A machine client reads the card, so a path spelled here is copy
    # for a human -- and a human copying "/mcp" where FastMCP mounts "/mcp/", or the bare
    # origin where A2A answers at "/a2a", gets sent somewhere that is not the endpoint.
    mcp_url = f"{base_url}{AGENT_ENDPOINT_PATHS['mcp']}"
    a2a_url = f"{base_url}{AGENT_ENDPOINT_PATHS['a2a']}"
    agent_card_url = f"{base_url}{AGENT_CARD_PATH}"
    admin_url = f"{base_url}/admin/"

    # Prepare template context
    template_context = {
        # Tenant information (escaped by Jinja2 auto-escape)
        "tenant_name": tenant_row.get("name", "Unknown Publisher"),
        # URLs
        "mcp_url": mcp_url,
        "a2a_url": a2a_url,
        "agent_card_url": agent_card_url,
        "admin_url": admin_url,
        "adcp_docs_url": "https://adcontextprotocol.org",
        # Additional context
        "page_title": f"{tenant_row.get('name', 'Publisher')} Sales Agent",
        "version": get_version(),
        "adcp_version": get_adcp_spec_version(),
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
