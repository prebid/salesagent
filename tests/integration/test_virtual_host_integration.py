"""Integration tests for virtual host functionality.

HEADER PARSING IS NOT GRADED HERE, and a test that defines a local ``MockContext``, puts
headers into it and asserts on what it just stored imports nothing from ``src`` — such a
test passes under any header priority, including one where two production resolvers
disagree. Which tenant a host resolves is graded on all four transports by
``tests/bdd/features/local-tenant-identification-routes.feature``.

What is here is the tests that call production, plus the case-folding class below — which
lives here rather than in BDD because two of the three readers it drives (the landing page
and the agent card) are ROOT HTTP endpoints outside the tool dispatch the BDD harness
speaks, and the point is to drive all three in one place.
"""

import pytest
from sqlalchemy import text

from src.core.agent_identity import AGENT_ENDPOINT_PATHS
from src.core.config_loader import get_tenant_by_virtual_host
from src.core.domain_routing import route_landing_page
from src.core.http_utils import hostname_of
from src.core.resolved_identity import public_identity_for
from src.services.seller_capabilities import describe_seller
from tests.factories import TenantFactory
from tests.harness._base import IntegrationEnv
from tests.helpers.credentials import credential_headers

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

#: A stored origin carrying uppercase in every position a host can carry it: the first
#: label, a middle label and the TLD. Under ``.test``, which RFC 2606 reserves.
STORED_MIXED_CASE = "Probe-Case.AdCP.test"

#: Every spelling of that host a client can put on the wire. A ``Host`` is
#: case-insensitive per RFC 7230 §5.4 (it names a DNS name, and DNS is case-insensitive),
#: so all of these address the same tenant and the seller has no licence to serve one and
#: not another. The port variant is here because a proxy forwards whatever the origin
#: listens on.
CLIENT_SPELLINGS = [
    STORED_MIXED_CASE,
    STORED_MIXED_CASE.lower(),
    STORED_MIXED_CASE.upper(),
    f"{STORED_MIXED_CASE.lower()}:8443",
]


class _VhostEnv(IntegrationEnv):
    """Bare integration env — no external patches. Commits so a fresh session sees the row."""

    EXTERNAL_PATCHES: dict[str, str] = {}

    def seed_row_stored_mixed_case(self) -> str:
        """A committed tenant whose COLUMN holds uppercase — the legacy row.

        Written with a raw UPDATE on purpose, and that is the whole value of this fixture:
        ``Tenant.virtual_host``'s validator folds every assignment, so no production path
        can produce such a row, and a factory call would silently seed a lowercase one.
        Then the readers below would pass on the strength of the WRITE-side fold and say
        nothing about the read side — a fixture that cannot express the defect cannot
        falsify the fix.

        ``virtual_host_name`` is written in the same statement, because that is the row the
        migration produces: it derives the name with ``urlsplit().hostname``, which folds,
        so a legacy row carries a mixed-case origin and a folded name. Writing only the
        origin would seed a desynced pair instead, and nothing can produce one — every
        production write of ``virtual_host`` is an ORM assignment, so the validator derives
        the name with it.
        """
        tenant = TenantFactory(tenant_id="vh_case", virtual_host="placeholder.example.com", is_active=True)
        self._commit_factory_data()
        self._session.execute(
            text("UPDATE tenants SET virtual_host = :host, virtual_host_name = :name WHERE tenant_id = :tid"),
            {
                "host": STORED_MIXED_CASE,
                "name": hostname_of(STORED_MIXED_CASE),
                "tid": tenant.tenant_id,
            },
        )
        self._session.commit()
        return tenant.tenant_id


class TestVirtualHostIntegration:
    """Test virtual host integration across multiple components."""

    def test_virtual_host_function_integration(self, integration_db):
        """Test that virtual host lookup function handles non-existent domains gracefully."""
        # This is a real integration test - calls the actual function
        # with a domain that shouldn't exist
        result = get_tenant_by_virtual_host("definitely-does-not-exist.invalid")

        # Should return None for non-existent virtual hosts
        assert result is None


class TestMixedCaseVirtualHostResolvesOnEveryReader:
    """A tenant stored with uppercase is served, whatever case the client sends.

    THREE READERS, GRADED TOGETHER ON PURPOSE. The resolver (``public_identity_for``,
    which every tool request and the agent card go through), the landing page
    (``route_landing_page``) and the card's description (``describe_seller``) reach the
    tenant through two different repository methods over one predicate. A fix applied at
    one of them is not a fix at the others, and a case mismatch took all three dark at
    once — the tenant was reachable only through the ``x-adcp-tenant`` literal-id path.

    ``virtual_host`` is all lowercase end to end (``Tenant.virtual_host``'s validator
    lowercases on write, and both routing lookups fold the column), so these also grade
    that a row ALREADY stored mixed-case still resolves rather than needing a data fix.
    """

    @pytest.mark.parametrize("spelling", CLIENT_SPELLINGS)
    def test_the_resolver_identifies_the_tenant(self, integration_db, spelling):
        with _VhostEnv() as env:
            tenant_id = env.seed_row_stored_mixed_case()

            identity = public_identity_for(credential_headers(host=spelling))

            assert identity.tenant is not None, f"Host {spelling!r} resolved no tenant"
            assert identity.tenant.tenant_id == tenant_id

    @pytest.mark.parametrize("spelling", CLIENT_SPELLINGS)
    def test_the_landing_page_finds_the_tenant(self, integration_db, spelling):
        with _VhostEnv() as env:
            tenant_id = env.seed_row_stored_mixed_case()

            result = route_landing_page(credential_headers(host=spelling))

            assert result.tenant is not None, f"Host {spelling!r} routed to no tenant, so the landing page is blank"
            assert result.tenant["tenant_id"] == tenant_id

    @pytest.mark.parametrize("spelling", CLIENT_SPELLINGS)
    def test_the_agent_card_describes_the_tenant(self, integration_db, spelling):
        with _VhostEnv() as env:
            env.seed_row_stored_mixed_case()

            seller = describe_seller(public_identity_for(credential_headers(host=spelling)))

            assert seller.agent_url is not None, (
                f"Host {spelling!r} produced a card with no url, which the route refuses as CONFIGURATION_ERROR"
            )
            assert seller.agent_url == f"https://{STORED_MIXED_CASE.lower()}{AGENT_ENDPOINT_PATHS['a2a']}", (
                "the card publishes a url that is not the stored host folded to lowercase"
            )

    def test_the_write_path_stores_lowercase(self, integration_db):
        """The COLUMN holds no uppercase, whatever the assignment was handed.

        Read back with raw SQL on purpose: the read-side folds would answer ``lowercase``
        for a mixed-case column and this assertion would say nothing about the write.
        """
        with _VhostEnv() as env:
            TenantFactory(tenant_id="vh_write", virtual_host=STORED_MIXED_CASE, is_active=True)
            env._commit_factory_data()

            stored = env._session.execute(
                text("SELECT virtual_host FROM tenants WHERE tenant_id = 'vh_write'")
            ).scalar_one()

            assert stored == STORED_MIXED_CASE.lower(), (
                f"the column holds {stored!r}: a case mismatch is still representable"
            )
