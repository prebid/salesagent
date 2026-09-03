"""Helper for the DB-free agent-card unit tests (#1291, trust-root work item A3).

Since A3 the agent card advertises the tenant's CANONICAL agent URL, read from
that tenant's stored host — so ``/.well-known/agent-card.json`` performs a
host->tenant lookup where it previously performed none. A DB error is NOT
treated as "no tenant here" (that would be exactly the quiet failure the trust
root exists to prevent: a card advertising one identity while brand.json
publishes another), so the endpoint fails loudly when the lookup cannot run.

Unit tests have no database. The ones that grade the card's SHAPE, or the
header-fallback branch itself, therefore have to state the precondition they
were previously getting by accident: this Host routes to no tenant.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

from src.core.domain_routing import RoutingResult


@contextmanager
def host_routes_to_no_tenant(effective_host: str = "testserver") -> Iterator[None]:
    """Make host->tenant resolution answer "no tenant", without a database.

    This is a real production branch — the shared sales-agent domain, or a host
    no tenant has claimed — and it is the branch in which the agent card still
    derives its URL from request headers.
    """
    with patch("src.app.route_landing_page", return_value=RoutingResult("custom_domain", None, effective_host)):
        yield
