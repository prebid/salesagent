"""Integration tests for currency-specific budget limit validation.

Tests the per-currency minimum/maximum spend limits and per-product override
functionality for media buy creation.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete

from src.core.database.database_session import get_db_session
from src.core.database.models import CurrencyLimit, MediaBuy, Principal, Product, Tenant
from src.core.main import _create_media_buy_impl
from src.core.schemas import Budget, TaskStatus


@pytest.mark.integration
class TestMinimumSpendValidation:
    """Test minimum spend validation for media buys."""

    @pytest.fixture
    def setup_test_data(self, integration_db):
        """Set up test tenant with products and currency-specific limits."""
        from src.core.config_loader import set_current_tenant

        with get_db_session() as session:
            now = datetime.now(UTC)

            # Create tenant
            tenant = Tenant(
                tenant_id="test_minspend_tenant",
                name="Test Minimum Spend Tenant",
                subdomain="testminspend",
                ad_server="mock",
                enable_axe_signals=True,
                human_review_required=False,
                created_at=now,
                updated_at=now,
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

            # Create principal
            principal = Principal(
                tenant_id="test_minspend_tenant",
                principal_id="test_principal",
                name="Test Principal",
                access_token="test_minspend_token",
                platform_mappings={"mock": {"advertiser_id": "test_advertiser_id"}},
                created_at=now,
            )
            session.add(principal)

            # Create product WITHOUT override (will use currency limit)
            product_no_override = Product(
                tenant_id="test_minspend_tenant",
                product_id="prod_global",
                name="Product Using Currency Minimum",
                description="Uses currency-specific minimum",
                formats=["display_300x250"],
                targeting_template={},
                delivery_type="guaranteed",
                is_fixed_price=True,
                cpm=Decimal("10.00"),
                min_spend=None,  # No override, uses currency limit
            )
            session.add(product_no_override)

            # Create product WITH override (higher than currency limit)
            product_high_override = Product(
                tenant_id="test_minspend_tenant",
                product_id="prod_high",
                name="Product With High Override",
                description="Has $5000 minimum override",
                formats=["display_300x250"],
                targeting_template={},
                delivery_type="guaranteed",
                is_fixed_price=True,
                cpm=Decimal("10.00"),
                min_spend=Decimal("5000.00"),  # Product-specific override
            )
            session.add(product_high_override)

            # Create product WITH override (lower than currency limit)
            product_low_override = Product(
                tenant_id="test_minspend_tenant",
                product_id="prod_low",
                name="Product With Low Override",
                description="Has $500 minimum override",
                formats=["display_300x250"],
                targeting_template={},
                delivery_type="guaranteed",
                is_fixed_price=True,
                cpm=Decimal("10.00"),
                min_spend=Decimal("500.00"),  # Lower override
            )
            session.add(product_low_override)

            session.commit()

            # Set current tenant
            set_current_tenant("test_minspend_tenant")

        yield

        # Cleanup
        with get_db_session() as session:
            session.execute(delete(MediaBuy).where(MediaBuy.tenant_id == "test_minspend_tenant"))
            session.execute(delete(Product).where(Product.tenant_id == "test_minspend_tenant"))
            session.execute(delete(Principal).where(Principal.tenant_id == "test_minspend_tenant"))
            session.execute(delete(CurrencyLimit).where(CurrencyLimit.tenant_id == "test_minspend_tenant"))
            session.execute(delete(Tenant).where(Tenant.tenant_id == "test_minspend_tenant"))
            session.commit()

    def test_currency_minimum_spend_enforced(self, setup_test_data):
        """Test that currency-specific minimum spend is enforced."""
        from unittest.mock import MagicMock

        # Create mock context
        context = MagicMock()
        context.headers = {"x-adcp-auth": "test_minspend_token"}

        # Try to create media buy below USD minimum ($1000)
        start_time = datetime.now(UTC) + timedelta(days=1)
        end_time = start_time + timedelta(days=7)

        response = _create_media_buy_impl(
            promoted_offering="Test Campaign",
            product_ids=["prod_global"],
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
            total_budget=500.0,  # Below $1000 minimum
            budget=Budget(amount=500.0, currency="USD"),  # Explicit USD
            context=context,
        )

        # Should fail validation
        assert response.status == TaskStatus.FAILED
        assert "minimum spend" in response.detail.lower()
        assert "1000" in response.detail
        assert "USD" in response.detail

    def test_product_override_enforced(self, setup_test_data):
        """Test that product-specific minimum spend override is enforced."""
        from unittest.mock import MagicMock

        context = MagicMock()
        context.headers = {"x-adcp-auth": "test_minspend_token"}

        start_time = datetime.now(UTC) + timedelta(days=1)
        end_time = start_time + timedelta(days=7)

        # Try to create media buy below product override ($5000)
        response = _create_media_buy_impl(
            promoted_offering="Test Campaign",
            product_ids=["prod_high"],
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
            total_budget=3000.0,  # Below $5000 product minimum
            budget=Budget(amount=3000.0, currency="USD"),
            context=context,
        )

        # Should fail validation
        assert response.status == TaskStatus.FAILED
        assert "minimum spend" in response.detail.lower()
        assert "5000" in response.detail
        assert "USD" in response.detail

    def test_lower_override_allows_smaller_spend(self, setup_test_data):
        """Test that lower product override allows smaller spend than currency limit."""
        from unittest.mock import MagicMock

        context = MagicMock()
        context.headers = {"x-adcp-auth": "test_minspend_token"}

        start_time = datetime.now(UTC) + timedelta(days=1)
        end_time = start_time + timedelta(days=7)

        # Create media buy above product minimum ($500) but below currency limit ($1000)
        response = _create_media_buy_impl(
            promoted_offering="Test Campaign",
            product_ids=["prod_low"],
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
            total_budget=750.0,  # Above $500 product min, below $1000 currency limit
            budget=Budget(amount=750.0, currency="USD"),
            context=context,
        )

        # Should succeed because product override is lower
        assert response.status != TaskStatus.FAILED

    def test_minimum_spend_met_success(self, setup_test_data):
        """Test that media buy succeeds when minimum spend is met."""
        from unittest.mock import MagicMock

        context = MagicMock()
        context.headers = {"x-adcp-auth": "test_minspend_token"}

        start_time = datetime.now(UTC) + timedelta(days=1)
        end_time = start_time + timedelta(days=7)

        # Create media buy above minimum
        response = _create_media_buy_impl(
            promoted_offering="Test Campaign",
            product_ids=["prod_global"],
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
            total_budget=2000.0,  # Above $1000 minimum
            budget=Budget(amount=2000.0, currency="USD"),
            context=context,
        )

        # Should succeed
        assert response.status != TaskStatus.FAILED
        assert response.media_buy_id

    def test_unsupported_currency_rejected(self, setup_test_data):
        """Test that unsupported currencies are rejected."""
        from unittest.mock import MagicMock

        context = MagicMock()
        context.headers = {"x-adcp-auth": "test_minspend_token"}

        start_time = datetime.now(UTC) + timedelta(days=1)
        end_time = start_time + timedelta(days=7)

        # Try to create media buy with unsupported currency (JPY)
        response = _create_media_buy_impl(
            promoted_offering="Test Campaign",
            product_ids=["prod_global"],
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
            total_budget=100000.0,  # ¥100,000
            budget=Budget(amount=100000.0, currency="JPY"),  # Not configured
            context=context,
        )

        # Should fail with currency not supported message
        assert response.status == TaskStatus.FAILED
        assert "currency" in response.detail.lower()
        assert "not supported" in response.detail.lower()
        assert "JPY" in response.detail

    def test_different_currency_different_minimum(self, setup_test_data):
        """Test that different currencies have different minimums."""
        from unittest.mock import MagicMock

        context = MagicMock()
        context.headers = {"x-adcp-auth": "test_minspend_token"}

        start_time = datetime.now(UTC) + timedelta(days=1)
        end_time = start_time + timedelta(days=7)

        # €950 should fail (below €900 minimum... wait, that should pass)
        # Let's try €800 which should fail
        response = _create_media_buy_impl(
            promoted_offering="Test Campaign",
            product_ids=["prod_global"],
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
            total_budget=800.0,  # Below €900 minimum
            budget=Budget(amount=800.0, currency="EUR"),
            context=context,
        )

        # Should fail validation
        assert response.status == TaskStatus.FAILED
        assert "minimum spend" in response.detail.lower()
        assert "900" in response.detail
        assert "EUR" in response.detail

    def test_no_minimum_when_not_set(self, setup_test_data):
        """Test that media buys with no minimum set in currency limit are allowed."""
        from unittest.mock import MagicMock

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

        context = MagicMock()
        context.headers = {"x-adcp-auth": "test_minspend_token"}

        start_time = datetime.now(UTC) + timedelta(days=1)
        end_time = start_time + timedelta(days=7)

        # Create media buy with low budget in GBP (should succeed - no minimum)
        response = _create_media_buy_impl(
            promoted_offering="Test Campaign",
            product_ids=["prod_global"],
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
            total_budget=100.0,  # Low budget, but no minimum for GBP
            budget=Budget(amount=100.0, currency="GBP"),
            context=context,
        )

        # Should succeed
        assert response.status != TaskStatus.FAILED
        assert response.media_buy_id
