"""Integration tests for TenantConfigRepository.

Verifies that the repository correctly queries PublisherPartner and AdapterConfig
models with tenant scoping against real PostgreSQL.

"""

import pytest
from cryptography.fernet import Fernet

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


class TestUpdateTenantProvesEveryKeyNamesAWritableAttribute:
    """``update_tenant`` refuses a key that is not a writable Tenant attribute (GH #1802).

    ``**columns: Any`` plus ``setattr`` on a SQLAlchemy declarative instance
    accepts a misspelled column as a plain Python attribute: nothing is mapped,
    nothing is written, and the only caller
    (``src/admin/blueprints/settings.py:513``) flashes "updated successfully"
    over a write that changed nothing. The boundary value has to prove itself
    before use — every key must name something the ORM will actually persist.

    The two halves are graded together on purpose. A key set derived naively
    from ``inspect(Tenant).mapper.column_attrs`` alone is not merely incomplete:
    ``_gemini_api_key`` is the mapped attribute for the ``gemini_api_key``
    column, and ``Tenant.gemini_api_key`` is a property whose setter ENCRYPTS
    (``models.py:173-194``). Such a set would reject the only spelling that
    encrypts and admit the one that writes the secret in the clear — inverting
    this lane's own invariant on exactly the field where it matters most.
    """

    def test_a_misspelled_column_raises_naming_the_key(self, integration_db):
        """A near-miss spelling is refused, and the message names the offending key."""
        with _RepoEnv() as env:
            TenantFactory(tenant_id="tcr_upd_bad")
            repo = TenantConfigRepository(env.get_session(), "tcr_upd_bad")

            with pytest.raises(ValueError, match="slack_webhook_urll"):
                repo.update_tenant(slack_webhook_urll="https://hooks.example.test/misspelled")

    def test_the_correctly_spelled_sibling_still_persists(self, integration_db):
        """The valid-spelling write is untouched: still ``True``, still persisted."""
        with _RepoEnv() as env:
            TenantFactory(tenant_id="tcr_upd_ok")
            session = env.get_session()
            repo = TenantConfigRepository(session, "tcr_upd_ok")

            assert repo.update_tenant(slack_webhook_url="https://hooks.example.test/ok") is True

            session.flush()
            session.expire_all()
            assert repo.get_tenant().slack_webhook_url == "https://hooks.example.test/ok"

    def test_the_encrypting_property_name_is_accepted_and_encrypts(self, integration_db, monkeypatch):
        """``gemini_api_key=`` is accepted and goes through the ENCRYPTING setter.

        Asserts the stored column value is not the plaintext — a column-only key
        set would have rejected this spelling outright, and the grader for that
        regression has to be the persisted bytes, not the return value.
        """
        monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())

        with _RepoEnv() as env:
            TenantFactory(tenant_id="tcr_upd_gemini")
            session = env.get_session()
            repo = TenantConfigRepository(session, "tcr_upd_gemini")

            assert repo.update_tenant(gemini_api_key="plaintext-gemini-key") is True

            session.flush()
            session.expire_all()
            tenant = repo.get_tenant()
            assert tenant._gemini_api_key != "plaintext-gemini-key", (
                "the write reached the mapped column directly — the encrypting property setter was bypassed"
            )
            assert tenant.gemini_api_key == "plaintext-gemini-key"

    def test_the_private_mapped_attribute_is_rejected(self, integration_db):
        """``_gemini_api_key=`` is refused — it is the encryption bypass, not a spelling.

        This is the security-relevant half: the underscore-prefixed mapped
        attribute persists successfully and stores the secret verbatim, which is
        precisely the laundering class this lane exists to kill.
        """
        with _RepoEnv() as env:
            TenantFactory(tenant_id="tcr_upd_private")
            repo = TenantConfigRepository(env.get_session(), "tcr_upd_private")

            with pytest.raises(ValueError, match="_gemini_api_key"):
                repo.update_tenant(_gemini_api_key="stored-in-the-clear")

    def test_a_read_only_property_is_rejected(self, integration_db):
        """``primary_domain`` is a property with no setter — a write naming it cannot land."""
        with _RepoEnv() as env:
            TenantFactory(tenant_id="tcr_upd_readonly")
            repo = TenantConfigRepository(env.get_session(), "tcr_upd_readonly")

            with pytest.raises(ValueError, match="primary_domain"):
                repo.update_tenant(primary_domain="nope.example.test")


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
