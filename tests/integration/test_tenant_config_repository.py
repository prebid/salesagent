"""Integration tests for TenantConfigRepository.

Verifies that the repository correctly queries PublisherPartner and AdapterConfig
models with tenant scoping against real PostgreSQL.

beads: salesagent-9y0
"""

import pytest

from src.core.database.repositories.tenant_config import TenantConfigRepository
from tests.factories import AdapterConfigFactory, PublisherPartnerFactory, TenantFactory
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _RepoEnv(IntegrationEnv):
    """Bare integration env for repository tests -- no external patches."""

    EXTERNAL_PATCHES: dict[str, str] = {}

    def get_session(self):
        """Expose session for direct repository construction."""
        self._commit_factory_data()
        return self._session


class TestListPublisherPartners:
    """list_publisher_partners returns all partners for the tenant."""

    def test_returns_all_partners(self, integration_db):
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="tcr_test")
            PublisherPartnerFactory(tenant=tenant, publisher_domain="alpha.com", display_name="Alpha")
            PublisherPartnerFactory(
                tenant=tenant,
                publisher_domain="beta.org",
                display_name="Beta",
                is_verified=False,
                sync_status="pending",
            )

            session = env.get_session()
            repo = TenantConfigRepository(session, "tcr_test")
            partners = repo.list_publisher_partners()

        assert len(partners) == 2
        domains = {p.publisher_domain for p in partners}
        assert domains == {"alpha.com", "beta.org"}

    def test_tenant_isolation(self, integration_db):
        with _RepoEnv() as env:
            t1 = TenantFactory(tenant_id="tcr_t1")
            t2 = TenantFactory(tenant_id="tcr_t2")
            PublisherPartnerFactory(tenant=t1, publisher_domain="t1.com")
            PublisherPartnerFactory(tenant=t2, publisher_domain="t2.com")

            session = env.get_session()
            repo = TenantConfigRepository(session, "tcr_t1")
            partners = repo.list_publisher_partners()

        domains = {p.publisher_domain for p in partners}
        assert domains == {"t1.com"}

    def test_empty_tenant(self, integration_db):
        with _RepoEnv() as env:
            session = env.get_session()
            repo = TenantConfigRepository(session, "nonexistent")
            partners = repo.list_publisher_partners()

        assert partners == []


class TestGetAdapterConfig:
    """get_adapter_config returns the adapter config row for the tenant."""

    def test_returns_config(self, integration_db):
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="tcr_ac")
            AdapterConfigFactory(tenant=tenant, adapter_type="broadstreet")

            session = env.get_session()
            repo = TenantConfigRepository(session, "tcr_ac")
            config = repo.get_adapter_config()

        assert config is not None
        assert config.adapter_type == "broadstreet"

    def test_returns_none_when_missing(self, integration_db):
        with _RepoEnv() as env:
            TenantFactory(tenant_id="tcr_no_config")

            session = env.get_session()
            repo = TenantConfigRepository(session, "tcr_no_config")
            config = repo.get_adapter_config()

        assert config is None


class TestListPublisherDomains:
    """list_publisher_domains returns sorted domain strings."""

    def test_sorted_domains(self, integration_db):
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="tcr_dom")
            PublisherPartnerFactory(tenant=tenant, publisher_domain="zebra.com")
            PublisherPartnerFactory(tenant=tenant, publisher_domain="alpha.com")

            session = env.get_session()
            repo = TenantConfigRepository(session, "tcr_dom")
            domains = repo.list_publisher_domains()

        assert domains == ["alpha.com", "zebra.com"]
