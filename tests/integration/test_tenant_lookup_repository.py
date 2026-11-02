"""Integration tests for TenantLookupRepository against real PostgreSQL.

The repository answers two DIFFERENT questions on the same table, and the difference is
the subject here: a uniqueness check ("is this key taken, by anyone?") must see an
inactive tenant, because an inactive tenant still occupies its subdomain in the index;
a routing lookup ("which tenant serves this request?") must not, because a deactivated
tenant is not served. Both are graded below, side by side, so a future filter added to
the wrong half fails.

BDD cannot reach this: the subject is a repository contract, not a buyer-visible
outcome, and the two halves differ only in rows a wire never shows.
"""

from datetime import UTC, datetime

import pytest

from src.core.database.repositories import TenantLookupRepository
from tests.factories import TenantFactory
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _RepoEnv(IntegrationEnv):
    """Bare integration env for repository tests -- no external patches."""

    EXTERNAL_PATCHES: dict[str, str] = {}

    def get_session(self):
        """Expose session for direct repository construction."""
        self._commit_factory_data()
        return self._session


class TestRoutingLookupsSkipInactiveTenants:
    """A routing lookup answers with an ACTIVE tenant or with nothing."""

    def test_virtual_host_of_an_inactive_tenant_resolves_to_nothing(self, integration_db):
        with _RepoEnv() as env:
            TenantFactory(tenant_id="tlr_off", virtual_host="off.example.com", is_active=False)
            repo = TenantLookupRepository(env.get_session())

            assert repo.find_active_by_virtual_host("off.example.com") is None
            assert repo.active_tenant_id_for_virtual_host("off.example.com") is None

    def test_virtual_host_of_an_active_tenant_resolves(self, integration_db):
        with _RepoEnv() as env:
            TenantFactory(tenant_id="tlr_on", virtual_host="on.example.com", is_active=True)
            repo = TenantLookupRepository(env.get_session())

            assert repo.find_active_by_virtual_host("on.example.com").tenant_id == "tlr_on"
            assert repo.active_tenant_id_for_virtual_host("on.example.com") == "tlr_on"

    def test_id_of_an_inactive_tenant_resolves_to_nothing(self, integration_db):
        with _RepoEnv() as env:
            TenantFactory(tenant_id="tlr_id_off", is_active=False)
            repo = TenantLookupRepository(env.get_session())

            assert repo.find_active_by_id("tlr_id_off") is None

    def test_id_of_an_active_tenant_resolves(self, integration_db):
        with _RepoEnv() as env:
            TenantFactory(tenant_id="tlr_id_on", is_active=True)
            repo = TenantLookupRepository(env.get_session())

            assert repo.find_active_by_id("tlr_id_on").tenant_id == "tlr_id_on"


class TestVirtualHostMatchesHostToHost:
    """A port says how a deployment is reached, not which seller it is.

    The same tenant answers at ``host`` and at ``host:8443``: which spelling a client sends
    depends on the port its origin uses, and ``@T-TENANTID-host-with-port``
    (``tests/bdd/features/local-tenant-identification-routes.feature``) pins that on every
    transport.

    The match is unambiguous because ``ux_tenants_virtual_host_name`` is UNIQUE on
    ``virtual_host_name``: one name is held by one tenant, so a port-insensitive comparison
    can reach at most one row. That name is derived by ``urlsplit().hostname`` and stored,
    never computed in SQL -- ``test_two_ipv6_tenants_do_not_collide`` is why that matters.
    """

    def test_request_naming_a_port_matches_a_portless_row(self, integration_db):
        with _RepoEnv() as env:
            TenantFactory(tenant_id="tlr_p1", virtual_host="ported.example.com")
            repo = TenantLookupRepository(env.get_session())

            assert repo.find_active_by_virtual_host("ported.example.com:8443").tenant_id == "tlr_p1"

    def test_portless_request_matches_a_row_that_stores_a_port(self, integration_db):
        with _RepoEnv() as env:
            TenantFactory(tenant_id="tlr_p2", virtual_host="stored.example.com:8443")
            repo = TenantLookupRepository(env.get_session())

            assert repo.find_active_by_virtual_host("stored.example.com").tenant_id == "tlr_p2"
            assert repo.active_tenant_id_for_virtual_host("stored.example.com") == "tlr_p2"

    def test_a_different_host_does_not_match(self, integration_db):
        with _RepoEnv() as env:
            TenantFactory(tenant_id="tlr_p3", virtual_host="stored.example.com:8443")
            repo = TenantLookupRepository(env.get_session())

            assert repo.find_active_by_virtual_host("other.example.com") is None

    def test_two_tenants_cannot_claim_one_host_name(self, integration_db):
        """THE REGRESSION: the pair that made the match ambiguous is unrepresentable.

        This is what licenses the port-insensitive comparison above. Without the functional
        index both rows exist and both match ``Host: pair.example.com``, and which tenant
        answers is the query plan's choice. Reverting the index to the raw column lets this
        INSERT succeed, which is the failure.
        """
        from sqlalchemy.exc import IntegrityError

        with _RepoEnv() as env:
            TenantFactory(tenant_id="tlr_bare", virtual_host="pair.example.com")
            with pytest.raises(IntegrityError):
                TenantFactory(tenant_id="tlr_ported", virtual_host="pair.example.com:8443")
                env.get_session().flush()

    def test_two_ipv6_tenants_do_not_collide(self, integration_db):
        """Two bracketed IPv6 tenants are two tenants, and each resolves to its own row.

        The case that decides where the host name may be derived. Splitting a host on its
        first colon yields ``[`` for both of these, so deriving the key that way makes the
        two rows one: the second INSERT is refused as a duplicate and whichever row exists
        answers for the other's address. ``urlsplit().hostname`` gives ``::1`` and ``::2``.
        """
        with _RepoEnv() as env:
            TenantFactory(tenant_id="tlr_v6a", virtual_host="[::1]:8000")
            TenantFactory(tenant_id="tlr_v6b", virtual_host="[::2]:9000")
            repo = TenantLookupRepository(env.get_session())

            assert repo.find_active_by_virtual_host("[::1]:8000").tenant_id == "tlr_v6a"
            assert repo.find_active_by_virtual_host("[::2]:9000").tenant_id == "tlr_v6b"
            # Port aside, as for any other host.
            assert repo.find_active_by_virtual_host("[::1]").tenant_id == "tlr_v6a"

    def test_case_is_folded_on_both_sides(self, integration_db):
        """The stored form is folded, so a request naming it in any case reaches it."""
        with _RepoEnv() as env:
            TenantFactory(tenant_id="tlr_case", virtual_host="folded.example.com")
            repo = TenantLookupRepository(env.get_session())

            assert repo.find_active_by_virtual_host("Folded.Example.COM").tenant_id == "tlr_case"
            assert repo.find_by_virtual_host("FOLDED.example.com").tenant_id == "tlr_case"


class TestDefaultActiveTenant:
    """The CLI/single-tenant default: the ``default`` row, else the oldest active one."""

    def test_prefers_the_tenant_named_default(self, integration_db):
        with _RepoEnv() as env:
            TenantFactory(tenant_id="tlr_older", created_at=datetime(2020, 1, 1, tzinfo=UTC))
            TenantFactory(tenant_id="default")
            repo = TenantLookupRepository(env.get_session())

            assert repo.find_default_active().tenant_id == "default"

    def test_falls_back_to_the_oldest_active_tenant(self, integration_db):
        with _RepoEnv() as env:
            TenantFactory(tenant_id="tlr_newer", created_at=datetime(2024, 1, 1, tzinfo=UTC))
            TenantFactory(tenant_id="tlr_oldest", created_at=datetime(2020, 1, 1, tzinfo=UTC))
            repo = TenantLookupRepository(env.get_session())

            assert repo.find_default_active().tenant_id == "tlr_oldest"

    def test_an_inactive_default_is_not_the_default(self, integration_db):
        with _RepoEnv() as env:
            TenantFactory(tenant_id="default", is_active=False)
            TenantFactory(tenant_id="tlr_live", created_at=datetime(2021, 1, 1, tzinfo=UTC))
            repo = TenantLookupRepository(env.get_session())

            assert repo.find_default_active().tenant_id == "tlr_live"

    def test_no_active_tenant_at_all_is_none(self, integration_db):
        with _RepoEnv() as env:
            TenantFactory(tenant_id="tlr_dead", is_active=False)
            repo = TenantLookupRepository(env.get_session())

            assert repo.find_default_active() is None


class TestUniquenessChecksStillSeeInactiveTenants:
    """The other half of this module: an inactive tenant still HOLDS its unique keys."""

    def test_subdomain_of_an_inactive_tenant_is_taken(self, integration_db):
        with _RepoEnv() as env:
            TenantFactory(tenant_id="tlr_u1", subdomain="taken-sub", is_active=False)
            repo = TenantLookupRepository(env.get_session())

            assert repo.find_by_subdomain("taken-sub").tenant_id == "tlr_u1"

    def test_virtual_host_of_an_inactive_tenant_is_taken(self, integration_db):
        with _RepoEnv() as env:
            TenantFactory(tenant_id="tlr_u2", virtual_host="taken.example.com", is_active=False)
            repo = TenantLookupRepository(env.get_session())

            assert repo.find_by_virtual_host("taken.example.com").tenant_id == "tlr_u2"

    def test_id_or_subdomain_of_an_inactive_tenant_is_taken(self, integration_db):
        with _RepoEnv() as env:
            TenantFactory(tenant_id="tlr_u3", subdomain="taken-either", is_active=False)
            repo = TenantLookupRepository(env.get_session())

            assert repo.find_by_id_or_subdomain("nobody", "taken-either").tenant_id == "tlr_u3"

    def test_a_virtual_host_differing_only_in_case_is_the_same_host_and_is_taken(self, integration_db):
        """The uniqueness check folds case, like the routing lookups above.

        A host is a DNS name, so ``Taken.Example.com`` and ``taken.example.com`` are one
        host. Answering "free" for the second would admit a row the routing lookups then
        resolve to whichever of the two they happen to see first.
        """
        with _RepoEnv() as env:
            TenantFactory(tenant_id="tlr_u4", virtual_host="case.example.com")
            repo = TenantLookupRepository(env.get_session())

            assert repo.find_by_virtual_host("Case.Example.COM").tenant_id == "tlr_u4"
