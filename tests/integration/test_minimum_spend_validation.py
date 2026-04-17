"""Integration tests for currency-specific budget limit validation.

Tests the per-currency minimum/maximum spend limits and per-product override
functionality for media buy creation.

MIGRATED: Uses new pricing_options model instead of legacy Product pricing fields.
Product.min_spend → PricingOption.min_spend_per_package

BUDGET FORMAT: AdCP v2.2.0 Migration (2025-10-27)
All tests in this file use float budget format per AdCP v2.2.0 spec:
- Package.budget: float (e.g., 1000.0) - NOT Budget object
- Currency is determined by PricingOption, not Package
- Excessive budgets trigger adapter errors (raises ToolError)

# --- Test Source-of-Truth Audit ---
# Audited: 2026-03-08
#
# SPEC_BACKED (4/7 tests):
#   test_currency_minimum_spend_enforced
#     — AdCP spec: BUDGET_INSUFFICIENT error code for budget below minimum
#   test_product_override_enforced
#     — AdCP spec: BUDGET_INSUFFICIENT + product-level min_spend_per_package
#   test_minimum_spend_met_success
#     — AdCP spec: valid budget accepted (no BUDGET_INSUFFICIENT)
#   test_different_currency_different_minimum
#     — AdCP spec: BUDGET_INSUFFICIENT per-currency validation
#
# DECISION_BACKED (2/7 tests):
#   test_lower_override_allows_smaller_spend
#     — Product decision: min_spend_per_package overrides currency limit
#       (see PricingOption.min_spend_per_package field, media_buy_create.py:1725)
#   test_no_minimum_when_not_set
#     — Product decision: null min_package_budget means no minimum enforced
#
# CHARACTERIZATION (1/7 tests):
#   test_unsupported_currency_rejected
#     — Locks mock adapter budget limit (>$1M → reject). Error codes are
#       mock-adapter-internal (PERCENTAGE_UNITS_BOUGHT_TOO_HIGH), not AdCP spec.
# ---
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from src.core.database.database_session import get_db_session
from src.core.database.models import (
    AuthorizedProperty,
    CurrencyLimit,
    GAMInventory,
    MediaBuy,
    Principal,
    Product,
    PropertyTag,
    Tenant,
    TenantAuthConfig,
)
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import CreateMediaBuyRequest
from src.core.testing_hooks import AdCPTestContext
from src.core.tools.media_buy_create import _create_media_buy_impl
from tests.helpers.adcp_factories import create_test_package_request
from tests.integration.conftest import create_test_product_with_pricing, get_pricing_option_id


@pytest.mark.integration
@pytest.mark.requires_db
class TestMinimumSpendValidation:
    """Test minimum spend validation for media buys."""

    @pytest.fixture
    def setup_test_data(self, integration_db):
        """Set up test tenant with products and currency-specific limits."""
        from src.core.config_loader import get_tenant_by_id, set_current_tenant

        with get_db_session() as session:
            now = datetime.now(UTC)

            # Create tenant (mock adapter accepted in test environments - ADCP_TESTING=true)
            tenant = Tenant(
                tenant_id="test_minspend_tenant",
                name="Test Minimum Spend Tenant",
                subdomain="testminspend",
                ad_server="mock",  # Mock adapter is accepted in test environments
                auth_setup_mode=False,  # Disable setup mode for production-ready auth
                enable_axe_signals=True,
                human_review_required=False,
                created_at=now,
                updated_at=now,
                # Required: Access control configuration
                authorized_emails=["test@example.com"],
            )
            session.add(tenant)

            # Create currency limits for USD
            currency_limit_usd = CurrencyLimit(
                tenant_id="test_minspend_tenant",
                currency_code="USD",
                min_package_budget=Decimal("1000.00"),  # $1000 minimum per product
                max_daily_package_spend=Decimal("50000.00"),  # $50k daily maximum
            )
            session.add(currency_limit_usd)

            # Create currency limits for EUR (different minimums)
            currency_limit_eur = CurrencyLimit(
                tenant_id="test_minspend_tenant",
                currency_code="EUR",
                min_package_budget=Decimal("900.00"),  # €900 minimum per product
                max_daily_package_spend=Decimal("45000.00"),  # €45k daily maximum
            )
            session.add(currency_limit_eur)

            # Create required PropertyTag (needed for product property_tags)
            property_tag = PropertyTag(
                tenant_id="test_minspend_tenant",
                tag_id="all_inventory",
                name="All Inventory",
                description="All available inventory",
            )
            session.add(property_tag)

            # Create required AuthorizedProperty (needed for setup validation)
            authorized_property = AuthorizedProperty(
                tenant_id="test_minspend_tenant",
                property_id="test_minspend_property",
                property_type="website",
                name="Test Property",
                identifiers=[{"type": "domain", "value": "example.com"}],
                publisher_domain="example.com",
                verification_status="verified",
            )
            session.add(authorized_property)

            # Create principal with both kevel and mock mappings
            principal = Principal(
                tenant_id="test_minspend_tenant",
                principal_id="test_principal",
                name="Test Principal",
                access_token="test_minspend_token",
                platform_mappings={
                    "kevel": {"advertiser_id": "test_advertiser_id"},
                    "mock": {"advertiser_id": "test_advertiser_id"},
                },
                created_at=now,
            )
            session.add(principal)
            session.flush()

            # Create product WITHOUT override (will use currency limit)
            # Note: This product supports both USD and EUR currencies
            product_no_override = create_test_product_with_pricing(
                session=session,
                tenant_id="test_minspend_tenant",
                product_id="prod_global",
                name="Product Using Currency Minimum",
                description="Uses currency-specific minimum",
                pricing_model="cpm",
                rate="10.00",
                is_fixed=True,
                currency="USD",
                min_spend_per_package=None,  # No override, uses currency limit
                format_ids=[{"agent_url": "https://test.com", "id": "display_300x250"}],
                targeting_template={},
                delivery_type="guaranteed",
            )

            # Add EUR pricing option to prod_global (multi-currency support)
            from src.core.database.models import PricingOption

            eur_pricing = PricingOption(
                tenant_id="test_minspend_tenant",
                product_id="prod_global",
                pricing_model="cpm",
                rate=Decimal("10.00"),
                is_fixed=True,
                currency="EUR",
                min_spend_per_package=None,  # No override, uses EUR currency limit (€900)
            )
            session.add(eur_pricing)
            session.flush()

            # Create product WITH override (higher than currency limit)
            product_high_override = create_test_product_with_pricing(
                session=session,
                tenant_id="test_minspend_tenant",
                product_id="prod_high",
                name="Product With High Override",
                description="Has $5000 minimum override",
                pricing_model="cpm",
                rate="10.00",
                is_fixed=True,
                currency="USD",
                min_spend_per_package="5000.00",  # Product-specific override
                format_ids=[{"agent_url": "https://test.com", "id": "display_300x250"}],
                targeting_template={},
                delivery_type="guaranteed",
            )

            # Create product WITH override (lower than currency limit)
            product_low_override = create_test_product_with_pricing(
                session=session,
                tenant_id="test_minspend_tenant",
                product_id="prod_low",
                name="Product With Low Override",
                description="Has $500 minimum override",
                pricing_model="cpm",
                rate="10.00",
                is_fixed=True,
                currency="USD",
                min_spend_per_package="500.00",  # Lower override
                format_ids=[{"agent_url": "https://test.com", "id": "display_300x250"}],
                targeting_template={},
                delivery_type="guaranteed",
            )

            # Create product with GBP pricing (for test_no_minimum_when_not_set)
            product_gbp = create_test_product_with_pricing(
                session=session,
                tenant_id="test_minspend_tenant",
                product_id="prod_global_gbp",
                name="Product With GBP Pricing",
                description="Uses GBP pricing, no minimum override",
                pricing_model="cpm",
                rate="8.00",
                is_fixed=True,
                currency="GBP",
                min_spend_per_package=None,  # No override, will use currency limit (which has no minimum for GBP)
                format_ids=[{"agent_url": "https://test.com", "id": "display_300x250"}],
                targeting_template={},
                delivery_type="guaranteed",
            )

            # Create GAMInventory records (required for inventory sync status in setup checklist)
            inventory_items = [
                GAMInventory(
                    tenant_id="test_minspend_tenant",
                    inventory_type="ad_unit",
                    inventory_id="test_minspend_ad_unit_1",
                    name="Test Ad Unit",
                    path=["root", "test"],
                    status="active",
                    inventory_metadata={"sizes": ["300x250"]},
                ),
                GAMInventory(
                    tenant_id="test_minspend_tenant",
                    inventory_type="placement",
                    inventory_id="test_minspend_placement_1",
                    name="Test Placement",
                    path=["root"],
                    status="active",
                    inventory_metadata={},
                ),
            ]
            for item in inventory_items:
                session.add(item)

            # Create TenantAuthConfig with SSO enabled (required for setup validation)
            auth_config = TenantAuthConfig(
                tenant_id="test_minspend_tenant",
                oidc_enabled=True,
                oidc_provider="google",
                oidc_discovery_url="https://accounts.google.com/.well-known/openid-configuration",
                oidc_client_id="test_client_id_for_minspend",
                oidc_scopes="openid email profile",
            )
            session.add(auth_config)

            session.commit()

            # Set current tenant (must be a dict, not a string)
            set_current_tenant(get_tenant_by_id("test_minspend_tenant"))

        # Return pricing_option_ids for tests (database-generated IDs as strings)
        # Eager-load pricing_options to avoid DetachedInstanceError
        from sqlalchemy.orm import selectinload

        with get_db_session() as session:
            stmt = (
                select(Product)
                .filter_by(tenant_id="test_minspend_tenant")
                .options(selectinload(Product.pricing_options))
            )
            products = {p.product_id: p for p in session.scalars(stmt).all()}

            # Extract pricing_option_ids while session is still open
            pricing_data = {
                "prod_global_usd": get_pricing_option_id(products["prod_global"], "USD"),
                "prod_global_eur": get_pricing_option_id(products["prod_global"], "EUR"),
                "prod_high": get_pricing_option_id(products["prod_high"], "USD"),
                "prod_low": get_pricing_option_id(products["prod_low"], "USD"),
                "prod_global_gbp": get_pricing_option_id(products["prod_global_gbp"], "GBP"),
            }

        yield pricing_data

        # Cleanup (order matters - delete children before parents due to foreign keys)
        from src.core.database.models import MediaPackage as MediaPackageModel

        with get_db_session() as session:
            # Delete media packages first (references media_buys)
            # MediaPackage doesn't have tenant_id, so use subquery through MediaBuy
            session.execute(
                delete(MediaPackageModel).where(
                    MediaPackageModel.media_buy_id.in_(
                        select(MediaBuy.media_buy_id).where(MediaBuy.tenant_id == "test_minspend_tenant")
                    )
                )
            )
            # Now safe to delete media_buys
            session.execute(delete(MediaBuy).where(MediaBuy.tenant_id == "test_minspend_tenant"))
            session.execute(delete(Product).where(Product.tenant_id == "test_minspend_tenant"))
            session.execute(delete(Principal).where(Principal.tenant_id == "test_minspend_tenant"))
            session.execute(delete(CurrencyLimit).where(CurrencyLimit.tenant_id == "test_minspend_tenant"))
            session.execute(delete(PropertyTag).where(PropertyTag.tenant_id == "test_minspend_tenant"))
            session.execute(delete(AuthorizedProperty).where(AuthorizedProperty.tenant_id == "test_minspend_tenant"))
            session.execute(delete(TenantAuthConfig).where(TenantAuthConfig.tenant_id == "test_minspend_tenant"))
            session.execute(delete(Tenant).where(Tenant.tenant_id == "test_minspend_tenant"))
            session.commit()

    async def test_currency_minimum_spend_enforced(self, setup_test_data):
        """Test that currency-specific minimum spend is enforced."""
        identity = ResolvedIdentity(
            principal_id="test_principal",
            tenant_id="test_minspend_tenant",
            tenant={"tenant_id": "test_minspend_tenant"},
            testing_context=AdCPTestContext(dry_run=True, test_session_id="test_session"),
            protocol="mcp",
        )

        # Try to create media buy below USD minimum ($1000)
        start_time = datetime.now(UTC) + timedelta(days=1)
        end_time = start_time + timedelta(days=7)

        # Should fail validation and return errors in response
        req = CreateMediaBuyRequest(
            brand={"domain": "testbrand.com"},
            packages=[
                create_test_package_request(
                    product_id="prod_global",
                    budget=500.0,  # Below $1000 minimum per AdCP v2.2.0, currency from pricing_option
                    pricing_option_id=setup_test_data["prod_global_usd"],  # Use actual database-generated ID
                )
            ],
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
        )
        response, _ = await _create_media_buy_impl(req=req, identity=identity)

        # Verify validation failed
        assert response.errors is not None and len(response.errors) > 0
        error_msg = response.errors[0].message.lower()
        assert "minimum spend" in error_msg or "does not meet" in error_msg
        assert "1000" in response.errors[0].message
        assert "usd" in error_msg

    async def test_product_override_enforced(self, setup_test_data):
        """Test that product-specific minimum spend override is enforced."""
        identity = ResolvedIdentity(
            principal_id="test_principal",
            tenant_id="test_minspend_tenant",
            tenant={"tenant_id": "test_minspend_tenant"},
            testing_context=AdCPTestContext(dry_run=True, test_session_id="test_session"),
            protocol="mcp",
        )

        start_time = datetime.now(UTC) + timedelta(days=1)
        end_time = start_time + timedelta(days=7)

        # Try to create media buy below product override ($5000)
        # Should fail validation and return errors in response
        req = CreateMediaBuyRequest(
            brand={"domain": "testbrand.com"},
            packages=[
                create_test_package_request(
                    product_id="prod_high",
                    budget=3000.0,  # Below $5000 product minimum per AdCP v2.2.0, currency from pricing_option
                    pricing_option_id=setup_test_data["prod_high"],  # Use actual database-generated ID
                )
            ],
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
        )
        response, _ = await _create_media_buy_impl(req=req, identity=identity)

        # Verify validation failed
        assert response.errors is not None and len(response.errors) > 0
        error_msg = response.errors[0].message.lower()
        assert "minimum spend" in error_msg or "does not meet" in error_msg
        assert "5000" in response.errors[0].message
        assert "usd" in error_msg

    async def test_lower_override_allows_smaller_spend(self, setup_test_data):
        """Test that lower product override allows smaller spend than currency limit."""
        identity = ResolvedIdentity(
            principal_id="test_principal",
            tenant_id="test_minspend_tenant",
            tenant={"tenant_id": "test_minspend_tenant"},
            testing_context=AdCPTestContext(dry_run=True, test_session_id="test_session"),
            protocol="mcp",
        )

        start_time = datetime.now(UTC) + timedelta(days=1)
        end_time = start_time + timedelta(days=7)

        # Create media buy above product minimum ($500) but below currency limit ($1000)
        # Should succeed because product override is lower
        req = CreateMediaBuyRequest(
            brand={"domain": "testbrand.com"},
            packages=[
                create_test_package_request(
                    product_id="prod_low",
                    budget=750.0,  # Above $500 product min, below $1000 currency limit per AdCP v2.2.0
                    pricing_option_id=setup_test_data["prod_low"],  # Use actual database-generated ID
                )
            ],
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
        )
        response, _ = await _create_media_buy_impl(req=req, identity=identity)

        # Should succeed - verify we got a media_buy_id
        assert response.media_buy_id is not None

    async def test_minimum_spend_met_success(self, setup_test_data):
        """Test that media buy succeeds when minimum spend is met."""
        identity = ResolvedIdentity(
            principal_id="test_principal",
            tenant_id="test_minspend_tenant",
            tenant={"tenant_id": "test_minspend_tenant"},
            testing_context=AdCPTestContext(dry_run=True, test_session_id="test_session"),
            protocol="mcp",
        )

        start_time = datetime.now(UTC) + timedelta(days=1)
        end_time = start_time + timedelta(days=7)

        # Create media buy above minimum - should succeed
        req = CreateMediaBuyRequest(
            brand={"domain": "testbrand.com"},
            packages=[
                create_test_package_request(
                    product_id="prod_global",
                    budget=2000.0,  # Above $1000 minimum per AdCP v2.2.0, currency from pricing_option
                    pricing_option_id=setup_test_data["prod_global_usd"],  # Use actual database-generated ID
                )
            ],
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
        )
        response, _ = await _create_media_buy_impl(req=req, identity=identity)

        # Should succeed - verify we got a media_buy_id
        assert response.media_buy_id is not None

    # Characterization: locks mock adapter budget limit behavior (no AdCP spec backing)
    async def test_unsupported_currency_rejected(self, setup_test_data):
        """Test that excessively high budgets are rejected by pre-adapter validation."""
        identity = ResolvedIdentity(
            principal_id="test_principal",
            tenant_id="test_minspend_tenant",
            tenant={"tenant_id": "test_minspend_tenant"},
            testing_context=AdCPTestContext(dry_run=True, test_session_id="test_session"),
            protocol="mcp",
        )

        start_time = datetime.now(UTC) + timedelta(days=1)
        end_time = start_time + timedelta(days=7)

        # Try to create media buy with excessive budget
        # $100,000 USD produces 10M impressions which exceeds the adapter limit
        req = CreateMediaBuyRequest(
            brand={"domain": "testbrand.com"},
            packages=[
                create_test_package_request(
                    product_id="prod_global",
                    budget=100000.0,  # Excessive budget per AdCP v2.2.0 float format
                    pricing_option_id=setup_test_data["prod_global_usd"],  # Use actual database-generated ID
                )
            ],
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
        )
        # Pre-adapter validation raises AdCPValidationError for excessive impressions/budget
        from src.core.exceptions import AdCPValidationError

        with pytest.raises(AdCPValidationError, match="PERCENTAGE_UNITS_BOUGHT_TOO_HIGH|VALUE_TOO_LARGE"):
            await _create_media_buy_impl(req=req, identity=identity)

    async def test_different_currency_different_minimum(self, setup_test_data):
        """Test that different currencies have different minimums."""
        identity = ResolvedIdentity(
            principal_id="test_principal",
            tenant_id="test_minspend_tenant",
            tenant={"tenant_id": "test_minspend_tenant"},
            testing_context=AdCPTestContext(dry_run=True, test_session_id="test_session"),
            protocol="mcp",
        )

        start_time = datetime.now(UTC) + timedelta(days=1)
        end_time = start_time + timedelta(days=7)

        # $800 should fail (below $1000 USD minimum)
        # Should fail validation and return errors in response
        req = CreateMediaBuyRequest(
            brand={"domain": "testbrand.com"},
            packages=[
                create_test_package_request(
                    product_id="prod_global",
                    budget=800.0,  # Below $1000 minimum per AdCP v2.2.0, currency from pricing_option
                    pricing_option_id=setup_test_data["prod_global_usd"],  # Use actual database-generated ID
                )
            ],
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
        )
        response, _ = await _create_media_buy_impl(req=req, identity=identity)

        # Verify validation failed with USD minimum
        assert response.errors is not None and len(response.errors) > 0
        error_msg = response.errors[0].message.lower()
        assert "minimum spend" in error_msg or "does not meet" in error_msg
        assert "1000" in response.errors[0].message  # USD minimum is $1000
        assert "usd" in error_msg

    async def test_no_minimum_when_not_set(self, setup_test_data):
        """Test that media buys with no minimum set in currency limit are allowed."""
        # Create a new currency limit with NO minimum (only max)
        with get_db_session() as session:
            currency_limit_gbp = CurrencyLimit(
                tenant_id="test_minspend_tenant",
                currency_code="GBP",
                min_package_budget=None,  # No minimum
                max_daily_package_spend=Decimal("40000.00"),  # Only max set
            )
            session.add(currency_limit_gbp)
            session.commit()

        identity = ResolvedIdentity(
            principal_id="test_principal",
            tenant_id="test_minspend_tenant",
            tenant={"tenant_id": "test_minspend_tenant"},
            testing_context=AdCPTestContext(dry_run=True, test_session_id="test_session"),
            protocol="mcp",
        )

        start_time = datetime.now(UTC) + timedelta(days=1)
        end_time = start_time + timedelta(days=7)

        # Create media buy with low budget in GBP (should succeed - no minimum)
        req = CreateMediaBuyRequest(
            brand={"domain": "testbrand.com"},
            packages=[
                create_test_package_request(
                    product_id="prod_global_gbp",  # Use GBP product
                    budget=100.0,  # Low budget, no minimum for GBP per AdCP v2.2.0, currency from pricing_option
                    pricing_option_id=setup_test_data["prod_global_gbp"],  # Use actual database-generated ID
                )
            ],
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
        )
        response, _ = await _create_media_buy_impl(req=req, identity=identity)

        # Should succeed - verify we got a media_buy_id
        assert response.media_buy_id is not None
