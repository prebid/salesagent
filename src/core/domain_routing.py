"""Which landing page a host gets.

- a host a tenant declares as its ``virtual_host`` → that tenant's agent page
- an admin domain → admin login
- anything else → the fallback page

Subdomain routing is GONE. A deployment serving a tenant at
``acme.example.com`` sets that tenant's ``virtual_host`` to it, which is the one
lookup here; the second derivation it replaced needed a ``SALES_AGENT_DOMAIN``
setting and disagreed with the first often enough to publish an agent card naming
a host nothing served.
"""

from dataclasses import dataclass
from typing import Literal

# Import existing tenant lookup functions from config_loader
# This ensures all servers (MCP, Admin, A2A) use the same lookup logic
from src.core.config_loader import get_tenant_by_virtual_host
from src.core.domain_config import is_admin_domain
from src.core.http_utils import requested_host


@dataclass
class RoutingResult:
    """Result of domain routing decision.

    Attributes:
        type: Type of routing decision (custom_domain, admin, unknown)
        tenant: Tenant dict if found, None otherwise
        effective_host: The host used for routing decision
    """

    type: Literal["custom_domain", "admin", "unknown"]
    tenant: dict | None
    effective_host: str


def route_landing_page(request_headers: dict) -> RoutingResult:
    """Determine landing page routing based on request headers.

    This function centralizes all domain routing logic used by both
    MCP server and Admin UI. It examines headers to determine:
    1. What type of domain is being accessed
    2. Whether a tenant exists for that domain
    3. What the appropriate response should be

    Args:
        request_headers: Dict of HTTP headers (case-insensitive keys supported)

    Returns:
        RoutingResult indicating routing decision and tenant if found

    Routing logic:
    - Admin domain → type="admin"
    - Any other host → type="custom_domain", carrying the tenant that declares that
      host as its ``virtual_host``, or None when no tenant declares it
    - No host at all → type="unknown"

    Examples:
        Admin domain routing:
        >>> route_landing_page({"Host": "admin.sales-agent.example.com"})
        RoutingResult(type="admin", tenant=None, effective_host="admin.sales-agent.example.com")

        A host some tenant declares:
        >>> route_landing_page({"Host": "sales-agent.publisher.com"})
        RoutingResult(type="custom_domain", tenant={...}, effective_host="sales-agent.publisher.com")

        A host no tenant declares:
        >>> route_landing_page({"Host": "nobody.example.com"})
        RoutingResult(type="custom_domain", tenant=None, effective_host="nobody.example.com")

        A request that reached here through a proxy names its host the same way. Whatever
        that proxy had to rewrite, it rewrote before the app, so this function reads one
        thing.
    """
    # The host this request is for. One owner (src/core/http_utils.py), so this module
    # has no header name of its own to disagree with anyone about.
    effective_host = requested_host(request_headers)

    if not effective_host:
        return RoutingResult("unknown", None, "")

    # Admin domain check - uses is_admin_domain() which validates against the
    # configured admin domain. This prevents spoofing via malicious domains that
    # start with 'admin.' but aren't our legitimate admin domain
    # (e.g., admin.sales-agent.example.com is valid, admin.malicious.com is not)
    if is_admin_domain(effective_host):
        return RoutingResult("admin", None, effective_host)

    # ONE lookup: the host a tenant declares it is served at. The branch that used to sit
    # in front of this asked whether the host was under SALES_AGENT_DOMAIN and, if so, took
    # a different path entirely — a second derivation of the same fact, deleted with the
    # subdomain strategy.
    tenant = get_tenant_by_virtual_host(effective_host)
    # ``custom_domain`` even when no tenant matched: the caller distinguishes "a host asking
    # for a tenant we do not serve" (it can offer signup) from "no host at all" (the generic
    # fallback above). Which of those to show is the caller's decision, not this function's.
    return RoutingResult("custom_domain", tenant, effective_host)
