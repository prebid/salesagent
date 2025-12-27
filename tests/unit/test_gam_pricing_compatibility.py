"""Unit tests for GAM pricing compatibility logic."""

import pytest

from src.adapters.gam.pricing_compatibility import PricingCompatibility


class TestCompatibilityMatrix:
    """Test the GAM compatibility matrix accuracy."""

    def test_cpm_compatible_with_all_types(self):
        """CPM should work with all line item types."""
        for line_item_type in ["STANDARD", "SPONSORSHIP", "NETWORK", "PRICE_PRIORITY", "BULK", "HOUSE"]:
            assert PricingCompatibility.is_compatible(
                line_item_type, "cpm"
            ), f"CPM should be compatible with {line_item_type}"

    def test_vcpm_compatible_with_standard_only(self):
        """VCPM should only work with STANDARD line items."""
        assert PricingCompatibility.is_compatible("STANDARD", "vcpm")

        # VCPM NOT compatible with other types
        for line_item_type in ["SPONSORSHIP", "NETWORK", "PRICE_PRIORITY", "BULK", "HOUSE"]:
            assert not PricingCompatibility.is_compatible(
                line_item_type, "vcpm"
            ), f"VCPM should NOT be compatible with {line_item_type}"

    def test_cpc_compatible_types(self):
        """CPC should work with STANDARD, SPONSORSHIP, NETWORK, PRICE_PRIORITY."""
        compatible = {"STANDARD", "SPONSORSHIP", "NETWORK", "PRICE_PRIORITY"}
        incompatible = {"BULK", "HOUSE"}

        for line_item_type in compatible:
            assert PricingCompatibility.is_compatible(
                line_item_type, "cpc"
            ), f"CPC should be compatible with {line_item_type}"

        for line_item_type in incompatible:
            assert not PricingCompatibility.is_compatible(
                line_item_type, "cpc"
            ), f"CPC should NOT be compatible with {line_item_type}"

    def test_flat_rate_compatible_types(self):
        """FLAT_RATE (→CPD) should work with SPONSORSHIP and NETWORK only."""
        compatible = {"SPONSORSHIP", "NETWORK"}
        incompatible = {"STANDARD", "PRICE_PRIORITY", "BULK", "HOUSE"}

        for line_item_type in compatible:
            assert PricingCompatibility.is_compatible(
                line_item_type, "flat_rate"
            ), f"FLAT_RATE should be compatible with {line_item_type}"

        for line_item_type in incompatible:
            assert not PricingCompatibility.is_compatible(
                line_item_type, "flat_rate"
            ), f"FLAT_RATE should NOT be compatible with {line_item_type}"


class TestLineItemTypeSelection:
    """Test automatic line item type selection logic."""

    def test_flat_rate_selects_sponsorship(self):
        """FLAT_RATE pricing should select SPONSORSHIP type."""
        result = PricingCompatibility.select_line_item_type("flat_rate", is_guaranteed=False)
        assert result == "SPONSORSHIP"

    def test_vcpm_selects_standard(self):
        """VCPM pricing should select STANDARD type."""
        result = PricingCompatibility.select_line_item_type("vcpm", is_guaranteed=False)
        assert result == "STANDARD"

    def test_guaranteed_cpm_selects_standard(self):
        """Guaranteed CPM campaigns should select STANDARD type."""
        result = PricingCompatibility.select_line_item_type("cpm", is_guaranteed=True)
        assert result == "STANDARD"

    def test_non_guaranteed_cpm_selects_price_priority(self):
        """Non-guaranteed CPM campaigns should select PRICE_PRIORITY type."""
        result = PricingCompatibility.select_line_item_type("cpm", is_guaranteed=False)
        assert result == "PRICE_PRIORITY"

    def test_cpc_non_guaranteed_selects_price_priority(self):
        """Non-guaranteed CPC campaigns should select PRICE_PRIORITY type."""
        result = PricingCompatibility.select_line_item_type("cpc", is_guaranteed=False)
        assert result == "PRICE_PRIORITY"

    def test_cpc_guaranteed_selects_standard(self):
        """Guaranteed CPC campaigns should select STANDARD type."""
        result = PricingCompatibility.select_line_item_type("cpc", is_guaranteed=True)
        assert result == "STANDARD"

    def test_override_compatible_type_accepted(self):
        """Override with compatible type should be accepted."""
        result = PricingCompatibility.select_line_item_type("cpc", is_guaranteed=False, override_type="NETWORK")
        assert result == "NETWORK"

    def test_override_incompatible_type_rejected(self):
        """Override with incompatible type should raise ValueError."""
        with pytest.raises(ValueError, match="not compatible with pricing model 'flat_rate'"):
            PricingCompatibility.select_line_item_type(
                "flat_rate",
                is_guaranteed=False,
                override_type="STANDARD",  # STANDARD doesn't support CPD (used by FLAT_RATE)
            )

    def test_override_vcpm_with_incompatible_rejected(self):
        """Override VCPM with non-STANDARD type should be rejected."""
        with pytest.raises(ValueError, match="not compatible with pricing model 'vcpm'"):
            PricingCompatibility.select_line_item_type(
                "vcpm",
                is_guaranteed=False,
                override_type="SPONSORSHIP",  # SPONSORSHIP doesn't support VCPM
            )


class TestGAMCostTypeMapping:
    """Test AdCP to GAM cost type conversion."""

    def test_supported_pricing_models(self):
        """Test conversion of supported pricing models."""
        assert PricingCompatibility.get_gam_cost_type("cpm") == "CPM"
        assert PricingCompatibility.get_gam_cost_type("vcpm") == "VCPM"
        assert PricingCompatibility.get_gam_cost_type("cpc") == "CPC"
        assert PricingCompatibility.get_gam_cost_type("flat_rate") == "CPD"

    def test_unsupported_pricing_models(self):
        """Test rejection of unsupported pricing models."""
        for unsupported in ["cpcv", "cpv", "cpp", "invalid"]:
            with pytest.raises(ValueError, match="not supported by GAM adapter"):
                PricingCompatibility.get_gam_cost_type(unsupported)


class TestDefaultPriorities:
    """Test default priority assignment for line item types."""

    def test_default_priorities_match_gam_best_practices(self):
        """Test priority values match GAM conventions."""
        assert PricingCompatibility.get_default_priority("SPONSORSHIP") == 4
        assert PricingCompatibility.get_default_priority("STANDARD") == 8
        assert PricingCompatibility.get_default_priority("PRICE_PRIORITY") == 12
        assert PricingCompatibility.get_default_priority("BULK") == 12
        assert PricingCompatibility.get_default_priority("NETWORK") == 16
        assert PricingCompatibility.get_default_priority("HOUSE") == 16

    def test_unknown_type_defaults_to_standard_priority(self):
        """Unknown line item types should default to STANDARD priority (8)."""
        assert PricingCompatibility.get_default_priority("UNKNOWN_TYPE") == 8


class TestCompatibleLineItemTypes:
    """Test getting all compatible line item types for a pricing model."""

    def test_get_compatible_types_for_cpm(self):
        """CPM should be compatible with all types."""
        compatible = PricingCompatibility.get_compatible_line_item_types("cpm")
        assert compatible == {"STANDARD", "SPONSORSHIP", "NETWORK", "PRICE_PRIORITY", "BULK", "HOUSE"}

    def test_get_compatible_types_for_vcpm(self):
        """VCPM should only be compatible with STANDARD."""
        compatible = PricingCompatibility.get_compatible_line_item_types("vcpm")
        assert compatible == {"STANDARD"}

    def test_get_compatible_types_for_cpc(self):
        """CPC should be compatible with 4 types."""
        compatible = PricingCompatibility.get_compatible_line_item_types("cpc")
        assert compatible == {"STANDARD", "SPONSORSHIP", "NETWORK", "PRICE_PRIORITY"}

    def test_get_compatible_types_for_flat_rate(self):
        """FLAT_RATE should be compatible with SPONSORSHIP and NETWORK."""
        compatible = PricingCompatibility.get_compatible_line_item_types("flat_rate")
        assert compatible == {"SPONSORSHIP", "NETWORK"}

    def test_get_compatible_types_for_unsupported_model(self):
        """Unsupported pricing models should return empty set."""
        compatible = PricingCompatibility.get_compatible_line_item_types("cpcv")
        assert compatible == set()
