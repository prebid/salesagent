"""Unit tests for non-CPM pricing model conversion paths.

Tests convert_pricing_option_to_adcp() for VCPM, CPC, CPCV, CPV, CPP, and
flat_rate pricing models. Each model tests fixed conversion, auction conversion
(where applicable), and error cases for missing required fields.
"""

import pytest
from adcp import (
    CpcPricingOption,
    CpcvPricingOption,
    CpmPricingOption,
    CppPricingOption,
    CpvPricingOption,
    FlatRatePricingOption,
    VcpmPricingOption,
)

from src.core.product_conversion import convert_pricing_option_to_adcp


def _make_pricing_option(
    pricing_model: str,
    is_fixed: bool,
    currency: str = "USD",
    rate: float | None = None,
    price_guidance: dict | None = None,
    parameters: dict | None = None,
    min_spend_per_package: float | None = None,
) -> dict:
    """Build a pricing option dict suitable for convert_pricing_option_to_adcp."""
    po: dict = {
        "pricing_model": pricing_model,
        "is_fixed": is_fixed,
        "currency": currency,
    }
    if rate is not None:
        po["rate"] = rate
    if price_guidance is not None:
        po["price_guidance"] = price_guidance
    if parameters is not None:
        po["parameters"] = parameters
    if min_spend_per_package is not None:
        po["min_spend_per_package"] = min_spend_per_package
    return po


# ---------------------------------------------------------------------------
# VCPM
# ---------------------------------------------------------------------------
class TestVcpmConversion:
    """VCPM pricing model conversion (lines 150-171)."""

    def test_vcpm_fixed_conversion(self):
        po = _make_pricing_option("vcpm", is_fixed=True, rate=8.50)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, VcpmPricingOption)
        assert result.pricing_model == "vcpm"
        assert result.fixed_price == 8.50
        assert result.currency == "USD"
        assert result.pricing_option_id == "vcpm_usd_fixed"

    def test_vcpm_auction_with_floor_price(self):
        guidance = {"floor": 3.00, "p25": 4.0, "p50": 5.0}
        po = _make_pricing_option("vcpm", is_fixed=False, price_guidance=guidance)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, VcpmPricingOption)
        assert result.pricing_model == "vcpm"
        assert result.floor_price == 3.00
        assert result.price_guidance is not None
        assert result.price_guidance.p25 == 4.0
        assert result.price_guidance.p50 == 5.0
        assert result.pricing_option_id == "vcpm_usd_auction"

    def test_vcpm_auction_without_floor_price(self):
        guidance = {"p25": 4.0, "p50": 5.0}
        po = _make_pricing_option("vcpm", is_fixed=False, price_guidance=guidance)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, VcpmPricingOption)
        assert result.floor_price is None
        assert result.price_guidance is not None
        assert result.price_guidance.p25 == 4.0

    def test_vcpm_fixed_missing_rate_raises(self):
        po = _make_pricing_option("vcpm", is_fixed=True)
        with pytest.raises(ValueError, match="requires rate"):
            convert_pricing_option_to_adcp(po)

    def test_vcpm_auction_missing_price_guidance_raises(self):
        po = _make_pricing_option("vcpm", is_fixed=False)
        with pytest.raises(ValueError, match="requires price_guidance"):
            convert_pricing_option_to_adcp(po)


# ---------------------------------------------------------------------------
# CPC
# ---------------------------------------------------------------------------
class TestCpcConversion:
    """CPC pricing model conversion (lines 173-194)."""

    def test_cpc_fixed_conversion(self):
        po = _make_pricing_option("cpc", is_fixed=True, rate=1.25)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, CpcPricingOption)
        assert result.pricing_model == "cpc"
        assert result.fixed_price == 1.25
        assert result.currency == "USD"
        assert result.pricing_option_id == "cpc_usd_fixed"

    def test_cpc_auction_conversion(self):
        guidance = {"floor": 0.50, "p25": 0.75, "p50": 1.00}
        po = _make_pricing_option("cpc", is_fixed=False, price_guidance=guidance)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, CpcPricingOption)
        assert result.pricing_model == "cpc"
        assert result.floor_price == 0.50
        assert result.price_guidance is not None
        assert result.price_guidance.p25 == 0.75
        assert result.price_guidance.p50 == 1.00
        assert result.pricing_option_id == "cpc_usd_auction"

    def test_cpc_auction_without_floor_price(self):
        guidance = {"p25": 0.75, "p50": 1.00}
        po = _make_pricing_option("cpc", is_fixed=False, price_guidance=guidance)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, CpcPricingOption)
        assert result.floor_price is None

    def test_cpc_fixed_missing_rate_raises(self):
        po = _make_pricing_option("cpc", is_fixed=True)
        with pytest.raises(ValueError, match="requires rate"):
            convert_pricing_option_to_adcp(po)

    def test_cpc_auction_missing_price_guidance_raises(self):
        po = _make_pricing_option("cpc", is_fixed=False)
        with pytest.raises(ValueError, match="requires price_guidance"):
            convert_pricing_option_to_adcp(po)


# ---------------------------------------------------------------------------
# CPCV
# ---------------------------------------------------------------------------
class TestCpcvConversion:
    """CPCV pricing model conversion (lines 196-207)."""

    def test_cpcv_fixed_conversion(self):
        po = _make_pricing_option("cpcv", is_fixed=True, rate=0.05)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, CpcvPricingOption)
        assert result.pricing_model == "cpcv"
        assert result.fixed_price == 0.05
        assert result.pricing_option_id == "cpcv_usd_fixed"

    def test_cpcv_with_parameters(self):
        params = {"view_completion_threshold": 0.75}
        po = _make_pricing_option("cpcv", is_fixed=True, rate=0.05, parameters=params)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, CpcvPricingOption)
        assert result.fixed_price == 0.05
        # CpcvPricingOption accepts parameters as extra fields
        assert hasattr(result, "parameters")

    def test_cpcv_without_parameters(self):
        po = _make_pricing_option("cpcv", is_fixed=True, rate=0.05)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, CpcvPricingOption)
        # CpcvPricingOption has no 'parameters' field in AdCP schema;
        # when not provided, the attribute is absent
        assert not hasattr(result, "parameters") or result.parameters is None

    def test_cpcv_missing_rate_raises(self):
        po = _make_pricing_option("cpcv", is_fixed=True)
        with pytest.raises(ValueError, match="requires rate"):
            convert_pricing_option_to_adcp(po)


# ---------------------------------------------------------------------------
# CPV
# ---------------------------------------------------------------------------
class TestCpvConversion:
    """CPV pricing model conversion (lines 209-221)."""

    def test_cpv_fixed_conversion(self):
        params = {"view_threshold": 0.5}
        po = _make_pricing_option("cpv", is_fixed=True, rate=0.03, parameters=params)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, CpvPricingOption)
        assert result.pricing_model == "cpv"
        assert result.fixed_price == 0.03
        assert result.parameters is not None
        assert result.parameters.view_threshold.root == 0.5
        assert result.pricing_option_id == "cpv_usd_fixed"

    def test_cpv_auction_conversion(self):
        params = {"view_threshold": {"duration_seconds": 5}}
        po = _make_pricing_option("cpv", is_fixed=False, rate=0.02, parameters=params)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, CpvPricingOption)
        assert result.pricing_model == "cpv"
        assert result.floor_price == 0.02
        assert result.parameters is not None
        assert result.pricing_option_id == "cpv_usd_auction"

    def test_cpv_missing_rate_raises(self):
        params = {"view_threshold": 0.5}
        po = _make_pricing_option("cpv", is_fixed=True, parameters=params)
        with pytest.raises(ValueError, match="requires rate"):
            convert_pricing_option_to_adcp(po)


# ---------------------------------------------------------------------------
# CPP
# ---------------------------------------------------------------------------
class TestCppConversion:
    """CPP pricing model conversion (lines 223-233)."""

    def test_cpp_fixed_conversion(self):
        params = {"demographic": "P18-49", "demographic_system": "nielsen"}
        po = _make_pricing_option("cpp", is_fixed=True, rate=25000.00, parameters=params)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, CppPricingOption)
        assert result.pricing_model == "cpp"
        assert result.fixed_price == 25000.00
        assert result.parameters is not None
        assert result.parameters.demographic == "P18-49"
        assert result.pricing_option_id == "cpp_usd_fixed"

    def test_cpp_missing_rate_raises(self):
        params = {"demographic": "P18-49"}
        po = _make_pricing_option("cpp", is_fixed=True, parameters=params)
        with pytest.raises(ValueError, match="requires rate"):
            convert_pricing_option_to_adcp(po)

    def test_cpp_missing_parameters_raises(self):
        po = _make_pricing_option("cpp", is_fixed=True, rate=25000.00)
        with pytest.raises(ValueError, match="requires parameters"):
            convert_pricing_option_to_adcp(po)


# ---------------------------------------------------------------------------
# flat_rate
# ---------------------------------------------------------------------------
class TestFlatRateConversion:
    """flat_rate pricing model conversion (lines 235-246)."""

    def test_flat_rate_conversion(self):
        po = _make_pricing_option("flat_rate", is_fixed=True, rate=5000.00)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, FlatRatePricingOption)
        assert result.pricing_model == "flat_rate"
        assert result.fixed_price == 5000.00
        assert result.pricing_option_id == "flat_rate_usd_fixed"

    def test_flat_rate_with_parameters(self):
        params = {"venue_package": "premium_malls", "share_of_voice": 0.25}
        po = _make_pricing_option("flat_rate", is_fixed=True, rate=5000.00, parameters=params)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, FlatRatePricingOption)
        assert result.fixed_price == 5000.00
        assert result.parameters is not None
        assert result.parameters.venue_package == "premium_malls"
        assert result.parameters.share_of_voice == 0.25

    def test_flat_rate_missing_rate_raises(self):
        po = _make_pricing_option("flat_rate", is_fixed=True)
        with pytest.raises(ValueError, match="requires rate"):
            convert_pricing_option_to_adcp(po)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
class TestPricingConversionEdgeCases:
    """Cross-cutting edge cases."""

    def test_unsupported_pricing_model_raises(self):
        po = _make_pricing_option("unknown_model", is_fixed=True, rate=1.0)
        with pytest.raises(ValueError, match="Unsupported pricing_model"):
            convert_pricing_option_to_adcp(po)

    def test_min_spend_per_package_passed_through(self):
        po = _make_pricing_option("vcpm", is_fixed=True, rate=8.50, min_spend_per_package=500.0)
        result = convert_pricing_option_to_adcp(po)

        assert result.min_spend_per_package == 500.0

    def test_non_usd_currency(self):
        po = _make_pricing_option("cpc", is_fixed=True, rate=1.25, currency="EUR")
        result = convert_pricing_option_to_adcp(po)

        assert result.currency == "EUR"
        assert result.pricing_option_id == "cpc_eur_fixed"

    def test_cpm_fixed_still_works(self):
        """Sanity check: CPM fixed path (already tested elsewhere) still works."""
        po = _make_pricing_option("cpm", is_fixed=True, rate=5.00)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, CpmPricingOption)
        assert result.fixed_price == 5.00
