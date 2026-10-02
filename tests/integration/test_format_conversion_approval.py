"""Integration tests for format conversion logic during media buy approval.

Tests the conversion of product.format_ids (FormatReference/dict) to MediaPackage.format_ids
(FormatId objects) in execute_approved_media_buy function.

This tests the critical format conversion logic at lines 391-452 of media_buy_create.py.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import (
    CurrencyLimit,
    MediaBuy,
    MediaPackage,
    PricingOption,
    Principal,
    Product,
    PropertyTag,
    Tenant,
)
from src.core.tools.media_buy_create import ApprovalOutcome
from tests.factories import PricingOptionFactory
from tests.factories.account import DEFAULT_TEST_ACCOUNT_ID as ACCOUNT_ID
from tests.factories.account import seed_default_account
from tests.factories.principal import plaintext_token_for
from tests.helpers.adcp_factories import create_test_db_product
from tests.helpers.media_buy_approval import run_approval
from tests.utils.database_helpers import bind_factories_to_session, vhost_for


def create_media_package(
    media_buy_id: str,
    package_id: str,
    product_id: str,
    budget: float,
    tenant_id: str,
    pricing_option_id: str = "pricing_opt_1",
):
    """Helper function to create MediaPackage record (required for execute_approved_media_buy).

    Note: package_config stores the PackageRequest fields per AdCP spec.
    package_id is an internal field stored separately on MediaPackage, not in package_config.
    """
    with get_db_session() as session:
        media_package = MediaPackage(
            media_buy_id=media_buy_id,
            package_id=package_id,
            budget=Decimal(str(budget)),
            package_config={
                # AdCP PackageRequest fields only - package_id is internal
                "product_id": product_id,
                "budget": budget,
                "pricing_option_id": pricing_option_id,
            },
        )
        session.add(media_package)
        session.commit()


def _assert_approval_failed(result, because: str) -> str:
    """Assert the approval FAILED (not merely "not ok"), and hand back its message.

    ``not result.ok`` is too weak to grade these: it is also true when the buy is HELD
    on unapproved creatives, so a bare boolean would let one of these tests pass for a
    reason that has nothing to do with the format under test. Each caller then asserts
    what its own message must say, against the lowercased text returned here.
    """
    assert result.outcome is ApprovalOutcome.FAILED, (
        f"approval with {because} should have FAILED, got {result.outcome} ({result.error_msg})"
    )
    return (result.error_msg or "").lower()


@pytest.fixture
def test_tenant(integration_db):
    """Create test tenant with mock ad server."""
    tenant_id = "test_format_conversion"
    with get_db_session() as session:
        # Required (NOT NULL) and UNIQUE. Nothing routes to this host; it exists to satisfy the column.
        tenant = Tenant(
            tenant_id=tenant_id,
            name="Format Test Tenant",
            subdomain="formattest",
            virtual_host=vhost_for(tenant_id),
            is_active=True,
            ad_server="mock",
        )
        session.add(tenant)
        session.commit()

    yield tenant_id

    # Cleanup
    with get_db_session() as session:
        stmt = select(Tenant).filter_by(tenant_id=tenant_id)
        tenant = session.scalars(stmt).first()
        if tenant:
            session.delete(tenant)
            session.commit()


@pytest.fixture
def test_currency_limit(integration_db, test_tenant):
    """Create required CurrencyLimit for budget validation.

    Per CLAUDE.md "Database Initialization Dependencies":
    Products require CurrencyLimit for budget validation in media buys.
    """
    with get_db_session() as session:
        currency_limit = CurrencyLimit(
            tenant_id=test_tenant,
            currency_code="USD",
            max_daily_package_spend=100000.0,
        )
        session.add(currency_limit)
        session.commit()

    yield "USD"

    # Cleanup
    with get_db_session() as session:
        stmt = select(CurrencyLimit).filter_by(tenant_id=test_tenant, currency_code="USD")
        limit = session.scalars(stmt).first()
        if limit:
            session.delete(limit)
            session.commit()


@pytest.fixture
def test_property_tag(integration_db, test_tenant):
    """Create required PropertyTag for property_tags array references.

    Per CLAUDE.md "Database Initialization Dependencies":
    Products with property_tags=["all_inventory"] require PropertyTag record.
    """
    with get_db_session() as session:
        property_tag = PropertyTag(
            tenant_id=test_tenant,
            tag_id="all_inventory",
            name="All Inventory",
            description="All available inventory",
        )
        session.add(property_tag)
        session.commit()

    yield "all_inventory"

    # Cleanup
    with get_db_session() as session:
        stmt = select(PropertyTag).filter_by(tenant_id=test_tenant, tag_id="all_inventory")
        tag = session.scalars(stmt).first()
        if tag:
            session.delete(tag)
            session.commit()


@pytest.fixture
def test_principal(integration_db, test_tenant):
    """Create test principal, and the account every media buy below is booked against.

    ``execute_approved_media_buy`` reads the buy's ``account_id`` off the persisted row
    and REFUSES a row that has none -- ``AdCPPersistedStateError``, reported as
    "cannot be acted on: Configuration error" -- because the account is what the
    boundary resolved and is never synthesised on the replay. So each MediaBuy here is
    seeded with ``account_id=ACCOUNT_ID``, which needs the Account row (media_buys
    carries a composite FK to accounts) and the principal's grant on it.
    """
    principal_id = "test_advertiser"
    with get_db_session() as session:
        principal = Principal.with_token(
            plaintext_token_for(principal_id),
            tenant_id=test_tenant,
            principal_id=principal_id,
            name="Test Advertiser",
            platform_mappings={"mock": {"advertiser_id": "test_adv"}},
        )
        session.add(principal)
        session.commit()

    with get_db_session() as session, bind_factories_to_session(session):
        seed_default_account(test_tenant, principal_id)

    yield principal_id

    # Cleanup
    with get_db_session() as session:
        stmt = select(Principal).filter_by(tenant_id=test_tenant, principal_id=principal_id)
        principal = session.scalars(stmt).first()
        if principal:
            session.delete(principal)
            session.commit()


@pytest.mark.requires_db
class TestFormatConversionApproval:
    """Test format conversion during media buy approval execution."""

    def test_valid_format_reference_dict_conversion(
        self, test_tenant, test_principal, test_currency_limit, test_property_tag
    ):
        """✅ Valid FormatReference dict (with format_id) converts to FormatId successfully."""
        product_id = "prod_format_ref"
        media_buy_id = "mb_format_ref"

        with get_db_session() as session:
            # Create product with FormatReference-style dict (has format_id field)
            product = create_test_db_product(
                tenant_id=test_tenant,
                product_id=product_id,
                name="Format Reference Product",
                description="Product with FormatReference format",
                format_ids=[
                    {
                        "agent_url": "https://creatives.example.com",
                        "id": "display_300x250",  # Changed from format_id to id
                    }
                ],
            )
            session.add(product)

            # Add pricing option
            pricing = PricingOptionFactory.build(
                tenant_id=test_tenant,
                product_id=product_id,
                pricing_model="CPM",
                rate=Decimal("10.00"),
                currency="USD",
                is_fixed=True,
            )
            session.add(pricing)

            # Create media buy
            now = datetime.now(UTC)
            media_buy = MediaBuy(
                tenant_id=test_tenant,
                media_buy_id=media_buy_id,
                # The account the buy is booked against; an approval refuses a row
                # without one. See the test_principal fixture.
                account_id=ACCOUNT_ID,
                principal_id=test_principal,
                order_name="Format Ref Test Order",
                advertiser_name="Test Advertiser",
                budget=1000.0,
                start_date=(now + timedelta(days=1)).date(),
                end_date=(now + timedelta(days=7)).date(),
                start_time=now + timedelta(days=1),
                end_time=now + timedelta(days=7),
                status="pending_approval",
                raw_request={
                    "brand": {"domain": "testbrand.com"},
                    "start_time": (now + timedelta(days=1)).isoformat(),
                    "end_time": (now + timedelta(days=7)).isoformat(),
                    "packages": [
                        {
                            # package_id is internal, not in AdCP PackageRequest spec
                            "product_id": product_id,
                            "budget": 1000.0,
                            "pricing_option_id": "pricing_opt_1",
                        }
                    ],
                },
            )
            session.add(media_buy)
            session.commit()

        # Create MediaPackage record (required by execute_approved_media_buy)
        create_media_package(media_buy_id, "pkg_1", product_id, 1000.0, test_tenant)

        # Execute approval
        result = run_approval(media_buy_id, test_tenant)

        assert result.ok, f"Approval should succeed: {result.error_msg}"

        # Cleanup
        with get_db_session() as session:
            # Delete MediaPackage first (foreign key constraint to MediaBuy)
            stmt_pkg = select(MediaPackage).filter_by(media_buy_id=media_buy_id)
            packages = session.scalars(stmt_pkg).all()
            for pkg in packages:
                session.delete(pkg)

            # Then delete MediaBuy
            stmt_mb = select(MediaBuy).filter_by(media_buy_id=media_buy_id)
            media_buy = session.scalars(stmt_mb).first()
            if media_buy:
                session.delete(media_buy)

            # Delete PricingOption (foreign key constraint to Product)
            stmt_pricing = select(PricingOption).filter_by(tenant_id=test_tenant, product_id=product_id)
            pricing_options = session.scalars(stmt_pricing).all()
            for pricing in pricing_options:
                session.delete(pricing)

            # Finally delete Product
            stmt_prod = select(Product).filter_by(product_id=product_id)
            product = session.scalars(stmt_prod).first()
            if product:
                session.delete(product)

            session.commit()

    def test_invalid_format_missing_agent_url(
        self, test_tenant, test_principal, test_currency_limit, test_property_tag
    ):
        """❌ FormatReference dict missing agent_url should fail validation."""
        product_id = "prod_no_agent_url"
        media_buy_id = "mb_no_agent_url"

        with get_db_session() as session:
            # Create product with invalid format (no agent_url)
            product = create_test_db_product(
                tenant_id=test_tenant,
                product_id=product_id,
                name="Invalid Format Product",
                description="Product with missing agent_url",
                format_ids=[
                    {
                        "id": "display_300x250",
                        # Missing agent_url - should fail
                    }
                ],
            )
            session.add(product)

            # Add pricing option
            pricing = PricingOptionFactory.build(
                tenant_id=test_tenant,
                product_id=product_id,
                pricing_model="CPM",
                rate=Decimal("10.00"),
                currency="USD",
                is_fixed=True,
            )
            session.add(pricing)

            # Create media buy
            now = datetime.now(UTC)
            media_buy = MediaBuy(
                tenant_id=test_tenant,
                media_buy_id=media_buy_id,
                # The account the buy is booked against; an approval refuses a row
                # without one. See the test_principal fixture.
                account_id=ACCOUNT_ID,
                principal_id=test_principal,
                order_name="Invalid Format Order",
                advertiser_name="Test Advertiser",
                budget=1000.0,
                start_date=(now + timedelta(days=1)).date(),
                end_date=(now + timedelta(days=7)).date(),
                start_time=now + timedelta(days=1),
                end_time=now + timedelta(days=7),
                status="pending_approval",
                raw_request={
                    "brand": {"domain": "testbrand.com"},
                    "start_time": (now + timedelta(days=1)).isoformat(),
                    "end_time": (now + timedelta(days=7)).isoformat(),
                    "packages": [
                        {
                            # package_id is internal, not in AdCP PackageRequest spec
                            "product_id": product_id,
                            "budget": 1000.0,
                            "pricing_option_id": "pricing_opt_1",
                        }
                    ],
                },
            )
            session.add(media_buy)
            session.commit()

        # Create MediaPackage record (required by execute_approved_media_buy)
        create_media_package(media_buy_id, "pkg_1", product_id, 1000.0, test_tenant)

        # Execute approval - should fail
        result = run_approval(media_buy_id, test_tenant)

        message = _assert_approval_failed(result, "missing agent_url")
        assert "agent_url" in message
        assert "format validation failed" in message

        # Cleanup
        with get_db_session() as session:
            # Delete MediaPackage first (foreign key constraint to MediaBuy)
            stmt_pkg = select(MediaPackage).filter_by(media_buy_id=media_buy_id)
            packages = session.scalars(stmt_pkg).all()
            for pkg in packages:
                session.delete(pkg)

            # Then delete MediaBuy
            stmt_mb = select(MediaBuy).filter_by(media_buy_id=media_buy_id)
            media_buy = session.scalars(stmt_mb).first()
            if media_buy:
                session.delete(media_buy)

            # Delete PricingOption (foreign key constraint to Product)
            stmt_pricing = select(PricingOption).filter_by(tenant_id=test_tenant, product_id=product_id)
            pricing_options = session.scalars(stmt_pricing).all()
            for pricing in pricing_options:
                session.delete(pricing)

            # Finally delete Product
            stmt_prod = select(Product).filter_by(product_id=product_id)
            product = session.scalars(stmt_prod).first()
            if product:
                session.delete(product)

            session.commit()

    def test_invalid_format_empty_agent_url(self, test_tenant, test_principal, test_currency_limit, test_property_tag):
        """❌ FormatReference dict with empty agent_url should fail validation."""
        product_id = "prod_empty_agent_url"
        media_buy_id = "mb_empty_agent_url"

        with get_db_session() as session:
            # Create product with empty agent_url
            product = create_test_db_product(
                tenant_id=test_tenant,
                product_id=product_id,
                name="Empty Agent URL Product",
                description="Product with empty agent_url",
                format_ids=[
                    {
                        "agent_url": "",  # Empty string - should fail
                        "id": "display_300x250",
                    }
                ],
            )
            session.add(product)

            # Add pricing option
            pricing = PricingOptionFactory.build(
                tenant_id=test_tenant,
                product_id=product_id,
                pricing_model="CPM",
                rate=Decimal("10.00"),
                currency="USD",
                is_fixed=True,
            )
            session.add(pricing)

            # Create media buy
            now = datetime.now(UTC)
            media_buy = MediaBuy(
                tenant_id=test_tenant,
                media_buy_id=media_buy_id,
                # The account the buy is booked against; an approval refuses a row
                # without one. See the test_principal fixture.
                account_id=ACCOUNT_ID,
                principal_id=test_principal,
                order_name="Empty Agent URL Order",
                advertiser_name="Test Advertiser",
                budget=1000.0,
                start_date=(now + timedelta(days=1)).date(),
                end_date=(now + timedelta(days=7)).date(),
                start_time=now + timedelta(days=1),
                end_time=now + timedelta(days=7),
                status="pending_approval",
                raw_request={
                    "brand": {"domain": "testbrand.com"},
                    "start_time": (now + timedelta(days=1)).isoformat(),
                    "end_time": (now + timedelta(days=7)).isoformat(),
                    "packages": [
                        {
                            # package_id is internal, not in AdCP PackageRequest spec
                            "product_id": product_id,
                            "budget": 1000.0,
                            "pricing_option_id": "pricing_opt_1",
                        }
                    ],
                },
            )
            session.add(media_buy)
            session.commit()

        # Create MediaPackage record (required by execute_approved_media_buy)
        create_media_package(media_buy_id, "pkg_1", product_id, 1000.0, test_tenant)

        # Execute approval - should fail
        result = run_approval(media_buy_id, test_tenant)

        message = _assert_approval_failed(result, "empty agent_url")
        assert "agent_url" in message

        # Cleanup
        with get_db_session() as session:
            # Delete MediaPackage first (foreign key constraint to MediaBuy)
            stmt_pkg = select(MediaPackage).filter_by(media_buy_id=media_buy_id)
            packages = session.scalars(stmt_pkg).all()
            for pkg in packages:
                session.delete(pkg)

            # Then delete MediaBuy
            stmt_mb = select(MediaBuy).filter_by(media_buy_id=media_buy_id)
            media_buy = session.scalars(stmt_mb).first()
            if media_buy:
                session.delete(media_buy)

            # Delete PricingOption (foreign key constraint to Product)
            stmt_pricing = select(PricingOption).filter_by(tenant_id=test_tenant, product_id=product_id)
            pricing_options = session.scalars(stmt_pricing).all()
            for pricing in pricing_options:
                session.delete(pricing)

            # Finally delete Product
            stmt_prod = select(Product).filter_by(product_id=product_id)
            product = session.scalars(stmt_prod).first()
            if product:
                session.delete(product)

            session.commit()

    def test_invalid_agent_url_not_http(self, test_tenant, test_principal, test_currency_limit, test_property_tag):
        """❌ FormatReference with non-HTTP(S) agent_url should fail validation."""
        product_id = "prod_invalid_url"
        media_buy_id = "mb_invalid_url"

        with get_db_session() as session:
            # Create product with invalid URL scheme
            product = create_test_db_product(
                tenant_id=test_tenant,
                product_id=product_id,
                name="Invalid URL Product",
                description="Product with non-HTTP URL",
                format_ids=[
                    {
                        "agent_url": "ftp://creatives.example.com",  # FTP not allowed
                        "id": "display_300x250",
                    }
                ],
            )
            session.add(product)

            # Add pricing option
            pricing = PricingOptionFactory.build(
                tenant_id=test_tenant,
                product_id=product_id,
                pricing_model="CPM",
                rate=Decimal("10.00"),
                currency="USD",
                is_fixed=True,
            )
            session.add(pricing)

            # Create media buy
            now = datetime.now(UTC)
            media_buy = MediaBuy(
                tenant_id=test_tenant,
                media_buy_id=media_buy_id,
                # The account the buy is booked against; an approval refuses a row
                # without one. See the test_principal fixture.
                account_id=ACCOUNT_ID,
                principal_id=test_principal,
                order_name="Invalid URL Order",
                advertiser_name="Test Advertiser",
                budget=1000.0,
                start_date=(now + timedelta(days=1)).date(),
                end_date=(now + timedelta(days=7)).date(),
                start_time=now + timedelta(days=1),
                end_time=now + timedelta(days=7),
                status="pending_approval",
                raw_request={
                    "brand": {"domain": "testbrand.com"},
                    "start_time": (now + timedelta(days=1)).isoformat(),
                    "end_time": (now + timedelta(days=7)).isoformat(),
                    "packages": [
                        {
                            # package_id is internal, not in AdCP PackageRequest spec
                            "product_id": product_id,
                            "budget": 1000.0,
                            "pricing_option_id": "pricing_opt_1",
                        }
                    ],
                },
            )
            session.add(media_buy)
            session.commit()

        # Create MediaPackage record (required by execute_approved_media_buy)
        create_media_package(media_buy_id, "pkg_1", product_id, 1000.0, test_tenant)

        # Execute approval - should fail
        result = run_approval(media_buy_id, test_tenant)

        message = _assert_approval_failed(result, "non-HTTP URL")
        assert "agent_url" in message
        assert "http" in message

        # Cleanup
        with get_db_session() as session:
            # Delete MediaPackage first (foreign key constraint to MediaBuy)
            stmt_pkg = select(MediaPackage).filter_by(media_buy_id=media_buy_id)
            packages = session.scalars(stmt_pkg).all()
            for pkg in packages:
                session.delete(pkg)

            # Then delete MediaBuy
            stmt_mb = select(MediaBuy).filter_by(media_buy_id=media_buy_id)
            media_buy = session.scalars(stmt_mb).first()
            if media_buy:
                session.delete(media_buy)

            # Delete PricingOption (foreign key constraint to Product)
            stmt_pricing = select(PricingOption).filter_by(tenant_id=test_tenant, product_id=product_id)
            pricing_options = session.scalars(stmt_pricing).all()
            for pricing in pricing_options:
                session.delete(pricing)

            # Finally delete Product
            stmt_prod = select(Product).filter_by(product_id=product_id)
            product = session.scalars(stmt_prod).first()
            if product:
                session.delete(product)

            session.commit()

    def test_invalid_format_missing_format_id(
        self, test_tenant, test_principal, test_currency_limit, test_property_tag
    ):
        """❌ FormatReference dict missing format_id/id should fail validation."""
        product_id = "prod_no_format_id"
        media_buy_id = "mb_no_format_id"

        with get_db_session() as session:
            # Create product with missing format_id
            product = create_test_db_product(
                tenant_id=test_tenant,
                product_id=product_id,
                name="No Format ID Product",
                description="Product with missing format_id",
                format_ids=[
                    {
                        "agent_url": "https://creatives.example.com",
                        # Missing format_id/id - should fail
                    }
                ],
            )
            session.add(product)

            # Add pricing option
            pricing = PricingOptionFactory.build(
                tenant_id=test_tenant,
                product_id=product_id,
                pricing_model="CPM",
                rate=Decimal("10.00"),
                currency="USD",
                is_fixed=True,
            )
            session.add(pricing)

            # Create media buy
            now = datetime.now(UTC)
            media_buy = MediaBuy(
                tenant_id=test_tenant,
                media_buy_id=media_buy_id,
                # The account the buy is booked against; an approval refuses a row
                # without one. See the test_principal fixture.
                account_id=ACCOUNT_ID,
                principal_id=test_principal,
                order_name="No Format ID Order",
                advertiser_name="Test Advertiser",
                budget=1000.0,
                start_date=(now + timedelta(days=1)).date(),
                end_date=(now + timedelta(days=7)).date(),
                start_time=now + timedelta(days=1),
                end_time=now + timedelta(days=7),
                status="pending_approval",
                raw_request={
                    "brand": {"domain": "testbrand.com"},
                    "start_time": (now + timedelta(days=1)).isoformat(),
                    "end_time": (now + timedelta(days=7)).isoformat(),
                    "packages": [
                        {
                            # package_id is internal, not in AdCP PackageRequest spec
                            "product_id": product_id,
                            "budget": 1000.0,
                            "pricing_option_id": "pricing_opt_1",
                        }
                    ],
                },
            )
            session.add(media_buy)
            session.commit()

        # Create MediaPackage record (required by execute_approved_media_buy)
        create_media_package(media_buy_id, "pkg_1", product_id, 1000.0, test_tenant)

        # Execute approval - should fail
        result = run_approval(media_buy_id, test_tenant)

        message = _assert_approval_failed(result, "missing format_id")
        # Error message varies: "no valid formats" or "format validation failed"
        assert "format" in message or "id" in message

        # Cleanup
        with get_db_session() as session:
            # Delete MediaPackage first (foreign key constraint to MediaBuy)
            stmt_pkg = select(MediaPackage).filter_by(media_buy_id=media_buy_id)
            packages = session.scalars(stmt_pkg).all()
            for pkg in packages:
                session.delete(pkg)

            # Then delete MediaBuy
            stmt_mb = select(MediaBuy).filter_by(media_buy_id=media_buy_id)
            media_buy = session.scalars(stmt_mb).first()
            if media_buy:
                session.delete(media_buy)

            # Delete PricingOption (foreign key constraint to Product)
            stmt_pricing = select(PricingOption).filter_by(tenant_id=test_tenant, product_id=product_id)
            pricing_options = session.scalars(stmt_pricing).all()
            for pricing in pricing_options:
                session.delete(pricing)

            # Finally delete Product
            stmt_prod = select(Product).filter_by(product_id=product_id)
            product = session.scalars(stmt_prod).first()
            if product:
                session.delete(product)

            session.commit()

    def test_valid_format_id_dict_conversion(self, test_tenant, test_principal, test_currency_limit, test_property_tag):
        """✅ Valid FormatId dict (with 'id' key) converts successfully."""
        product_id = "prod_format_id"
        media_buy_id = "mb_format_id"

        with get_db_session() as session:
            # Create product with FormatId-style dict (has 'id' field, not 'format_id')
            product = create_test_db_product(
                tenant_id=test_tenant,
                product_id=product_id,
                name="Format ID Product",
                description="Product with FormatId format",
                format_ids=[
                    {
                        "agent_url": "https://creatives.example.com",
                        "id": "display_728x90",  # New field name per AdCP spec
                    }
                ],
            )
            session.add(product)

            # Add pricing option
            pricing = PricingOptionFactory.build(
                tenant_id=test_tenant,
                product_id=product_id,
                pricing_model="CPM",
                rate=Decimal("15.00"),
                currency="USD",
                is_fixed=True,
            )
            session.add(pricing)

            # Create media buy
            now = datetime.now(UTC)
            media_buy = MediaBuy(
                tenant_id=test_tenant,
                media_buy_id=media_buy_id,
                # The account the buy is booked against; an approval refuses a row
                # without one. See the test_principal fixture.
                account_id=ACCOUNT_ID,
                principal_id=test_principal,
                order_name="Format ID Test Order",
                advertiser_name="Test Advertiser",
                budget=1500.0,
                start_date=(now + timedelta(days=1)).date(),
                end_date=(now + timedelta(days=7)).date(),
                start_time=now + timedelta(days=1),
                end_time=now + timedelta(days=7),
                status="pending_approval",
                raw_request={
                    "brand": {"domain": "testbrand.com"},
                    "start_time": (now + timedelta(days=1)).isoformat(),
                    "end_time": (now + timedelta(days=7)).isoformat(),
                    "packages": [
                        {
                            # package_id is internal, not in AdCP PackageRequest spec
                            "product_id": product_id,
                            "budget": 1500.0,
                            "pricing_option_id": "pricing_opt_1",
                        }
                    ],
                },
            )
            session.add(media_buy)
            session.commit()

        # Create MediaPackage record (required by execute_approved_media_buy)
        create_media_package(media_buy_id, "pkg_1", product_id, 1000.0, test_tenant)

        # Execute approval
        result = run_approval(media_buy_id, test_tenant)

        assert result.ok, f"Approval should succeed: {result.error_msg}"

        # Cleanup
        with get_db_session() as session:
            # Delete MediaPackage first (foreign key constraint to MediaBuy)
            stmt_pkg = select(MediaPackage).filter_by(media_buy_id=media_buy_id)
            packages = session.scalars(stmt_pkg).all()
            for pkg in packages:
                session.delete(pkg)

            # Then delete MediaBuy
            stmt_mb = select(MediaBuy).filter_by(media_buy_id=media_buy_id)
            media_buy = session.scalars(stmt_mb).first()
            if media_buy:
                session.delete(media_buy)

            # Delete PricingOption (foreign key constraint to Product)
            stmt_pricing = select(PricingOption).filter_by(tenant_id=test_tenant, product_id=product_id)
            pricing_options = session.scalars(stmt_pricing).all()
            for pricing in pricing_options:
                session.delete(pricing)

            # Finally delete Product
            stmt_prod = select(Product).filter_by(product_id=product_id)
            product = session.scalars(stmt_prod).first()
            if product:
                session.delete(product)

            session.commit()

    def test_invalid_dict_missing_id(self, test_tenant, test_principal, test_currency_limit, test_property_tag):
        """❌ Dict with neither 'id' nor 'format_id' should fail validation."""
        product_id = "prod_missing_both"
        media_buy_id = "mb_missing_both"

        with get_db_session() as session:
            # Create product with dict missing both id fields
            product = create_test_db_product(
                tenant_id=test_tenant,
                product_id=product_id,
                name="Missing ID Product",
                description="Product with dict missing both id fields",
                format_ids=[
                    {
                        "agent_url": "https://creatives.example.com",
                        "name": "Display Ad",  # Wrong field - not id or format_id
                    }
                ],
            )
            session.add(product)

            # Add pricing option
            pricing = PricingOptionFactory.build(
                tenant_id=test_tenant,
                product_id=product_id,
                pricing_model="CPM",
                rate=Decimal("10.00"),
                currency="USD",
                is_fixed=True,
            )
            session.add(pricing)

            # Create media buy
            now = datetime.now(UTC)
            media_buy = MediaBuy(
                tenant_id=test_tenant,
                media_buy_id=media_buy_id,
                # The account the buy is booked against; an approval refuses a row
                # without one. See the test_principal fixture.
                account_id=ACCOUNT_ID,
                principal_id=test_principal,
                order_name="Missing Both IDs Order",
                advertiser_name="Test Advertiser",
                budget=1000.0,
                start_date=(now + timedelta(days=1)).date(),
                end_date=(now + timedelta(days=7)).date(),
                start_time=now + timedelta(days=1),
                end_time=now + timedelta(days=7),
                status="pending_approval",
                raw_request={
                    "brand": {"domain": "testbrand.com"},
                    "start_time": (now + timedelta(days=1)).isoformat(),
                    "end_time": (now + timedelta(days=7)).isoformat(),
                    "packages": [
                        {
                            # package_id is internal, not in AdCP PackageRequest spec
                            "product_id": product_id,
                            "budget": 1000.0,
                            "pricing_option_id": "pricing_opt_1",
                        }
                    ],
                },
            )
            session.add(media_buy)
            session.commit()

        # Create MediaPackage record (required by execute_approved_media_buy)
        create_media_package(media_buy_id, "pkg_1", product_id, 1000.0, test_tenant)

        # Execute approval - should fail
        result = run_approval(media_buy_id, test_tenant)

        message = _assert_approval_failed(result, "missing id/format_id")
        assert "id" in message

        # Cleanup
        with get_db_session() as session:
            # Delete MediaPackage first (foreign key constraint to MediaBuy)
            stmt_pkg = select(MediaPackage).filter_by(media_buy_id=media_buy_id)
            packages = session.scalars(stmt_pkg).all()
            for pkg in packages:
                session.delete(pkg)

            # Then delete MediaBuy
            stmt_mb = select(MediaBuy).filter_by(media_buy_id=media_buy_id)
            media_buy = session.scalars(stmt_mb).first()
            if media_buy:
                session.delete(media_buy)

            # Delete PricingOption (foreign key constraint to Product)
            stmt_pricing = select(PricingOption).filter_by(tenant_id=test_tenant, product_id=product_id)
            pricing_options = session.scalars(stmt_pricing).all()
            for pricing in pricing_options:
                session.delete(pricing)

            # Finally delete Product
            stmt_prod = select(Product).filter_by(product_id=product_id)
            product = session.scalars(stmt_prod).first()
            if product:
                session.delete(product)

            session.commit()

    def test_empty_formats_list_fails(self, test_tenant, test_principal, test_currency_limit, test_property_tag):
        """❌ Product with empty formats list should fail validation."""
        product_id = "prod_empty_formats"
        media_buy_id = "mb_empty_formats"

        with get_db_session() as session:
            # Create product with empty formats list
            product = create_test_db_product(
                tenant_id=test_tenant,
                product_id=product_id,
                name="Empty Formats Product",
                description="Product with no formats",
                format_ids=[],  # Empty list - should fail
            )
            session.add(product)

            # Add pricing option
            pricing = PricingOptionFactory.build(
                tenant_id=test_tenant,
                product_id=product_id,
                pricing_model="CPM",
                rate=Decimal("10.00"),
                currency="USD",
                is_fixed=True,
            )
            session.add(pricing)

            # Create media buy
            now = datetime.now(UTC)
            media_buy = MediaBuy(
                tenant_id=test_tenant,
                media_buy_id=media_buy_id,
                # The account the buy is booked against; an approval refuses a row
                # without one. See the test_principal fixture.
                account_id=ACCOUNT_ID,
                principal_id=test_principal,
                order_name="Empty Formats Order",
                advertiser_name="Test Advertiser",
                budget=1000.0,
                start_date=(now + timedelta(days=1)).date(),
                end_date=(now + timedelta(days=7)).date(),
                start_time=now + timedelta(days=1),
                end_time=now + timedelta(days=7),
                status="pending_approval",
                raw_request={
                    "brand": {"domain": "testbrand.com"},
                    "start_time": (now + timedelta(days=1)).isoformat(),
                    "end_time": (now + timedelta(days=7)).isoformat(),
                    "packages": [
                        {
                            # package_id is internal, not in AdCP PackageRequest spec
                            "product_id": product_id,
                            "budget": 1000.0,
                            "pricing_option_id": "pricing_opt_1",
                        }
                    ],
                },
            )
            session.add(media_buy)
            session.commit()

        # Create MediaPackage record (required by execute_approved_media_buy)
        create_media_package(media_buy_id, "pkg_1", product_id, 1000.0, test_tenant)

        # Execute approval - should fail
        result = run_approval(media_buy_id, test_tenant)

        message = _assert_approval_failed(result, "empty formats")
        assert "no valid formats" in message

        # Cleanup
        with get_db_session() as session:
            # Delete MediaPackage first (foreign key constraint to MediaBuy)
            stmt_pkg = select(MediaPackage).filter_by(media_buy_id=media_buy_id)
            packages = session.scalars(stmt_pkg).all()
            for pkg in packages:
                session.delete(pkg)

            # Then delete MediaBuy
            stmt_mb = select(MediaBuy).filter_by(media_buy_id=media_buy_id)
            media_buy = session.scalars(stmt_mb).first()
            if media_buy:
                session.delete(media_buy)

            # Delete PricingOption (foreign key constraint to Product)
            stmt_pricing = select(PricingOption).filter_by(tenant_id=test_tenant, product_id=product_id)
            pricing_options = session.scalars(stmt_pricing).all()
            for pricing in pricing_options:
                session.delete(pricing)

            # Finally delete Product
            stmt_prod = select(Product).filter_by(product_id=product_id)
            product = session.scalars(stmt_prod).first()
            if product:
                session.delete(product)

            session.commit()

    def test_mixed_valid_format_types(self, test_tenant, test_principal, test_currency_limit, test_property_tag):
        """✅ Product with mixed valid format types (FormatRef, FormatId, dict) succeeds."""
        product_id = "prod_mixed_formats"
        media_buy_id = "mb_mixed_formats"

        with get_db_session() as session:
            # Create product with multiple format types
            product = create_test_db_product(
                tenant_id=test_tenant,
                product_id=product_id,
                name="Mixed Formats Product",
                description="Product with different format styles",
                format_ids=[
                    # FormatId style
                    {
                        "agent_url": "https://creatives.example.com",
                        "id": "display_300x250",
                    },
                    # FormatId style
                    {
                        "agent_url": "https://creatives.example.com",
                        "id": "display_728x90",
                    },
                    # FormatId style
                    {
                        "agent_url": "https://creatives.example.com",
                        "id": "display_160x600",
                    },
                ],
            )
            session.add(product)

            # Add pricing option
            pricing = PricingOptionFactory.build(
                tenant_id=test_tenant,
                product_id=product_id,
                pricing_model="CPM",
                rate=Decimal("20.00"),
                currency="USD",
                is_fixed=True,
            )
            session.add(pricing)

            # Create media buy
            now = datetime.now(UTC)
            media_buy = MediaBuy(
                tenant_id=test_tenant,
                media_buy_id=media_buy_id,
                # The account the buy is booked against; an approval refuses a row
                # without one. See the test_principal fixture.
                account_id=ACCOUNT_ID,
                principal_id=test_principal,
                order_name="Mixed Formats Order",
                advertiser_name="Test Advertiser",
                budget=2000.0,
                start_date=(now + timedelta(days=1)).date(),
                end_date=(now + timedelta(days=7)).date(),
                start_time=now + timedelta(days=1),
                end_time=now + timedelta(days=7),
                status="pending_approval",
                raw_request={
                    "brand": {"domain": "testbrand.com"},
                    "start_time": (now + timedelta(days=1)).isoformat(),
                    "end_time": (now + timedelta(days=7)).isoformat(),
                    "packages": [
                        {
                            # package_id is internal, not in AdCP PackageRequest spec
                            "product_id": product_id,
                            "budget": 2000.0,
                            "pricing_option_id": "pricing_opt_1",
                        }
                    ],
                },
            )
            session.add(media_buy)
            session.commit()

        # Create MediaPackage record (required by execute_approved_media_buy)
        create_media_package(media_buy_id, "pkg_1", product_id, 1000.0, test_tenant)

        # Execute approval - should succeed
        result = run_approval(media_buy_id, test_tenant)

        assert result.ok, f"Approval should succeed with mixed formats: {result.error_msg}"
        # Success returns (True, None), so no message to check

        # Cleanup
        with get_db_session() as session:
            # Delete MediaPackage first (foreign key constraint to MediaBuy)
            stmt_pkg = select(MediaPackage).filter_by(media_buy_id=media_buy_id)
            packages = session.scalars(stmt_pkg).all()
            for pkg in packages:
                session.delete(pkg)

            # Then delete MediaBuy
            stmt_mb = select(MediaBuy).filter_by(media_buy_id=media_buy_id)
            media_buy = session.scalars(stmt_mb).first()
            if media_buy:
                session.delete(media_buy)

            # Delete PricingOption (foreign key constraint to Product)
            stmt_pricing = select(PricingOption).filter_by(tenant_id=test_tenant, product_id=product_id)
            pricing_options = session.scalars(stmt_pricing).all()
            for pricing in pricing_options:
                session.delete(pricing)

            # Finally delete Product
            stmt_prod = select(Product).filter_by(product_id=product_id)
            product = session.scalars(stmt_prod).first()
            if product:
                session.delete(product)

            session.commit()

    def test_invalid_format_unknown_type(self, test_tenant, test_principal, test_currency_limit, test_property_tag):
        """❌ Format with unknown type (string, int) should fail validation."""
        product_id = "prod_invalid_type"
        media_buy_id = "mb_invalid_type"

        with get_db_session() as session:
            # Create product with invalid format type (string instead of dict)
            product = create_test_db_product(
                tenant_id=test_tenant,
                product_id=product_id,
                name="Invalid Type Product",
                description="Product with string format (should be dict)",
                format_ids=["display_300x250"],  # String instead of dict - should fail
            )
            session.add(product)

            # Add pricing option
            pricing = PricingOptionFactory.build(
                tenant_id=test_tenant,
                product_id=product_id,
                pricing_model="CPM",
                rate=Decimal("10.00"),
                currency="USD",
                is_fixed=True,
            )
            session.add(pricing)

            # Create media buy
            now = datetime.now(UTC)
            media_buy = MediaBuy(
                tenant_id=test_tenant,
                media_buy_id=media_buy_id,
                # The account the buy is booked against; an approval refuses a row
                # without one. See the test_principal fixture.
                account_id=ACCOUNT_ID,
                principal_id=test_principal,
                order_name="Invalid Type Order",
                advertiser_name="Test Advertiser",
                budget=1000.0,
                start_date=(now + timedelta(days=1)).date(),
                end_date=(now + timedelta(days=7)).date(),
                start_time=now + timedelta(days=1),
                end_time=now + timedelta(days=7),
                status="pending_approval",
                raw_request={
                    "brand": {"domain": "testbrand.com"},
                    "start_time": (now + timedelta(days=1)).isoformat(),
                    "end_time": (now + timedelta(days=7)).isoformat(),
                    "packages": [
                        {
                            # package_id is internal, not in AdCP PackageRequest spec
                            "product_id": product_id,
                            "budget": 1000.0,
                            "pricing_option_id": "pricing_opt_1",
                        }
                    ],
                },
            )
            session.add(media_buy)
            session.commit()

        # Create MediaPackage record (required by execute_approved_media_buy)
        create_media_package(media_buy_id, "pkg_1", product_id, 1000.0, test_tenant)

        # Execute approval - should fail
        result = run_approval(media_buy_id, test_tenant)

        message = _assert_approval_failed(result, "unknown format type")
        assert "unknown format type" in message or "format validation failed" in message

        # Cleanup
        with get_db_session() as session:
            # Delete MediaPackage first (foreign key constraint to MediaBuy)
            stmt_pkg = select(MediaPackage).filter_by(media_buy_id=media_buy_id)
            packages = session.scalars(stmt_pkg).all()
            for pkg in packages:
                session.delete(pkg)

            # Then delete MediaBuy
            stmt_mb = select(MediaBuy).filter_by(media_buy_id=media_buy_id)
            media_buy = session.scalars(stmt_mb).first()
            if media_buy:
                session.delete(media_buy)

            # Delete PricingOption (foreign key constraint to Product)
            stmt_pricing = select(PricingOption).filter_by(tenant_id=test_tenant, product_id=product_id)
            pricing_options = session.scalars(stmt_pricing).all()
            for pricing in pricing_options:
                session.delete(pricing)

            # Finally delete Product
            stmt_prod = select(Product).filter_by(product_id=product_id)
            product = session.scalars(stmt_prod).first()
            if product:
                session.delete(product)

            session.commit()
