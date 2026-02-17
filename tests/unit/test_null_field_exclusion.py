"""Test that null/None fields are properly excluded from model serialization per AdCP spec.

Per AdCP V3 specification, optional fields should be omitted from responses rather than
set to null. This is especially important for pricing data where:
- Auction pricing should not include fixed_price (fixed_price is only for fixed pricing)
- Fixed pricing should not include floor_price (floor_price is only for auction)
- Price guidance should not include null percentile values (V3: floor moved to floor_price)
"""

from src.core.schemas import PriceGuidance, PricingModel, PricingOption


class TestPricingOptionNullExclusion:
    """Test that PricingOption correctly excludes null values per AdCP V3 spec."""

    def test_auction_pricing_excludes_null_fixed_price(self):
        """Auction-based pricing should NOT include fixed_price=null in serialization.

        V3 Migration: rate→fixed_price, floor moved from price_guidance to floor_price.
        """
        auction_option = PricingOption(
            pricing_option_id="cpm_usd_auction",
            pricing_model=PricingModel.cpm,
            floor_price=5.0,  # V3: was price_guidance.floor
            currency="USD",
            price_guidance=PriceGuidance(p25=None, p50=7.0, p75=None, p90=10.0),  # V3: no floor
        )

        dump = auction_option.model_dump()

        # Verify null fixed_price is excluded (V3: was rate)
        assert "fixed_price" not in dump, "fixed_price=null should be excluded from auction pricing"

        # Verify internal fields are excluded
        assert "is_fixed" not in dump, "is_fixed should be excluded per AdCP spec"
        assert "supported" not in dump, "supported is internal field, should be excluded"

        # Verify required fields are present
        assert "pricing_option_id" in dump
        assert "pricing_model" in dump
        assert "currency" in dump
        assert "floor_price" in dump  # V3: floor at pricing option level
        assert dump["floor_price"] == 5.0

    def test_fixed_pricing_includes_fixed_price_excludes_floor_price(self):
        """Fixed-rate pricing should include fixed_price and exclude floor_price.

        V3 Migration: rate→fixed_price.
        """
        fixed_option = PricingOption(
            pricing_option_id="cpm_usd_fixed",
            pricing_model=PricingModel.cpm,
            fixed_price=12.50,  # V3: was rate
            currency="USD",
            price_guidance=None,
        )

        dump = fixed_option.model_dump()

        # Verify fixed_price is included (V3: was rate)
        assert "fixed_price" in dump, "fixed_price should be included for fixed pricing"
        assert dump["fixed_price"] == 12.50, "fixed_price value should be preserved"

        # Verify null floor_price is excluded
        assert "floor_price" not in dump, "floor_price=null should be excluded"

        # Verify null price_guidance is excluded
        assert "price_guidance" not in dump, "price_guidance=null should be excluded"

        # Verify internal fields are excluded
        assert "is_fixed" not in dump, "is_fixed should be excluded per AdCP spec"

    def test_optional_fields_excluded_when_null(self):
        """Optional pricing fields should be excluded when null."""
        option = PricingOption(
            pricing_option_id="cpm_usd_fixed",
            pricing_model=PricingModel.cpm,
            fixed_price=10.0,  # V3: was rate
            currency="USD",
            price_guidance=None,
            min_spend_per_package=None,  # Should be excluded
        )

        dump = option.model_dump()

        # Verify null optional fields are excluded
        assert "min_spend_per_package" not in dump, "min_spend_per_package=null should be excluded"

    def test_optional_fields_included_when_present(self):
        """Optional pricing fields should be included when present (not null)."""
        option = PricingOption(
            pricing_option_id="cpm_usd_fixed",
            pricing_model=PricingModel.cpm,
            fixed_price=10.0,  # V3: was rate
            currency="USD",
            price_guidance=None,
            min_spend_per_package=500.0,  # Should be included
        )

        dump = option.model_dump()

        # Verify present optional field is included
        assert "min_spend_per_package" in dump, "min_spend_per_package should be included when present"
        assert dump["min_spend_per_package"] == 500.0


class TestPriceGuidanceNullExclusion:
    """Test that PriceGuidance correctly excludes null percentile values.

    V3 Migration: PriceGuidance no longer has floor - it moved to floor_price at
    pricing option level. Only percentiles (p25, p50, p75, p90) remain.
    """

    def test_null_percentiles_excluded(self):
        """Null percentile values (p25, p50, p75, p90) should be excluded from dump."""
        price_guidance = PriceGuidance(
            p25=None,  # Should be excluded
            p50=7.0,  # Should be included
            p75=None,  # Should be excluded
            p90=10.0,  # Should be included
        )

        dump = price_guidance.model_dump(exclude_none=True)

        # V3: No floor field in PriceGuidance - it's now floor_price at pricing option level

        # Verify null percentiles are excluded
        assert "p25" not in dump, "p25=null should be excluded"
        assert "p75" not in dump, "p75=null should be excluded"

        # Verify present percentiles are included
        assert "p50" in dump, "p50 should be included when present"
        assert dump["p50"] == 7.0
        assert "p90" in dump, "p90 should be included when present"
        assert dump["p90"] == 10.0

    def test_all_percentiles_null(self):
        """PriceGuidance with all percentiles null should serialize to empty dict.

        V3 Migration: Without floor, if all percentiles are null, the dump is empty.
        """
        price_guidance = PriceGuidance(
            p25=None,
            p50=None,
            p75=None,
            p90=None,
        )

        dump = price_guidance.model_dump(exclude_none=True)

        # V3: Should be empty when all percentiles are null
        assert len(dump) == 0, "Should be empty when all percentiles are null"


class TestNestedModelNullExclusion:
    """Test that null exclusion works for nested models (PricingOption contains PriceGuidance)."""

    def test_nested_price_guidance_excludes_nulls(self):
        """When PricingOption contains PriceGuidance, nulls should be excluded in nested object.

        V3 Migration: floor is now floor_price at pricing option level, not in price_guidance.
        """
        auction_option = PricingOption(
            pricing_option_id="cpm_usd_auction",
            pricing_model=PricingModel.cpm,
            floor_price=5.0,  # V3: was price_guidance.floor
            currency="USD",
            price_guidance=PriceGuidance(p25=None, p50=7.0, p75=None, p90=10.0),  # V3: no floor
        )

        dump = auction_option.model_dump()

        # Check floor_price at top level (V3 change)
        assert "floor_price" in dump
        assert dump["floor_price"] == 5.0

        # Check nested price_guidance
        assert "price_guidance" in dump
        price_guidance_dump = dump["price_guidance"]

        # Verify null percentiles are excluded from nested object
        assert "p25" not in price_guidance_dump, "p25 should be excluded from nested price_guidance"
        assert "p75" not in price_guidance_dump, "p75 should be excluded from nested price_guidance"

        # Verify present values are included
        assert "p50" in price_guidance_dump
        assert "p90" in price_guidance_dump


class TestAdCPComplianceViaExamples:
    """Test examples from actual A2A responses to verify AdCP V3 spec compliance."""

    def test_cpm_auction_response_structure(self):
        """Test that CPM auction pricing matches AdCP V3 cpm-pricing-option.json schema.

        Per AdCP V3 spec, auction pricing requires:
        - pricing_option_id (required)
        - pricing_model: "cpm" (required)
        - currency (required)
        - floor_price (required for auction)
        - price_guidance (optional, only percentiles)

        Should NOT include:
        - fixed_price (that's only for fixed pricing)
        - is_fixed (internal field)
        """
        auction_option = PricingOption(
            pricing_option_id="cpm_usd_auction",
            pricing_model=PricingModel.cpm,
            floor_price=5.0,  # V3: was price_guidance.floor
            currency="USD",
            price_guidance=None,  # Optional - can omit percentiles
        )

        dump = auction_option.model_dump()

        # Verify AdCP V3-compliant structure
        assert set(dump.keys()) == {
            "pricing_option_id",
            "pricing_model",
            "currency",
            "floor_price",  # V3: at top level
        }, "Should only have AdCP V3 spec fields for auction pricing"

        # Verify floor_price value
        assert dump["floor_price"] == 5.0

    def test_cpm_fixed_response_structure(self):
        """Test that CPM fixed pricing matches AdCP V3 cpm-pricing-option.json schema.

        Per AdCP V3 spec, fixed pricing requires:
        - pricing_option_id (required)
        - pricing_model: "cpm" (required)
        - fixed_price (required for fixed pricing)
        - currency (required)

        Should NOT include:
        - floor_price (that's only for auction pricing)
        - price_guidance (optional but excluded when null)
        - is_fixed (internal field)
        """
        fixed_option = PricingOption(
            pricing_option_id="cpm_usd_fixed",
            pricing_model=PricingModel.cpm,
            fixed_price=12.50,  # V3: was rate
            currency="USD",
            price_guidance=None,
        )

        dump = fixed_option.model_dump()

        # Verify AdCP V3-compliant structure
        assert set(dump.keys()) == {
            "pricing_option_id",
            "pricing_model",
            "fixed_price",  # V3: was rate
            "currency",
        }, "Should only have AdCP V3 spec fields for fixed pricing"
