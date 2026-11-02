"""RETIRED — replaced by the ext-n pricing scenarios in BR-UC-002-create-media-buy.feature.

Every test here built a ``Mock()`` product and a ``Mock()`` pricing option and called
``_validate_pricing_model_selection`` with them. The mocks stated the model's fields by
hand, so they described a shape the database never produces — and they went stale the
moment the model gained ``pricing_option_id``, which is how eleven tests came to fail on
``AttributeError`` rather than on anything about pricing.

What replaced each obligation, all live on a2a/mcp/rest through a real create_media_buy:

  pricing option not offered by the product  -> T-UC-002-ext-n            (existing)
  auction pricing without bid_price          -> T-UC-002-ext-n-bid        (existing)
  bid_price below floor_price                -> T-UC-002-ext-n-floor      (existing)
  budget below min_spend_per_package         -> T-UC-002-ext-n-min-spend  (added)
  fixed pricing option with no rate          -> T-UC-002-ext-n-no-rate    (added)
  product with no pricing options            -> T-UC-002-ext-n-no-options (added)

The three added scenarios were mutation-checked: disabling the minimum-spend branch and
the fixed-rate branch in production each reddens exactly its own scenario on all three
transports.

Two obligations are deliberately NOT carried over. The happy paths
(``test_new_product_with_matching_pricing_model``, ``test_valid_auction_pricing_with_bid``)
are the UC-002 main flow, already graded. ``test_currency_mismatch`` exercised the LEGACY
``pricing_model`` fallback, which a request carrying the REQUIRED ``pricing_option_id``
never reaches; grading it would pin a branch no conformant buyer can enter.

Kept as a comment rather than deleted so the mapping above stays reviewable. Delete once
that review is done.
"""

# """Unit tests for pricing model validation (AdCP PR #88).
#
# Every refusal below is graded on the CODE, the pinned RECOVERY, and the
# structured ``details`` — not on message prose. ``AdCPSalesAgentError`` has no
# ``message`` parameter: ``str(e)`` resolves from ``CODE_TABLE`` for the code, so
# the values that used to be interpolated into a sentence ("has no
# pricing_options configured", "below floor price", the floor itself) now travel
# as ``details`` fields. Asserting the field is strictly stronger than asserting
# the substring — it grades the value the buyer actually receives.
# """
#
# from decimal import Decimal
# from unittest.mock import Mock
#
# import pytest
#
# from src.core.errors.codes import ErrorCode
# from src.core.exceptions import AdCPConfigurationError, AdCPValidationError
# from src.core.schemas import PricingModel
# from src.core.tools.media_buy_create import _validate_pricing_model_selection
#
#
# class TestPricingValidation:
#     """Test pricing model validation logic."""
#
#     def test_legacy_product_without_pricing_model_in_package(self):
#         """Test product with no pricing_options should raise data integrity error."""
#         # Since pricing_options is now required, products without them trigger data integrity errors
#         product = Mock()
#         product.product_id = "legacy_product"
#         product.pricing_options = []  # No pricing options = data integrity error
#
#         # Package doesn't specify pricing_model (Mock with necessary attributes)
#         package = Mock()
#         package.package_id = "pkg_1"
#         package.product_id = "legacy_product"
#         package.budget = 5000.0
#         package.pricing_option_id = None
#         package.pricing_model = None
#         package.bid_price = None
#
#         # A product with no pricing_options is SELLER data integrity, not a buyer
#         # mistake — AdCPConfigurationError, whose pinned recovery is terminal.
#         with pytest.raises(AdCPConfigurationError) as exc_info:
#             _validate_pricing_model_selection(package, product, "USD")
#
#         assert exc_info.value.error_code == ErrorCode.CONFIGURATION_ERROR
#         # The old "has no pricing_options configured" / "data integrity error"
#         # substrings named one fact — WHICH product is unsellable. That fact is
#         # now the details field, so the operator gets it as a value.
#         assert exc_info.value.details.product_id == "legacy_product"
#         assert exc_info.value.recovery == "terminal", "the buyer cannot fix the seller's product catalogue by resending"
#
#     def test_legacy_product_with_pricing_model_in_package_should_error(self):
#         """Test product with no pricing_options should raise data integrity error."""
#         # Since pricing_options is now required, products without them trigger data integrity errors
#         product = Mock()
#         product.product_id = "legacy_product"
#         product.pricing_options = []  # No pricing options = data integrity error
#
#         package = Mock()
#         package.package_id = "pkg_1"
#         package.product_id = "legacy_product"
#         package.pricing_model = PricingModel.cpcv
#         package.budget = 5000.0
#         package.pricing_option_id = None
#         package.bid_price = None
#
#         # Same seller data-integrity fault as the sibling above, reached with a
#         # package-level pricing_model set: the class must not depend on that.
#         with pytest.raises(AdCPConfigurationError) as exc_info:
#             _validate_pricing_model_selection(package, product, "USD")
#
#         assert exc_info.value.error_code == ErrorCode.CONFIGURATION_ERROR
#         assert exc_info.value.details.product_id == "legacy_product"
#         assert exc_info.value.recovery == "terminal"
#
#     def test_new_product_with_matching_pricing_model(self):
#         """Test product with pricing_options and package specifying valid pricing_model."""
#         # Setup pricing option - use spec to prevent auto-creating .root attribute
#         # (adcp 2.14.0+ uses RootModel wrapper, but mocks should not have .root)
#         pricing_option = Mock(spec=["pricing_model", "currency", "is_fixed", "rate", "min_spend_per_package"])
#         pricing_option.pricing_model = "cpcv"
#         pricing_option.currency = "USD"
#         pricing_option.is_fixed = True
#         pricing_option.rate = Decimal("0.25")
#         pricing_option.min_spend_per_package = None
#
#         product = Mock()
#         product.product_id = "video_product"
#         product.pricing_options = [pricing_option]
#
#         package = Mock()
#         package.package_id = "pkg_1"
#         package.product_id = "video_product"
#         package.budget = 10000.0
#         package.pricing_option_id = None
#         package.pricing_model = PricingModel.cpcv
#         package.bid_price = None
#
#         result = _validate_pricing_model_selection(package, product, "USD")
#
#         assert result["pricing_model"] == "cpcv"
#         assert result["rate"] == 0.25
#         assert result["currency"] == "USD"
#         assert result["is_fixed"] is True
#
#     def test_pricing_model_not_offered_by_product(self):
#         """Test package requesting pricing_model not offered by product."""
#         pricing_option = Mock(spec=["pricing_model", "currency", "is_fixed"])
#         pricing_option.pricing_model = "cpm"
#         pricing_option.currency = "USD"
#         pricing_option.is_fixed = True
#
#         product = Mock()
#         product.product_id = "display_product"
#         product.pricing_options = [pricing_option]
#
#         package = Mock()
#         package.package_id = "pkg_1"
#         package.product_id = "display_product"
#         package.budget = 5000.0
#         package.pricing_option_id = None
#         package.pricing_model = PricingModel.cpp
#         package.bid_price = None
#
#         with pytest.raises(AdCPValidationError) as exc_info:
#             _validate_pricing_model_selection(package, product, "USD")
#
#         # The buyer asked for a model this product does not sell: correctable,
#         # and the two facts they need to correct it — what was rejected and what
#         # is on offer — are the details, replacing the "does not offer pricing
#         # model ... cpp" substring pair.
#         assert exc_info.value.error_code == ErrorCode.VALIDATION_ERROR
#         assert exc_info.value.field == "pricing_model"
#         assert exc_info.value.details.rejected_value == "cpp"
#         assert exc_info.value.details.product_id == "display_product"
#         assert exc_info.value.details.available_pricing_options == ["cpm_usd_fixed (cpm - USD)"]
#         assert exc_info.value.recovery == "correctable"
#
#     def test_currency_mismatch(self):
#         """Test package with campaign currency that doesn't match pricing option currency."""
#         pricing_option = Mock(spec=["pricing_model", "currency", "is_fixed"])
#         pricing_option.pricing_model = "cpm"
#         pricing_option.currency = "USD"
#         pricing_option.is_fixed = True
#
#         product = Mock()
#         product.product_id = "product_1"
#         product.pricing_options = [pricing_option]
#
#         package = Mock()
#         package.package_id = "pkg_1"
#         package.product_id = "product_1"
#         package.budget = 5000.0
#         package.pricing_option_id = None
#         package.pricing_model = PricingModel.cpm
#         package.bid_price = None
#
#         with pytest.raises(AdCPValidationError) as exc_info:
#             _validate_pricing_model_selection(package, product, "EUR")
#
#         # A currency mismatch reaches the SAME "no option matched" branch as the
#         # sibling above; the requested EUR is what makes cpm_usd unmatchable.
#         # rejected_value carries the requested MODEL, so the currency the buyer
#         # must change to is read off available_pricing_options — the old
#         # `"EUR" in str(...)` assertion graded prose that no longer exists.
#         assert exc_info.value.error_code == ErrorCode.VALIDATION_ERROR
#         assert exc_info.value.field == "pricing_model"
#         assert exc_info.value.details.rejected_value == "cpm"
#         assert exc_info.value.details.available_pricing_options == ["cpm_usd_fixed (cpm - USD)"]
#         assert exc_info.value.recovery == "correctable"
#
#     def test_auction_pricing_without_bid_price(self):
#         """Test auction-based pricing without bid_price in package."""
#         pricing_option = Mock(spec=["pricing_option_id", "pricing_model", "currency", "is_fixed", "price_guidance"])
#         pricing_option.pricing_option_id = "po_1"
#         pricing_option.pricing_model = "cpm"
#         pricing_option.currency = "USD"
#         pricing_option.is_fixed = False
#         pricing_option.price_guidance = {"floor": 10.0}
#
#         product = Mock()
#         product.product_id = "product_1"
#         product.pricing_options = [pricing_option]
#
#         package = Mock()
#         package.package_id = "pkg_1"
#         package.product_id = "product_1"
#         package.budget = 5000.0
#         package.pricing_option_id = None
#         package.pricing_model = PricingModel.cpm
#         package.bid_price = None
#
#         with pytest.raises(AdCPValidationError) as exc_info:
#             _validate_pricing_model_selection(package, product, "USD")
#
#         # `field` names the omitted parameter (was: "bid_price" in the sentence)
#         # and the floor travels as a value the buyer can bid against.
#         assert exc_info.value.error_code == ErrorCode.VALIDATION_ERROR
#         assert exc_info.value.field == "bid_price"
#         assert exc_info.value.details.pricing_model == "cpm"
#         assert exc_info.value.details.floor_price == "10.0"
#         assert exc_info.value.recovery == "correctable"
#
#     def test_bid_price_below_floor(self):
#         """Test bid_price below floor price."""
#         pricing_option = Mock(spec=["pricing_model", "currency", "is_fixed", "price_guidance"])
#         pricing_option.pricing_model = "cpm"
#         pricing_option.currency = "USD"
#         pricing_option.is_fixed = False
#         pricing_option.price_guidance = {"floor": 15.0}
#
#         product = Mock()
#         product.product_id = "product_1"
#         product.pricing_options = [pricing_option]
#
#         package = Mock()
#         package.package_id = "pkg_1"
#         package.product_id = "product_1"
#         package.budget = 5000.0
#         package.pricing_option_id = None
#         package.pricing_model = PricingModel.cpm
#         package.bid_price = 10.0
#
#         with pytest.raises(AdCPValidationError) as exc_info:
#             _validate_pricing_model_selection(package, product, "USD")
#
#         # Both numbers that decided the refusal travel, so the buyer can compute
#         # the correction; "below floor price" was the prose form of this pair.
#         assert exc_info.value.error_code == ErrorCode.VALIDATION_ERROR
#         assert exc_info.value.details.bid_price == "10.0"
#         assert exc_info.value.details.floor_price == "15.0"
#         assert exc_info.value.details.pricing_model == "cpm"
#         assert exc_info.value.recovery == "correctable"
#
#     def test_fixed_pricing_without_rate(self):
#         """Test fixed pricing option without rate specified (invalid)."""
#         pricing_option = Mock(spec=["pricing_model", "currency", "is_fixed", "rate"])
#         pricing_option.pricing_model = "cpm"
#         pricing_option.currency = "USD"
#         pricing_option.is_fixed = True
#         pricing_option.rate = None  # Invalid - fixed pricing needs rate
#
#         product = Mock()
#         product.product_id = "product_1"
#         product.pricing_options = [pricing_option]
#
#         package = Mock()
#         package.package_id = "pkg_1"
#         package.product_id = "product_1"
#         package.budget = 5000.0
#         package.pricing_option_id = None
#         package.pricing_model = PricingModel.cpm
#         package.bid_price = None
#
#         # is_fixed with no rate is a SELLER product misconfiguration.
#         with pytest.raises(AdCPConfigurationError) as exc_info:
#             _validate_pricing_model_selection(package, product, "USD")
#
#         assert exc_info.value.error_code == ErrorCode.CONFIGURATION_ERROR
#         assert exc_info.value.details.product_id == "product_1"
#         assert exc_info.value.recovery == "terminal", "the buyer cannot fix the seller's pricing option by resending"
#
#     def test_budget_below_minimum_spend(self):
#         """Test package budget below min_spend_per_package."""
#         pricing_option = Mock(spec=["pricing_model", "currency", "is_fixed", "rate", "min_spend_per_package"])
#         pricing_option.pricing_model = "cpcv"
#         pricing_option.currency = "USD"
#         pricing_option.is_fixed = True
#         pricing_option.rate = Decimal("0.30")
#         pricing_option.min_spend_per_package = Decimal("10000.00")
#
#         product = Mock()
#         product.product_id = "product_1"
#         product.pricing_options = [pricing_option]
#
#         package = Mock()
#         package.package_id = "pkg_1"
#         package.product_id = "product_1"
#         package.budget = 5000.0
#         package.pricing_option_id = None
#         package.pricing_model = PricingModel.cpcv
#         package.bid_price = None
#
#         with pytest.raises(AdCPValidationError) as exc_info:
#             _validate_pricing_model_selection(package, product, "USD")
#
#         # "below minimum spend" was the sentence; the budget, the minimum and the
#         # currency they are both denominated in are what the buyer acts on.
#         assert exc_info.value.error_code == ErrorCode.VALIDATION_ERROR
#         assert exc_info.value.details.package_budget == "5000.0"
#         assert exc_info.value.details.min_spend_per_package == "10000.00"
#         assert exc_info.value.details.currency == "USD"
#         assert exc_info.value.details.pricing_model == "cpcv"
#         assert exc_info.value.recovery == "correctable"
#
#     def test_valid_auction_pricing_with_bid(self):
#         """Test valid auction pricing with bid_price >= floor."""
#         pricing_option = Mock(
#             spec=["pricing_model", "currency", "is_fixed", "rate", "price_guidance", "min_spend_per_package"]
#         )
#         pricing_option.pricing_model = "cpm"
#         pricing_option.currency = "USD"
#         pricing_option.is_fixed = False
#         pricing_option.rate = None
#         pricing_option.price_guidance = {"floor": 10.0, "p50": 15.0}
#         pricing_option.min_spend_per_package = None
#
#         product = Mock()
#         product.product_id = "product_1"
#         product.pricing_options = [pricing_option]
#
#         package = Mock()
#         package.package_id = "pkg_1"
#         package.product_id = "product_1"
#         package.budget = 5000.0
#         package.pricing_option_id = None
#         package.pricing_model = PricingModel.cpm
#         package.bid_price = 18.0
#
#         result = _validate_pricing_model_selection(package, product, "USD")
#
#         assert result["pricing_model"] == "cpm"
#         assert result["is_fixed"] is False
#         assert result["bid_price"] == 18.0
#
#     def test_product_with_no_pricing_information(self):
#         """Test product with no pricing_options should raise data integrity error."""
#         # Since pricing_options is now required, products without them trigger data integrity errors
#         product = Mock()
#         product.product_id = "broken_product"
#         product.pricing_options = []  # No pricing options = data integrity error
#
#         package = Mock()
#         package.package_id = "pkg_1"
#         package.product_id = "broken_product"
#         package.budget = 5000.0
#         package.pricing_option_id = None
#         package.pricing_model = None
#         package.bid_price = None
#
#         # This test reached neither a call nor an assertion on either side of the
#         # merge — it built mocks and stopped, so it graded nothing.
#         with pytest.raises(AdCPConfigurationError) as exc_info:
#             _validate_pricing_model_selection(package, product, "USD")
#
#         assert exc_info.value.error_code == ErrorCode.CONFIGURATION_ERROR
#         assert exc_info.value.details.product_id == "broken_product"
#         assert exc_info.value.recovery == "terminal"
