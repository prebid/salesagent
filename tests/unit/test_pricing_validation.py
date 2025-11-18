"""Unit tests for pricing model validation (AdCP PR #88)."""

from decimal import Decimal
from unittest.mock import Mock

import pytest
from fastmcp.exceptions import ToolError

from src.core.schemas import PricingModel
from src.core.tools.media_buy_create import _validate_pricing_model_selection


class TestPricingValidation:
    """Test pricing model validation logic."""

    def test_legacy_product_without_pricing_model_in_package(self):
        """Test product with no pricing_options should raise data integrity error."""
        # Since pricing_options is now required, products without them trigger data integrity errors
        product = Mock()
        product.product_id = "legacy_product"
        product.pricing_options = []  # No pricing options = data integrity error

        # Package doesn't specify pricing_model (Mock with necessary attributes)
        package = Mock()
        package.package_id = "pkg_1"
        package.product_id = "legacy_product"
        package.budget = 5000.0
        package.pricing_option_id = None
        package.pricing_model = None
        package.bid_price = None

        # Should raise data integrity error
        with pytest.raises(ToolError) as exc_info:
            _validate_pricing_model_selection(package, product, "USD")

        assert "has no pricing_options configured" in str(exc_info.value)
        assert "data integrity error" in str(exc_info.value)

    def test_legacy_product_with_pricing_model_in_package_should_error(self):
        """Test product with no pricing_options should raise data integrity error."""
        # Since pricing_options is now required, products without them trigger data integrity errors
        product = Mock()
        product.product_id = "legacy_product"
        product.pricing_options = []  # No pricing options = data integrity error

        package = Mock()
        package.package_id = "pkg_1"
        package.product_id = "legacy_product"
        package.pricing_model = PricingModel.CPCV
        package.budget = 5000.0
        package.pricing_option_id = None
        package.bid_price = None

        with pytest.raises(ToolError) as exc_info:
            _validate_pricing_model_selection(package, product, "USD")

        assert "has no pricing_options configured" in str(exc_info.value)
        assert "data integrity error" in str(exc_info.value)

    def test_new_product_with_matching_pricing_model(self):
        """Test product with pricing_options and package specifying valid pricing_model."""
        # Setup pricing option
        pricing_option = Mock()
        pricing_option.pricing_model = "cpcv"
        pricing_option.currency = "USD"
        pricing_option.is_fixed = True
        pricing_option.rate = Decimal("0.25")
        pricing_option.min_spend_per_package = None

        product = Mock()
        product.product_id = "video_product"
        product.pricing_options = [pricing_option]

        package = Mock()
        package.package_id = "pkg_1"
        package.product_id = "video_product"
        package.budget = 10000.0
        package.pricing_option_id = None
        package.pricing_model = PricingModel.CPCV
        package.bid_price = None

        result = _validate_pricing_model_selection(package, product, "USD")

        assert result["pricing_model"] == "cpcv"
        assert result["rate"] == 0.25
        assert result["currency"] == "USD"
        assert result["is_fixed"] is True

    def test_pricing_model_not_offered_by_product(self):
        """Test package requesting pricing_model not offered by product."""
        pricing_option = Mock()
        pricing_option.pricing_model = "cpm"
        pricing_option.currency = "USD"

        product = Mock()
        product.product_id = "display_product"
        product.pricing_options = [pricing_option]

        package = Mock()
        package.package_id = "pkg_1"
        package.product_id = "display_product"
        package.budget = 5000.0
        package.pricing_option_id = None
        package.pricing_model = PricingModel.CPP
        package.bid_price = None

        with pytest.raises(ToolError) as exc_info:
            _validate_pricing_model_selection(package, product, "USD")

        assert "does not offer pricing model" in str(exc_info.value)
        assert "cpp" in str(exc_info.value).lower()

    def test_currency_mismatch(self):
        """Test package with campaign currency that doesn't match pricing option currency."""
        pricing_option = Mock()
        pricing_option.pricing_model = "cpm"
        pricing_option.currency = "USD"

        product = Mock()
        product.product_id = "product_1"
        product.pricing_options = [pricing_option]

        package = Mock()
        package.package_id = "pkg_1"
        package.product_id = "product_1"
        package.budget = 5000.0
        package.pricing_option_id = None
        package.pricing_model = PricingModel.CPM
        package.bid_price = None

        with pytest.raises(ToolError) as exc_info:
            _validate_pricing_model_selection(package, product, "EUR")

        assert "does not offer pricing model" in str(exc_info.value)
        assert "EUR" in str(exc_info.value)

    def test_auction_pricing_without_bid_price(self):
        """Test auction-based pricing without bid_price in package."""
        pricing_option = Mock()
        pricing_option.pricing_model = "cpm"
        pricing_option.currency = "USD"
        pricing_option.is_fixed = False
        pricing_option.price_guidance = {"floor": 10.0}

        product = Mock()
        product.product_id = "product_1"
        product.pricing_options = [pricing_option]

        package = Mock()
        package.package_id = "pkg_1"
        package.product_id = "product_1"
        package.budget = 5000.0
        package.pricing_option_id = None
        package.pricing_model = PricingModel.CPM
        package.bid_price = None

        with pytest.raises(ToolError) as exc_info:
            _validate_pricing_model_selection(package, product, "USD")

        # ToolError message is in the second argument
        error_str = str(exc_info.value)
        assert "bid_price" in error_str and "requires" in error_str

    def test_bid_price_below_floor(self):
        """Test bid_price below floor price."""
        pricing_option = Mock()
        pricing_option.pricing_model = "cpm"
        pricing_option.currency = "USD"
        pricing_option.is_fixed = False
        pricing_option.price_guidance = {"floor": 15.0}

        product = Mock()
        product.product_id = "product_1"
        product.pricing_options = [pricing_option]

        package = Mock()
        package.package_id = "pkg_1"
        package.product_id = "product_1"
        package.budget = 5000.0
        package.pricing_option_id = None
        package.pricing_model = PricingModel.CPM
        package.bid_price = 10.0

        with pytest.raises(ToolError) as exc_info:
            _validate_pricing_model_selection(package, product, "USD")

        assert "below floor price" in str(exc_info.value)

    def test_fixed_pricing_without_rate(self):
        """Test fixed pricing option without rate specified (invalid)."""
        pricing_option = Mock()
        pricing_option.pricing_model = "cpm"
        pricing_option.currency = "USD"
        pricing_option.is_fixed = True
        pricing_option.rate = None  # Invalid - fixed pricing needs rate

        product = Mock()
        product.product_id = "product_1"
        product.pricing_options = [pricing_option]

        package = Mock()
        package.package_id = "pkg_1"
        package.product_id = "product_1"
        package.budget = 5000.0
        package.pricing_option_id = None
        package.pricing_model = PricingModel.CPM
        package.bid_price = None

        with pytest.raises(ToolError) as exc_info:
            _validate_pricing_model_selection(package, product, "USD")

        assert "no rate specified" in str(exc_info.value)

    def test_budget_below_minimum_spend(self):
        """Test package budget below min_spend_per_package."""
        pricing_option = Mock()
        pricing_option.pricing_model = "cpcv"
        pricing_option.currency = "USD"
        pricing_option.is_fixed = True
        pricing_option.rate = Decimal("0.30")
        pricing_option.min_spend_per_package = Decimal("10000.00")

        product = Mock()
        product.product_id = "product_1"
        product.pricing_options = [pricing_option]

        package = Mock()
        package.package_id = "pkg_1"
        package.product_id = "product_1"
        package.budget = 5000.0
        package.pricing_option_id = None
        package.pricing_model = PricingModel.CPCV
        package.bid_price = None

        with pytest.raises(ToolError) as exc_info:
            _validate_pricing_model_selection(package, product, "USD")

        assert "below minimum spend" in str(exc_info.value)

    def test_valid_auction_pricing_with_bid(self):
        """Test valid auction pricing with bid_price >= floor."""
        pricing_option = Mock()
        pricing_option.pricing_model = "cpm"
        pricing_option.currency = "USD"
        pricing_option.is_fixed = False
        pricing_option.rate = None
        pricing_option.price_guidance = {"floor": 10.0, "p50": 15.0}
        pricing_option.min_spend_per_package = None

        product = Mock()
        product.product_id = "product_1"
        product.pricing_options = [pricing_option]

        package = Mock()
        package.package_id = "pkg_1"
        package.product_id = "product_1"
        package.budget = 5000.0
        package.pricing_option_id = None
        package.pricing_model = PricingModel.CPM
        package.bid_price = 18.0

        result = _validate_pricing_model_selection(package, product, "USD")

        assert result["pricing_model"] == "cpm"
        assert result["is_fixed"] is False
        assert result["bid_price"] == 18.0

    def test_product_with_no_pricing_information(self):
        """Test product with no pricing_options should raise data integrity error."""
        # Since pricing_options is now required, products without them trigger data integrity errors
        product = Mock()
        product.product_id = "broken_product"
        product.pricing_options = []  # No pricing options = data integrity error

        package = Mock()
        package.package_id = "pkg_1"
        package.product_id = "broken_product"
        package.budget = 5000.0
        package.pricing_option_id = None
        package.pricing_model = None
        package.bid_price = None
