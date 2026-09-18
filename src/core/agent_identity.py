"""Where a tenant's agent is reachable — derived from stored state, once.

One function answers it, and everything that publishes or byte-matches an agent URL
reads that one answer. The point is not tidiness: a tenant that answered on several
hosts, or whose scheme was taken from a request header, would publish several
identities, and a counterparty comparing the URL it invoked against the one we
published would fail with no diagnostic.

So the scheme and host come from the tenant row, never from a request header —
not ``Host``, not ``X-Forwarded-Proto``. The ladder over those that used to sit in
``src/app.py`` is gone: it published whatever host the caller asked for, behind
nothing but a syntax check.

Scope. This module is the DERIVATION and nothing else. The agent card reads it
through :mod:`src.services.seller_capabilities`, which is also what
``get_adcp_capabilities`` renders from, so the card and the tool cannot name
different URLs for one seller. The trust-root documents that also build on this
origin — brand.json, adagents.json, the JWKS, and the entry ids addressing them —
belong to the RFC 9421 signing work (#1291) and live on its branch together with the
signing-key repository and migration they need. They are not re-declared here with
no caller.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.config import get_settings
from src.core.domain_config import _get_protocol_for_domain

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.core.tenant_context import TenantContext

# The paths a counterparty actually reaches this agent at, keyed by transport.
# Values are what the running app resolves to AFTER any redirect it issues —
# ``/mcp`` 307s to ``/mcp/``, ``/a2a`` does not redirect. This table is what stops
# the URL we publish drifting from the mount the app actually serves.
AGENT_ENDPOINT_PATHS: dict[str, str] = {"mcp": "/mcp/", "a2a": "/a2a"}


def _agent_host(tenant: TenantContext) -> str | None:
    """The host this tenant is reachable at, or None when it declares none.

    ONE source: ``virtual_host``, the origin the tenant says it is served at (it may carry
    a port, because the card publishes this string and a client connects to what the card
    says). Stored state, never request state.

    The fallback that stood here built ``f"{subdomain}.{SALES_AGENT_DOMAIN}"`` — and that is
    what published ``ci-test.sales-agent.example.com`` on the CI tenant's card, a name
    nothing on the network served, which the A2A runner followed and failed every check
    against. A second derivation of "where is this tenant" is a second chance to be wrong
    about it, and the tenant already answers the question. A tenant that declares no host
    has no agent URL; inventing one produces a name nobody can reach.
    """
    return tenant.virtual_host


def canonical_agent_url(tenant: TenantContext) -> str:
    """The tenant's canonical ORIGIN — scheme + host, no path, no trailing slash.

    An anchor rather than an endpoint: every URL this agent publishes for *tenant* is
    this string plus a path from :data:`AGENT_ENDPOINT_PATHS`.

    Takes the typed read projection the resolver hands on, not the ORM row. This is a
    read, it touches one column (``virtual_host``), and ``TenantContext`` carries it — so
    nothing here opens a session, and a caller holding a tenant already has what it needs.
    """
    host = _agent_host(tenant)
    if host:
        return f"{_get_protocol_for_domain(host)}://{host}"

    # Deployment-level base: single-tenant installs with no per-tenant host. Ranked
    # BELOW the tenant's own host deliberately — a deployment-wide literal that
    # overrode a per-tenant identity would collapse every tenant onto one URL, which
    # is the defect this module exists to remove.
    runtime = get_settings().runtime
    if runtime.adcp_agent_url:
        return runtime.adcp_agent_url.rstrip("/")

    # Development default — the port the sales agent serves on locally.
    return runtime.local_base_url
