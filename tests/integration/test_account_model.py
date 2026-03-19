"""Integration tests for Account and AgentAccountAccess ORM models.

Verifies that the Account data model correctly persists, enforces constraints,
and maintains tenant isolation against real PostgreSQL.

beads: salesagent-t53
"""

import pytest
from sqlalchemy import select

from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _AccountEnv(IntegrationEnv):
    """Bare integration env for Account model tests."""

    EXTERNAL_PATCHES: dict[str, str] = {}

    def get_session(self):
        """Expose session for direct model operations."""
        self._commit_factory_data()
        return self._session


class TestAccountModel:
    """Account ORM model CRUD and constraint tests."""

    def test_create_account_with_required_fields(self, integration_db):
        """Account can be created with only required fields (account_id, name, status)."""
        from src.core.database.models import Account
        from tests.factories import AccountFactory, TenantFactory

        with _AccountEnv() as env:
            tenant = TenantFactory(tenant_id="acc_test_t1")
            AccountFactory(
                tenant=tenant,
                account_id="acc_001",
                name="Acme Corp",
                status="active",
            )
            session = env.get_session()

            result = session.scalars(select(Account).filter_by(tenant_id="acc_test_t1", account_id="acc_001")).first()

        assert result is not None
        assert result.account_id == "acc_001"
        assert result.name == "Acme Corp"
        assert result.status == "active"
        assert result.created_at is not None
        assert result.updated_at is not None

    def test_account_with_all_optional_fields(self, integration_db):
        """Account persists all optional fields from AdCP spec."""
        from src.core.database.models import Account
        from tests.factories import AccountFactory, TenantFactory

        with _AccountEnv() as env:
            tenant = TenantFactory(tenant_id="acc_test_t2")
            AccountFactory(
                tenant=tenant,
                account_id="acc_002",
                name="Acme via Pinnacle",
                status="pending_approval",
                advertiser="Acme Corp",
                billing_proxy="Pinnacle Media",
                operator="pinnacle-media.com",
                billing="operator",
                rate_card="acme_vip_2024",
                payment_terms="net_30",
                account_scope="operator_brand",
                brand={"domain": "acme-corp.com"},
                credit_limit={"amount": 50000, "currency": "USD"},
                setup={"message": "Complete credit application"},
                governance_agents=[
                    {
                        "url": "https://gov.example.com",
                        "authentication": {"schemes": ["Bearer"], "credentials": "x" * 32},
                    }
                ],
                sandbox=False,
            )
            session = env.get_session()

            result = session.scalars(select(Account).filter_by(tenant_id="acc_test_t2", account_id="acc_002")).first()

        assert result is not None
        assert result.advertiser == "Acme Corp"
        assert result.billing == "operator"
        # brand and credit_limit are now typed Pydantic models, not dicts
        assert result.brand.domain == "acme-corp.com"
        assert result.credit_limit.amount == 50000
        assert result.credit_limit.currency == "USD"
        assert result.sandbox is False

    def test_account_tenant_cascade_delete(self, integration_db):
        """Deleting a tenant cascades to its accounts."""
        from src.core.database.models import Account, Tenant
        from tests.factories import AccountFactory, TenantFactory

        with _AccountEnv() as env:
            tenant = TenantFactory(tenant_id="acc_test_cascade")
            AccountFactory(
                tenant=tenant,
                account_id="acc_cascade",
                name="Will Be Deleted",
            )
            session = env.get_session()

            # Delete tenant
            t = session.scalars(select(Tenant).filter_by(tenant_id="acc_test_cascade")).first()
            session.delete(t)
            session.commit()

            result = session.scalars(select(Account).filter_by(account_id="acc_cascade")).first()

        assert result is None

    def test_account_status_check_constraint(self, integration_db):
        """Account status must be one of the AdCP-defined values."""
        from sqlalchemy.exc import IntegrityError

        from tests.factories import AccountFactory, TenantFactory

        with _AccountEnv() as env:
            tenant = TenantFactory(tenant_id="acc_test_ck")

            with pytest.raises(IntegrityError):
                AccountFactory(
                    tenant=tenant,
                    account_id="acc_bad_status",
                    name="Bad Status",
                    status="invalid_status",
                )
                env.get_session()  # triggers commit


class TestAgentAccountAccess:
    """AgentAccountAccess junction table tests."""

    def test_grant_agent_access_to_account(self, integration_db):
        """AgentAccountAccess links a principal to an account."""
        from src.core.database.models import AgentAccountAccess
        from tests.factories import AccountFactory, AgentAccountAccessFactory, PrincipalFactory, TenantFactory

        with _AccountEnv() as env:
            tenant = TenantFactory(tenant_id="aaa_test_t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="agent_001")
            account = AccountFactory(tenant=tenant, account_id="acc_access_001", name="Accessible Account")
            AgentAccountAccessFactory(
                tenant_id=tenant.tenant_id,
                principal=principal,
                account=account,
            )
            session = env.get_session()

            result = session.scalars(
                select(AgentAccountAccess).filter_by(
                    tenant_id="aaa_test_t1",
                    principal_id="agent_001",
                    account_id="acc_access_001",
                )
            ).first()

        assert result is not None
        assert result.granted_at is not None


class TestMediaBuyAccountId:
    """MediaBuy.account_id FK tests."""

    def test_media_buy_account_id_nullable(self, integration_db):
        """MediaBuy can be created without account_id (backward compatible)."""
        from src.core.database.models import MediaBuy
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory

        with _AccountEnv() as env:
            tenant = TenantFactory(tenant_id="mb_acc_test")
            principal = PrincipalFactory(tenant=tenant, principal_id="agent_mb")
            MediaBuyFactory(tenant=tenant, principal=principal)
            session = env.get_session()

            result = session.scalars(select(MediaBuy).filter_by(tenant_id="mb_acc_test")).first()

        assert result is not None
        assert result.account_id is None

    def test_media_buy_with_account_id(self, integration_db):
        """MediaBuy can reference an account via account_id FK."""
        from src.core.database.models import MediaBuy
        from tests.factories import AccountFactory, MediaBuyFactory, PrincipalFactory, TenantFactory

        with _AccountEnv() as env:
            tenant = TenantFactory(tenant_id="mb_acc_ref")
            principal = PrincipalFactory(tenant=tenant, principal_id="agent_ref")
            account = AccountFactory(tenant=tenant, account_id="acc_for_mb", name="MB Account")
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                account_id="acc_for_mb",
            )
            session = env.get_session()

            result = session.scalars(select(MediaBuy).filter_by(tenant_id="mb_acc_ref")).first()

        assert result is not None
        assert result.account_id == "acc_for_mb"
