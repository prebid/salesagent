"""Test that null/None fields are properly excluded from model serialization per AdCP spec.

Per AdCP specification, optional fields should be omitted from responses rather than
set to null. This is especially important for pricing data where:
- Auction pricing should not include rate=null (rate is only for fixed pricing)
- Fixed pricing should not include price_guidance=null (price_guidance is only for auction)
- Price guidance should not include null percentile values (only floor is required)
"""

from src.core.schemas import PriceGuidance, PricingModel, PricingOption


class TestPricingOptionNullExclusion:
    """Test that PricingOption correctly excludes null values per AdCP spec."""

    def test_auction_pricing_excludes_null_rate(self):
        """Auction-based pricing should NOT include rate=null in serialization."""
        auction_option = PricingOption(
            pricing_option_id="cpm_usd_auction",
            pricing_model=PricingModel.CPM,
            rate=None,  # Should be excluded from dump
            currency="USD",
            is_fixed=False,
            price_guidance=PriceGuidance(floor=5.0, p25=None, p50=7.0, p75=None, p90=10.0),
        )

        dump = auction_option.model_dump()

        # Verify null rate is excluded
        assert "rate" not in dump, "rate=null should be excluded from auction pricing"

        # Verify internal fields are excluded
        assert "is_fixed" not in dump, "is_fixed should be excluded per AdCP spec"
        assert "supported" not in dump, "supported is internal field, should be excluded"

        # Verify required fields are present
        assert "pricing_option_id" in dump
        assert "pricing_model" in dump
        assert "currency" in dump
        assert "price_guidance" in dump

    def test_fixed_pricing_includes_rate_excludes_price_guidance(self):
        """Fixed-rate pricing should include rate and exclude price_guidance=null."""
        fixed_option = PricingOption(
            pricing_option_id="cpm_usd_fixed",
            pricing_model=PricingModel.CPM,
            rate=12.50,
            currency="USD",
            is_fixed=True,
            price_guidance=None,  # Should be excluded from dump
        )

        dump = fixed_option.model_dump()

        # Verify rate is included (not null)
        assert "rate" in dump, "rate should be included for fixed pricing"
        assert dump["rate"] == 12.50, "rate value should be preserved"

        # Verify null price_guidance is excluded
        assert "price_guidance" not in dump, "price_guidance=null should be excluded"

        # Verify internal fields are excluded
        assert "is_fixed" not in dump, "is_fixed should be excluded per AdCP spec"

    def test_optional_fields_excluded_when_null(self):
        """Optional pricing fields should be excluded when null."""
        option = PricingOption(
            pricing_option_id="cpm_usd_fixed",
            pricing_model=PricingModel.CPM,
            rate=10.0,
            currency="USD",
            is_fixed=True,
            price_guidance=None,
            parameters=None,  # Should be excluded
            min_spend_per_package=None,  # Should be excluded
        )

        dump = option.model_dump()

        # Verify null optional fields are excluded
        assert "parameters" not in dump, "parameters=null should be excluded"
        assert "min_spend_per_package" not in dump, "min_spend_per_package=null should be excluded"

    def test_optional_fields_included_when_present(self):
        """Optional pricing fields should be included when present (not null)."""
        option = PricingOption(
            pricing_option_id="cpm_usd_fixed",
            pricing_model=PricingModel.CPM,
            rate=10.0,
            currency="USD",
            is_fixed=True,
            price_guidance=None,
            min_spend_per_package=500.0,  # Should be included
        )

        dump = option.model_dump()

        # Verify present optional field is included
        assert "min_spend_per_package" in dump, "min_spend_per_package should be included when present"
        assert dump["min_spend_per_package"] == 500.0


class TestPriceGuidanceNullExclusion:
    """Test that PriceGuidance correctly excludes null percentile values."""

    def test_null_percentiles_excluded(self):
        """Null percentile values (p25, p50, p75, p90) should be excluded from dump."""
        price_guidance = PriceGuidance(
            floor=5.0,  # Required, should be included
            p25=None,  # Should be excluded
            p50=7.0,  # Should be included
            p75=None,  # Should be excluded
            p90=10.0,  # Should be included
        )

        dump = price_guidance.model_dump()

        # Verify floor is always present (required)
        assert "floor" in dump, "floor is required"
        assert dump["floor"] == 5.0

        # Verify null percentiles are excluded
        assert "p25" not in dump, "p25=null should be excluded"
        assert "p75" not in dump, "p75=null should be excluded"

        # Verify present percentiles are included
        assert "p50" in dump, "p50 should be included when present"
        assert dump["p50"] == 7.0
        assert "p90" in dump, "p90 should be included when present"
        assert dump["p90"] == 10.0

    def test_only_floor_required(self):
        """PriceGuidance with only floor (all percentiles null) should serialize correctly."""
        price_guidance = PriceGuidance(
            floor=3.0,
            p25=None,
            p50=None,
            p75=None,
            p90=None,
        )

        dump = price_guidance.model_dump()

        # Should only have floor
        assert len(dump) == 1, "Should only contain floor when all percentiles are null"
        assert dump == {"floor": 3.0}


class TestNestedModelNullExclusion:
    """Test that null exclusion works for nested models (PricingOption contains PriceGuidance)."""

    def test_nested_price_guidance_excludes_nulls(self):
        """When PricingOption contains PriceGuidance, nulls should be excluded in nested object."""
        auction_option = PricingOption(
            pricing_option_id="cpm_usd_auction",
            pricing_model=PricingModel.CPM,
            rate=None,
            currency="USD",
            is_fixed=False,
            price_guidance=PriceGuidance(floor=5.0, p25=None, p50=7.0, p75=None, p90=10.0),
        )

        dump = auction_option.model_dump()

        # Check nested price_guidance
        assert "price_guidance" in dump
        price_guidance_dump = dump["price_guidance"]

        # Verify null percentiles are excluded from nested object
        assert "p25" not in price_guidance_dump, "p25 should be excluded from nested price_guidance"
        assert "p75" not in price_guidance_dump, "p75 should be excluded from nested price_guidance"

        # Verify present values are included
        assert "floor" in price_guidance_dump
        assert "p50" in price_guidance_dump
        assert "p90" in price_guidance_dump


class TestAdCPComplianceViaExamples:
    """Test examples from actual A2A responses to verify AdCP spec compliance."""

    def test_cpm_auction_response_structure(self):
        """Test that CPM auction pricing matches AdCP cpm-auction-option.json schema.

        Per AdCP spec, cpm-auction-option.json requires:
        - pricing_option_id (required)
        - pricing_model: "cpm" (required)
        - currency (required)
        - price_guidance (required, with only floor required inside)

        Should NOT include:
        - rate (that's only for fixed pricing)
        - is_fixed (internal field)
        """
        auction_option = PricingOption(
            pricing_option_id="cpm_usd_auction",
            pricing_model=PricingModel.CPM,
            rate=None,
            currency="USD",
            is_fixed=False,
            price_guidance=PriceGuidance(floor=5.0, p25=None, p50=None, p75=None, p90=None),
        )

        dump = auction_option.model_dump()

        # Verify AdCP-compliant structure
        assert set(dump.keys()) == {
            "pricing_option_id",
            "pricing_model",
            "currency",
            "price_guidance",
        }, "Should only have AdCP spec fields for auction pricing"

        # Verify price_guidance structure
        assert dump["price_guidance"] == {
            "floor": 5.0
        }, "price_guidance should only have floor when percentiles are null"

    def test_cpm_fixed_response_structure(self):
        """Test that CPM fixed pricing matches AdCP cpm-fixed-option.json schema.

        Per AdCP spec, cpm-fixed-option.json requires:
        - pricing_option_id (required)
        - pricing_model: "cpm" (required)
        - rate (required)
        - currency (required)

        Should NOT include:
        - price_guidance (that's only for auction pricing)
        - is_fixed (internal field)
        """
        fixed_option = PricingOption(
            pricing_option_id="cpm_usd_fixed",
            pricing_model=PricingModel.CPM,
            rate=12.50,
            currency="USD",
            is_fixed=True,
            price_guidance=None,
        )

        dump = fixed_option.model_dump()

        # Verify AdCP-compliant structure
        assert set(dump.keys()) == {
            "pricing_option_id",
            "pricing_model",
            "rate",
            "currency",
        }, "Should only have AdCP spec fields for fixed pricing"
