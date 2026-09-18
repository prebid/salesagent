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
    """A port on either side is not part of the question."""

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
