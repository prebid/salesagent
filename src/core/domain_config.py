"""
Domain configuration utilities.

This module answers questions about THIS DEPLOYMENT's own domains — the admin
domain, the OAuth redirect, the session cookie — so they are configurable rather
than vendor-hardcoded.

It says nothing about which tenant a request belongs to. A tenant is identified by
the host it declares (``Tenant.virtual_host``) or by an explicit ``x-adcp-tenant``
header, and by nothing else.
"""

from src.core.config import get_settings


def _is_localhost(domain: str | None) -> bool:
    """Check if domain is localhost or 127.0.0.1."""
    if not domain:
        return False
    # Strip port if present
    host = domain.split(":")[0]
    return host in ("localhost", "127.0.0.1")


def _get_protocol_for_domain(domain: str | None) -> str:
    """Return http for localhost, https for production domains."""
    return "http" if _is_localhost(domain) else "https"


def get_sales_agent_domain() -> str | None:
    """Get this deployment's own domain (e.g., sales-agent.example.com).

    This is the domain the deployment answers on — what the admin URL, the OAuth
    redirect and the session cookie are built from. It is NOT a suffix tenants are
    resolved under.

    Returns:
        The configured SALES_AGENT_DOMAIN, or None if not configured.
    """
    return get_settings().runtime.sales_agent_domain


def get_admin_domain() -> str | None:
    """Get the admin domain (e.g., admin.sales-agent.example.com).

    Returns:
        The configured ADMIN_DOMAIN, or constructs from SALES_AGENT_DOMAIN,
        or None if neither is configured.
    """
    return get_settings().runtime.admin_domain_or_derived


def get_super_admin_domain() -> str | None:
    """Get the domain for super admin emails (e.g., example.com).

    Returns:
        The configured SUPER_ADMIN_DOMAIN, or None if not configured.
    """
    return get_settings().runtime.super_admin_domain


def get_sales_agent_url(protocol: str = "https") -> str | None:
    """Get the full sales agent URL (e.g., https://sales-agent.example.com).

    Returns:
        The full URL, or None if SALES_AGENT_DOMAIN is not configured.
    """
    if domain := get_sales_agent_domain():
        return f"{protocol}://{domain}"
    return None


def get_admin_url(protocol: str = "https") -> str | None:
    """Get the full admin URL (e.g., https://admin.sales-agent.example.com).

    Returns:
        The full URL, or None if domain is not configured.
    """
    if domain := get_admin_domain():
        return f"{protocol}://{domain}"
    return None


def is_admin_domain(host: str) -> bool:
    """
    Check if the given host is the admin domain.

    Args:
        host: The hostname to check

    Returns:
        True if the host is the admin domain.
        Returns False if admin domain is not configured.
    """
    admin_domain = get_admin_domain()
    if not admin_domain:
        return False
    return host == admin_domain or host.startswith(f"{admin_domain}:")


def get_oauth_redirect_uri(protocol: str = "https") -> str | None:
    """
    Get the OAuth redirect URI.

    Returns:
        The OAuth callback URL (e.g., https://sales-agent.example.com/admin/auth/google/callback)
        or None if not configured.
    """
    if configured := get_settings().auth.google_oauth_redirect_uri:
        return configured

    if url := get_sales_agent_url(protocol):
        return f"{url}/admin/auth/google/callback"
    return None


def get_session_cookie_domain() -> str | None:
    """
    Get the session cookie domain (with leading dot for subdomain sharing).

    Returns:
        The cookie domain (e.g., ".sales-agent.example.com")
        or None if SALES_AGENT_DOMAIN is not configured.
    """
    if sales_domain := get_sales_agent_domain():
        return f".{sales_domain}"
    return None


def get_support_email() -> str:
    """
    Get the support email address for user-facing messages.

    Returns:
        The configured SUPPORT_EMAIL, or a placeholder if not set.
        Configure via environment variable for production deployments.
    """
    return get_settings().runtime.support_email
