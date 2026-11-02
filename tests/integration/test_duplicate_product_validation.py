#!/usr/bin/env python3
"""
Unit test for duplicate product validation in media buy packages.

Tests the validation logic that rejects media buy requests where the same
product_id appears in multiple packages.

📊 BUDGET FORMAT: AdCP v2.2.0 Migration (2025-10-27)
All tests in this file use float budget format per AdCP v2.2.0 spec:
- Package.budget: float (e.g., 1000.0) - NOT Budget object
- Currency is determined by PricingOption, not Package
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.core.exceptions import AdCPSalesAgentError, AdCPValidationError
from src.core.resolved_identity import AccountIdentity
from src.core.schemas import CreateMediaBuyRequest
from src.core.schemas.account import Account
from src.core.tenant_context import TenantContext
from tests.factories.principal import PrincipalFactory
from tests.helpers.adcp_factories import create_test_package_request

_TENANT_ID = "test_tenant_dupe"
_PRINCIPAL_ID = "test_principal"
_ACCOUNT_ID = "acct_test"


@pytest.fixture
def seeded_tenant(integration_db):
    """A committed tenant whose setup checklist is complete, plus its account.

    The duplicate-product check runs after ``validate_setup_complete``, which reads the
    tenant ROW -- the tenant is no longer an ambient dict a test could fabricate, so it has
    to exist. Factories, per CLAUDE.md.
    """
    from tests.factories import AccountFactory, PricingOptionFactory, ProductFactory, TenantFactory
    from tests.harness._base import BareIntegrationEnv
    from tests.integration.conftest import add_required_setup_data

    with BareIntegrationEnv() as env:
        tenant = TenantFactory(tenant_id=_TENANT_ID, ad_server="mock", human_review_required=False)
        PrincipalFactory(tenant=tenant, principal_id=_PRINCIPAL_ID)
        AccountFactory(tenant_id=_TENANT_ID, account_id=_ACCOUNT_ID)
        # A product with a pricing option: "Products" is one of the critical checklist
        # tasks, so without one validate_setup_complete refuses before the duplicate
        # check this file is about.
        PricingOptionFactory(product=ProductFactory(tenant=tenant, property_tags=["all_inventory"]))
        session = env.get_session()
        add_required_setup_data(session, _TENANT_ID)
        session.commit()
    yield


def _identity() -> AccountIdentity:
    """The caller ``_create_media_buy_impl`` takes, with the seeded tenant row inside."""
    tenant = TenantContext.load(_TENANT_ID)
    assert tenant is not None, "the seeded_tenant fixture must have committed the tenant"
    return PrincipalFactory.make_account_identity(
        PrincipalFactory.make_identity(principal_id=_PRINCIPAL_ID, tenant_id=_TENANT_ID, tenant=tenant),
        Account(account_id=_ACCOUNT_ID, name="Test Account", status="active"),
    )


@pytest.mark.requires_db
class TestDuplicateProductValidation:
    """Test that duplicate products in packages are rejected."""

    @pytest.mark.asyncio
    async def test_duplicate_product_in_packages_rejected(self, seeded_tenant):
        """Test that duplicate product_ids in packages are rejected."""
        from src.core.tools.media_buy_create import _create_media_buy_impl

        # Mock context manager
        mock_ctx_manager = MagicMock()
        mock_persistent_ctx = MagicMock()
        mock_ctx_manager.get_context.return_value = mock_persistent_ctx

        identity = _identity()

        # Mock the dependencies that still exist on the module
        with (
            patch("src.core.tools.media_buy_create.get_context_manager", return_value=mock_ctx_manager),
        ):
            # Create packages with duplicate product_id
            packages = [
                create_test_package_request(
                    product_id="prod_test_1",
                    budget=1000.0,  # Float budget per AdCP v2.2.0, currency from pricing_option
                    pricing_option_id="test_pricing",
                ),
                create_test_package_request(
                    product_id="prod_test_1",  # Same product as pkg_1
                    budget=1500.0,  # Float budget per AdCP v2.2.0, currency from pricing_option
                    pricing_option_id="test_pricing",
                ),
            ]

            start_time = datetime.now(UTC) + timedelta(hours=1)  # 1 hour in the future
            end_time = start_time + timedelta(days=7)

            # Should raise typed AdCPValidationError about duplicate products
            # (propagates past _impl boundary catch since 572b55b4f).
            req = CreateMediaBuyRequest(
                account={"account_id": "acct_test"},
                brand={"domain": "testbrand.com"},
                packages=packages,
                start_time=start_time,
                end_time=end_time,
                idempotency_key=f"int-key-{uuid.uuid4().hex}",
            )
            with pytest.raises(AdCPValidationError) as excinfo:
                await _create_media_buy_impl(req=req, identity=identity)

            exc = excinfo.value
            assert exc.error_code == "VALIDATION_ERROR"
            # WHICH products collided travels structurally, not in the sentence.
            assert exc.details is not None and exc.details.duplicate_product_ids == ["prod_test_1"]

    @pytest.mark.asyncio
    async def test_multiple_duplicate_products_all_listed(self, seeded_tenant):
        """All duplicate product_ids are listed in error.details."""
        from src.core.tools.media_buy_create import _create_media_buy_impl

        # Mock context manager
        mock_ctx_manager = MagicMock()
        mock_persistent_ctx = MagicMock()
        mock_ctx_manager.get_context.return_value = mock_persistent_ctx

        identity = _identity()

        # Mock the dependencies that still exist on the module
        with (
            patch("src.core.tools.media_buy_create.get_context_manager", return_value=mock_ctx_manager),
        ):
            # Create packages with multiple duplicates
            packages = [
                create_test_package_request(
                    product_id="prod_test_1",
                    budget=1000.0,  # Float budget per AdCP v2.2.0, currency from pricing_option
                    pricing_option_id="test_pricing",
                ),
                create_test_package_request(
                    product_id="prod_test_1",  # Duplicate of pkg_1
                    budget=1500.0,  # Float budget per AdCP v2.2.0, currency from pricing_option
                    pricing_option_id="test_pricing",
                ),
                create_test_package_request(
                    product_id="prod_test_2",
                    budget=2000.0,  # Float budget per AdCP v2.2.0, currency from pricing_option
                    pricing_option_id="test_pricing",
                ),
                create_test_package_request(
                    product_id="prod_test_2",  # Duplicate of pkg_3
                    budget=1800.0,  # Float budget per AdCP v2.2.0, currency from pricing_option
                    pricing_option_id="test_pricing",
                ),
            ]

            start_time = datetime.now(UTC) + timedelta(hours=1)  # 1 hour in the future
            end_time = start_time + timedelta(days=7)

            # Should raise typed AdCPValidationError listing both duplicate products
            # (propagates past _impl boundary catch since 572b55b4f).
            req = CreateMediaBuyRequest(
                account={"account_id": "acct_test"},
                brand={"domain": "testbrand.com"},
                packages=packages,
                start_time=start_time,
                end_time=end_time,
                idempotency_key=f"int-key-{uuid.uuid4().hex}",
            )
            with pytest.raises(AdCPValidationError) as excinfo:
                await _create_media_buy_impl(req=req, identity=identity)

            exc = excinfo.value
            assert exc.error_code == "VALIDATION_ERROR"
            # Sorted by production, so the oracle is deterministic.
            assert exc.details is not None and exc.details.duplicate_product_ids == ["prod_test_1", "prod_test_2"]

    @pytest.mark.asyncio
    async def test_no_duplicates_validation_passes(self, seeded_tenant):
        """Unique product_ids do not trip the duplicate check.

        The request names two DIFFERENT products that the seeded tenant does not sell, so
        the create fails downstream -- what this pins is that the failure is not the
        duplicate refusal, and carries no duplicate_product_ids. The version this replaced
        patched a deleted ``context_helpers.ensure_tenant_context``, called the
        implementation with no identity at all, and accepted ``Exception`` -- so the
        missing-argument TypeError satisfied it.
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        packages = [
            create_test_package_request(
                product_id="prod_test_1",
                budget=1000.0,  # Float budget per AdCP v2.2.0, currency from pricing_option
                pricing_option_id="test_pricing",
            ),
            create_test_package_request(
                product_id="prod_test_2",  # Different product
                budget=1500.0,  # Float budget per AdCP v2.2.0, currency from pricing_option
                pricing_option_id="test_pricing",
            ),
        ]

        start_time = datetime.now(UTC) + timedelta(hours=1)  # 1 hour in the future
        end_time = start_time + timedelta(days=7)

        req = CreateMediaBuyRequest(
            account={"account_id": _ACCOUNT_ID},
            brand={"domain": "testbrand.com"},
            packages=packages,
            start_time=start_time,
            end_time=end_time,
            idempotency_key=f"int-key-{uuid.uuid4().hex}",
        )
        with pytest.raises(AdCPSalesAgentError) as exc_info:
            await _create_media_buy_impl(req=req, identity=_identity())

        exc = exc_info.value
        assert exc.error_code != "VALIDATION_ERROR" or getattr(exc.details, "duplicate_product_ids", None) is None, (
            f"unique product_ids must not trip the duplicate check, got {exc.details!r}"
        )
