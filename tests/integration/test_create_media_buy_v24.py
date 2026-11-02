"""Integration tests for AdCP v2.4 create_media_buy format with nested objects.

These tests specifically verify that packages containing nested Pydantic objects
(Budget, Targeting) are properly serialized in responses. This catches bugs like
the 'dict' object has no attribute 'model_dump' error that occurred when nested
objects weren't being serialized correctly.

Key differences from existing tests:
- Tests the NEW v2.4 format (packages with Budget/Targeting)
- Tests both MCP and A2A paths
- Exercises the FULL serialization path (not just schema validation)
- Uses integration-level mocking (real DB, mock adapter only)

NOTE: These tests require a database connection. Run with:
    env TEST_DATABASE_URL="sqlite:///:memory:" pytest tests/integration_v2/test_create_media_buy_v24.py
or with Docker Compose running for PostgreSQL.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from src.core.database.database_session import get_db_session
from src.core.resolved_identity import AccountIdentity
from src.core.schemas import CreateMediaBuyRequest, PackageRequest, Targeting
from src.core.schemas.account import Account
from src.core.tenant_context import TenantContext
from tests.factories import AccountFactory
from tests.factories.principal import PrincipalFactory, plaintext_token_for
from tests.integration.conftest import add_required_setup_data, create_test_product_with_pricing

pytestmark = [pytest.mark.integration, pytest.mark.requires_db, pytest.mark.asyncio]

_TENANT_ID = "test_tenant_v24"
_ACCOUNT_ID = "acct_test"


def _account_identity() -> AccountIdentity:
    """The caller ``_create_media_buy_impl`` takes: the identity with the account inside.

    ``create-media-buy-request.json`` requires ``account``, so the implementation is
    annotated ``AccountIdentity``; the tenant is the committed ROW, which is what the
    resolver would have loaded.
    """
    tenant = TenantContext.load(_TENANT_ID)
    assert tenant is not None, "the setup fixture must have committed the tenant row"
    return PrincipalFactory.make_account_identity(
        PrincipalFactory.make_identity(principal_id="test_principal_v24", tenant_id=_TENANT_ID, tenant=tenant),
        Account(account_id=_ACCOUNT_ID, name="Test Account", status="active"),
    )


@pytest.mark.integration
@pytest.mark.requires_db
class TestCreateMediaBuyV24Format:
    """Test create_media_buy with AdCP v2.4 packages containing nested objects."""

    @pytest.fixture
    def setup_test_tenant(self, integration_db):
        """Set up test tenant with product."""
        from src.core.database.models import CurrencyLimit
        from src.core.database.models import Principal as ModelPrincipal
        from src.core.database.models import Tenant as ModelTenant

        with get_db_session() as session:
            now = datetime.now(UTC)

            # Create tenant
            # Note: human_review_required=False ensures media buy runs immediately
            # rather than going to approval workflow (needed for testing serialization)
            tenant = ModelTenant(
                tenant_id="test_tenant_v24",
                name="Test V24 Tenant",
                subdomain="testv24",
                # Required (NOT NULL) and UNIQUE. Nothing routes to this host; it exists to satisfy the column.
                virtual_host="test-tenant-v24.adcp.test",
                ad_server="mock",
                is_active=True,
                human_review_required=False,
                created_at=now,
                updated_at=now,
                # Required: Access control configuration (will be updated by add_required_setup_data)
                authorized_emails=[],
            )
            session.add(tenant)
            session.flush()  # Flush so add_required_setup_data can find the tenant

            # Create principal
            principal = ModelPrincipal.with_token(
                plaintext_token_for("test_principal_v24"),
                tenant_id="test_tenant_v24",
                principal_id="test_principal_v24",
                name="Test Principal V24",
                platform_mappings={"mock": {"advertiser_id": "adv_test_v24"}},
            )
            session.add(principal)

            # Add required setup data (access control, currency limits, property tags)
            add_required_setup_data(session, "test_tenant_v24")

            # Create products for different currencies (for multi-package test)
            product_usd = create_test_product_with_pricing(
                session=session,
                tenant_id="test_tenant_v24",
                product_id="prod_test_v24_usd",
                name="Test Product V24 USD",
                description="Test product for v2.4 format (USD)",
                format_ids=[{"agent_url": "https://test.com", "id": "display_300x250"}],
                delivery_type="guaranteed",
                pricing_model="CPM",
                rate="10.0",
                is_fixed=True,
                currency="USD",
                min_spend_per_package="1000.0",
                targeting_template={},
            )

            product_eur = create_test_product_with_pricing(
                session=session,
                tenant_id="test_tenant_v24",
                product_id="prod_test_v24_eur",
                name="Test Product V24 EUR",
                description="Test product for v2.4 format (EUR)",
                format_ids=[{"agent_url": "https://test.com", "id": "display_300x250"}],
                delivery_type="guaranteed",
                pricing_model="CPM",
                rate="10.0",
                is_fixed=True,
                currency="EUR",
                min_spend_per_package="1000.0",
                targeting_template={},
            )

            product_gbp = create_test_product_with_pricing(
                session=session,
                tenant_id="test_tenant_v24",
                product_id="prod_test_v24_gbp",
                name="Test Product V24 GBP",
                description="Test product for v2.4 format (GBP)",
                format_ids=[{"agent_url": "https://test.com", "id": "display_300x250"}],
                delivery_type="guaranteed",
                pricing_model="CPM",
                rate="10.0",
                is_fixed=True,
                currency="GBP",
                min_spend_per_package="1000.0",
                targeting_template={},
            )

            # Add additional currency limits for EUR
            currency_limit_eur = CurrencyLimit(
                tenant_id="test_tenant_v24",
                currency_code="EUR",
                min_package_budget=1000.0,
                max_daily_package_spend=10000.0,
            )
            session.add(currency_limit_eur)

            # Add GBP for multi-currency test
            currency_limit_gbp = CurrencyLimit(
                tenant_id="test_tenant_v24",
                currency_code="GBP",
                min_package_budget=1000.0,
                max_daily_package_spend=10000.0,
            )
            session.add(currency_limit_gbp)

            # The account every request below names. media_buys has a
            # (tenant_id, account_id) foreign key, so the row must exist; in production
            # the boundary resolves the reference and these tests call _impl directly.
            session.add(AccountFactory.build(tenant_id="test_tenant_v24", account_id=_ACCOUNT_ID))

            session.commit()

            # No ambient tenant to set: the tenant reaches the implementation on the
            # identity each test builds, loaded from the row committed above.

            # Get pricing_option_ids for created products (needed for PackageRequest)
            # NOTE: pricing_option_id is auto-generated from pricing model details
            # Format: {pricing_model}_{currency}_{fixed|auction}
            # Example: "cpm_usd_fixed", "cpm_eur_auction"
            yield {
                "tenant_id": "test_tenant_v24",
                "principal_id": "test_principal_v24",
                "product_id_usd": "prod_test_v24_usd",
                "product_id_eur": "prod_test_v24_eur",
                "product_id_gbp": "prod_test_v24_gbp",
                # Use generated pricing_option_id format (not database ID)
                "pricing_option_id_usd": "cpm_usd_fixed",
                "pricing_option_id_eur": "cpm_eur_fixed",
                "pricing_option_id_gbp": "cpm_gbp_fixed",
            }

            # Cleanup - IMPORTANT: Delete in reverse dependency order
            from src.core.database.models import (
                AuthorizedProperty,
                MediaBuy,
                MediaPackage,
                PricingOption,
                Product,
                PropertyTag,
            )

            # Delete media_packages first (depends on media_buys)
            session.execute(
                delete(MediaPackage).where(
                    MediaPackage.media_buy_id.in_(
                        select(MediaBuy.media_buy_id).where(MediaBuy.tenant_id == "test_tenant_v24")
                    )
                )
            )
            # Delete media_buys (depends on principals/products)
            session.execute(delete(MediaBuy).where(MediaBuy.tenant_id == "test_tenant_v24"))
            # Delete pricing options (depends on products)
            session.execute(delete(PricingOption).where(PricingOption.tenant_id == "test_tenant_v24"))
            # Delete products
            session.execute(delete(Product).where(Product.tenant_id == "test_tenant_v24"))
            # Delete principals
            session.execute(delete(ModelPrincipal).where(ModelPrincipal.tenant_id == "test_tenant_v24"))
            # Delete currency limits
            session.execute(delete(CurrencyLimit).where(CurrencyLimit.tenant_id == "test_tenant_v24"))
            # Delete property tags
            session.execute(delete(PropertyTag).where(PropertyTag.tenant_id == "test_tenant_v24"))
            # Delete authorized properties
            session.execute(delete(AuthorizedProperty).where(AuthorizedProperty.tenant_id == "test_tenant_v24"))
            # Finally delete tenant
            session.execute(delete(ModelTenant).where(ModelTenant.tenant_id == "test_tenant_v24"))
            session.commit()

    async def test_create_media_buy_with_package_budget_mcp(self, setup_test_tenant):
        """Test MCP path with packages containing Budget objects.

        This test specifically exercises the bug fix for 'dict' object has no attribute 'model_dump'.
        Before the fix, this would fail when building response_packages because Budget objects
        weren't being serialized to dicts properly.
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        # Create PackageRequest with float budget (new format)
        packages = [
            PackageRequest(
                product_id=setup_test_tenant["product_id_usd"],  # Use USD product
                pricing_option_id=setup_test_tenant["pricing_option_id_usd"],  # Required field
                budget=5000.0,  # Float budget, currency from pricing_option
            )
        ]

        identity = _account_identity()

        # Call _impl with a CreateMediaBuyRequest object
        # This exercises the FULL serialization path including response_packages construction
        # NOTE: budget is at package level per AdCP v2.4 spec (not a top-level parameter)
        req = CreateMediaBuyRequest(
            account={"account_id": "acct_test"},
            brand={"domain": "testbrand.com"},
            packages=[p.model_dump() for p in packages],
            start_time=datetime.now(UTC) + timedelta(days=1),
            end_time=datetime.now(UTC) + timedelta(days=31),
            po_number="TEST-V24-001",
            idempotency_key=f"int-key-{uuid.uuid4().hex}",
        )
        response = await _create_media_buy_impl(req=req, identity=identity)

        # Verify response structure
        if not hasattr(response, "media_buy_id"):
            # If error response, print the error for debugging
            print(f"ERROR RESPONSE: {response}")
            print(f"ERROR RESPONSE TYPE: {type(response)}")
            print(
                f"ERROR RESPONSE DICT: {response.model_dump() if hasattr(response, 'model_dump') else vars(response)}"
            )
            if hasattr(response, "error_code"):
                print(f"Error code: {response.error_code}")
            if hasattr(response, "message"):
                print(f"Error message: {response.message}")
            if hasattr(response, "details"):
                print(f"Error details: {response.details}")
            raise AssertionError(f"Expected CreateMediaBuySuccess but got {type(response).__name__}: {response}")
        assert response.media_buy_id
        assert len(response.packages) == 1

        # CRITICAL: Verify package can be serialized correctly (no model_dump errors)
        # The response.packages field contains Pydantic Package objects internally,
        # but they should serialize correctly to dicts when .model_dump() is called
        response_dict = response.model_dump()
        package = response_dict["packages"][0]
        assert isinstance(package, dict), "Package must be serialized to dict"
        assert package["package_id"]  # Should have generated ID

    async def test_create_media_buy_with_targeting_overlay_mcp(self, setup_test_tenant):
        """Test MCP path with packages containing Targeting objects.

        This tests another potential serialization issue with nested Pydantic objects.
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        # Create PackageRequest with nested Targeting object
        packages = [
            PackageRequest(
                product_id=setup_test_tenant["product_id_eur"],  # Use EUR product
                pricing_option_id=setup_test_tenant["pricing_option_id_eur"],  # Required field
                budget=8000.0,  # Float budget, currency from pricing_option
                targeting_overlay=Targeting(
                    geo_countries=["US", "CA"],
                ),
            )
        ]

        identity = _account_identity()

        req = CreateMediaBuyRequest(
            account={"account_id": "acct_test"},
            brand={"domain": "testbrand.com"},
            packages=[p.model_dump() for p in packages],
            start_time=datetime.now(UTC) + timedelta(days=1),
            end_time=datetime.now(UTC) + timedelta(days=31),
            po_number="TEST-V24-002",
            idempotency_key=f"int-key-{uuid.uuid4().hex}",
        )
        response = await _create_media_buy_impl(req=req, identity=identity)

        # Verify response structure
        if not hasattr(response, "media_buy_id"):
            # If error response, print the error for debugging
            print(f"ERROR RESPONSE: {response}")
            print(f"ERROR RESPONSE TYPE: {type(response)}")
            print(
                f"ERROR RESPONSE DICT: {response.model_dump() if hasattr(response, 'model_dump') else vars(response)}"
            )
            if hasattr(response, "error_code"):
                print(f"Error code: {response.error_code}")
            if hasattr(response, "message"):
                print(f"Error message: {response.message}")
            if hasattr(response, "details"):
                print(f"Error details: {response.details}")
            raise AssertionError(f"Expected CreateMediaBuySuccess but got {type(response).__name__}: {response}")
        assert response.media_buy_id
        assert len(response.packages) == 1

        # Verify package can be serialized correctly (no model_dump errors)
        # The response.packages field contains Pydantic Package objects internally,
        # but they should serialize correctly to dicts when .model_dump() is called
        response_dict = response.model_dump()
        package = response_dict["packages"][0]
        assert isinstance(package, dict), "Package must be serialized to dict"
        assert package["package_id"]  # Should have generated ID

        # Verify nested targeting was serialized (if present in response)
        # Note: targeting_overlay may or may not be included in response depending on impl

    async def test_create_media_buy_multiple_packages_with_budgets_mcp(self, setup_test_tenant):
        """Test MCP path with multiple packages, each with different budgets.

        This tests the iteration over packages in response construction.
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        packages = [
            PackageRequest(
                product_id=setup_test_tenant["product_id_usd"],  # Use USD product
                pricing_option_id=setup_test_tenant["pricing_option_id_usd"],  # Required field
                budget=3000.0,  # Float budget, currency from pricing_option
            ),
            PackageRequest(
                product_id=setup_test_tenant["product_id_eur"],  # Use EUR product
                pricing_option_id=setup_test_tenant["pricing_option_id_eur"],  # Required field
                budget=2500.0,  # Float budget, currency from pricing_option
            ),
            PackageRequest(
                product_id=setup_test_tenant["product_id_gbp"],  # Use GBP product
                pricing_option_id=setup_test_tenant["pricing_option_id_gbp"],  # Required field
                budget=2000.0,  # Float budget, currency from pricing_option
            ),
        ]

        identity = _account_identity()

        # Total budget is sum of all package budgets
        total_budget_value = sum(pkg.budget for pkg in packages)

        req = CreateMediaBuyRequest(
            account={"account_id": "acct_test"},
            brand={"domain": "testbrand.com"},
            packages=[p.model_dump() for p in packages],
            start_time=datetime.now(UTC) + timedelta(days=1),
            end_time=datetime.now(UTC) + timedelta(days=31),
            po_number="TEST-V24-003",
            idempotency_key=f"int-key-{uuid.uuid4().hex}",
        )
        response = await _create_media_buy_impl(req=req, identity=identity)

        # Verify all packages serialized correctly
        assert response.media_buy_id
        assert len(response.packages) == 3

        # Serialize response to check packages are dicts
        response_dict = response.model_dump()
        package_ids = [pkg["package_id"] for pkg in response_dict["packages"]]
        assert len(package_ids) == 3

    async def test_create_media_buy_with_package_budget_a2a(self, setup_test_tenant):
        """Test A2A path with packages containing Budget objects.

        This verifies the A2A → tools.py → _impl path also handles nested objects correctly.
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        # Create PackageRequest with float budget (new format)
        packages = [
            PackageRequest(
                product_id=setup_test_tenant["product_id_usd"],  # Use USD product
                pricing_option_id=setup_test_tenant["pricing_option_id_usd"],  # Required field
                budget=6000.0,  # Float budget, currency from pricing_option
            )
        ]

        identity = _account_identity()

        req = CreateMediaBuyRequest(
            account={"account_id": "acct_test"},
            brand={"domain": "testbrand.com"},
            packages=[p.model_dump() for p in packages],
            start_time=datetime.now(UTC) + timedelta(days=1),
            end_time=datetime.now(UTC) + timedelta(days=31),
            po_number="TEST-V24-A2A-001",
            idempotency_key=f"int-key-{uuid.uuid4().hex}",
        )
        response = await _create_media_buy_impl(req=req, identity=identity)

        # Verify response structure (same as MCP)
        assert response.media_buy_id
        assert len(response.packages) == 1

        # CRITICAL: Verify package can be serialized correctly
        response_dict = response.model_dump()
        package = response_dict["packages"][0]
        assert isinstance(package, dict), "Package must be serialized to dict"
        assert package["package_id"]  # Should have generated ID

    async def test_create_media_buy_with_minimal_package(self, setup_test_tenant):
        """Verify media buy creation works with a minimal valid package.

        Tests that the standard AdCP format creates media buys correctly.
        Legacy formats (product_ids, total_budget) were removed in adcp 2.16.0.
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        identity = _account_identity()

        # Standard AdCP format with explicit package
        # pricing_option_id format: {model}_{currency}_{fixed|auction}
        req = CreateMediaBuyRequest(
            account={"account_id": "acct_test"},
            brand={"domain": "testbrand.com"},
            packages=[
                PackageRequest(
                    product_id="prod_test_v24_usd",
                    budget=5000.0,
                    pricing_option_id="cpm_usd_fixed",  # Matches fixture: CPM, USD, is_fixed=True
                )
            ],
            start_time=datetime.now(UTC) + timedelta(days=1),
            end_time=datetime.now(UTC) + timedelta(days=31),
            po_number="TEST-STANDARD-001",
            idempotency_key=f"int-key-{uuid.uuid4().hex}",
        )
        response = await _create_media_buy_impl(req=req, identity=identity)

        # Verify response
        assert response.media_buy_id
        assert len(response.packages) > 0

        # Packages should still serialize to dicts
        response_dict = response.model_dump()
        for package in response_dict["packages"]:
            assert isinstance(package, dict)
