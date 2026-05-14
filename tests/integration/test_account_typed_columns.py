"""Integration tests for Account typed JSON columns.

Verifies that Account.brand, credit_limit, setup, and governance_agents
return typed Pydantic models from the adcp library, not raw dicts.

beads: salesagent-6wo
"""

import pytest
from sqlalchemy import select

from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _AccountEnv(IntegrationEnv):
    """Bare integration env for Account typed column tests."""

    EXTERNAL_PATCHES: dict[str, str] = {}

    def get_session(self):
        self._commit_factory_data()
        return self._session


class TestAccountTypedBrand:
    """Account.brand returns BrandReference, not dict."""

    def test_brand_is_pydantic_model(self, integration_db):
        """After DB roundtrip, brand is a BrandReference instance."""
        from adcp.types.generated_poc.core.brand_ref import BrandReference

        from src.core.database.models import Account
        from tests.factories import AccountFactory, TenantFactory

        with _AccountEnv() as env:
            tenant = TenantFactory(tenant_id="typed_brand_test")
            AccountFactory(
                tenant=tenant,
                account_id="acc_typed_001",
                name="Typed Brand Test",
                brand={"domain": "typed-test.com", "brand_id": "spark"},
            )
            session = env.get_session()
            result = session.scalars(
                select(Account).filter_by(tenant_id="typed_brand_test", account_id="acc_typed_001")
            ).first()

        assert result is not None
        assert isinstance(result.brand, BrandReference), (
            f"Expected BrandReference, got {type(result.brand).__name__}: {result.brand}"
        )
        assert result.brand.domain == "typed-test.com"
        # BrandId is RootModel[str] — access via .root
        assert result.brand.brand_id.root == "spark"

    def test_null_brand_is_none(self, integration_db):
        """NULL brand column returns None, not empty model."""
        from src.core.database.models import Account
        from tests.factories import AccountFactory, TenantFactory

        with _AccountEnv() as env:
            tenant = TenantFactory(tenant_id="typed_brand_null")
            AccountFactory(
                tenant=tenant,
                account_id="acc_typed_002",
                name="Null Brand",
                brand=None,
            )
            session = env.get_session()
            result = session.scalars(
                select(Account).filter_by(tenant_id="typed_brand_null", account_id="acc_typed_002")
            ).first()

        assert result is not None
        assert result.brand is None


class TestAccountTypedCreditLimit:
    """Account.credit_limit returns CreditLimit, not dict."""

    def test_credit_limit_is_pydantic_model(self, integration_db):
        """After DB roundtrip, credit_limit is a CreditLimit instance."""
        from adcp.types.generated_poc.core.account import CreditLimit

        from src.core.database.models import Account
        from tests.factories import AccountFactory, TenantFactory

        with _AccountEnv() as env:
            tenant = TenantFactory(tenant_id="typed_credit_test")
            AccountFactory(
                tenant=tenant,
                account_id="acc_typed_003",
                name="Typed Credit Test",
                credit_limit={"amount": 50000.0, "currency": "USD"},
            )
            session = env.get_session()
            result = session.scalars(
                select(Account).filter_by(tenant_id="typed_credit_test", account_id="acc_typed_003")
            ).first()

        assert result is not None
        assert isinstance(result.credit_limit, CreditLimit), (
            f"Expected CreditLimit, got {type(result.credit_limit).__name__}"
        )
        assert result.credit_limit.amount == 50000.0
        assert result.credit_limit.currency == "USD"
