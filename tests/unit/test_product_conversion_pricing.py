"""Unit tests for non-CPM pricing model conversion paths.

Tests convert_pricing_option_to_adcp() for VCPM, CPC, CPCV, CPV, CPP,
flat_rate, CPA, and time-based pricing models. Each model tests fixed
conversion, auction conversion (where applicable), and error cases for
missing required fields.
"""

import pytest
from adcp import (
    CpaPricingOption,
    CpcPricingOption,
    CpcvPricingOption,
    CpmPricingOption,
    CppPricingOption,
    CpvPricingOption,
    FlatRatePricingOption,
    TimeBasedPricingOption,
    TimeUnit,
    VcpmPricingOption,
)
from adcp.types.generated_poc.enums.event_type import EventType

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
# CPA
# ---------------------------------------------------------------------------
class TestCpaConversion:
    """CPA (Cost Per Acquisition) pricing model conversion (AdCP 3.1)."""

    def test_cpa_fixed_with_default_event_type(self):
        """CPA with no event_type in parameters defaults to 'purchase'."""
        po = _make_pricing_option("cpa", is_fixed=True, rate=15.00)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, CpaPricingOption)
        assert result.pricing_model == "cpa"
        assert result.fixed_price == 15.00
        assert result.currency == "USD"
        assert result.event_type == EventType.purchase
        assert result.pricing_option_id == "cpa_usd_fixed"

    def test_cpa_with_explicit_event_type(self):
        """CPA with event_type in parameters uses that event type."""
        params = {"event_type": "lead"}
        po = _make_pricing_option("cpa", is_fixed=True, rate=25.00, parameters=params)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, CpaPricingOption)
        assert result.event_type == EventType.lead
        assert result.fixed_price == 25.00

    def test_cpa_with_purchase_event_type(self):
        """CPA with explicit 'purchase' event_type."""
        params = {"event_type": "purchase"}
        po = _make_pricing_option("cpa", is_fixed=True, rate=10.00, parameters=params)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, CpaPricingOption)
        assert result.event_type == EventType.purchase

    def test_cpa_with_app_install_event_type(self):
        """CPA with 'app_install' event_type."""
        params = {"event_type": "app_install"}
        po = _make_pricing_option("cpa", is_fixed=True, rate=5.00, parameters=params)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, CpaPricingOption)
        assert result.event_type == EventType.app_install

    def test_cpa_unknown_event_type_falls_back_to_purchase(self):
        """CPA with unknown event_type logs a warning and defaults to 'purchase'."""
        params = {"event_type": "not_a_real_event"}
        po = _make_pricing_option("cpa", is_fixed=True, rate=10.00, parameters=params)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, CpaPricingOption)
        assert result.event_type == EventType.purchase

    def test_cpa_missing_rate_raises(self):
        """CPA without a rate raises ValueError."""
        po = _make_pricing_option("cpa", is_fixed=True)
        with pytest.raises(ValueError, match="requires rate"):
            convert_pricing_option_to_adcp(po)

    def test_cpa_non_usd_currency(self):
        """CPA pricing option with EUR currency."""
        po = _make_pricing_option("cpa", is_fixed=True, rate=12.00, currency="EUR")
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, CpaPricingOption)
        assert result.currency == "EUR"
        assert result.pricing_option_id == "cpa_eur_fixed"

    def test_cpa_with_min_spend(self):
        """CPA pricing option with min_spend_per_package."""
        po = _make_pricing_option("cpa", is_fixed=True, rate=10.00, min_spend_per_package=200.0)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, CpaPricingOption)
        assert result.min_spend_per_package == 200.0


# ---------------------------------------------------------------------------
# time (TimeBasedPricingOption)
# ---------------------------------------------------------------------------
class TestTimeBasedConversion:
    """Time-based pricing model conversion (AdCP 3.1)."""

    def test_time_fixed_daily_rate(self):
        """Fixed time-based pricing with day time_unit."""
        params = {"time_unit": "day"}
        po = _make_pricing_option("time", is_fixed=True, rate=500.00, parameters=params)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, TimeBasedPricingOption)
        assert result.pricing_model == "time"
        assert result.fixed_price == 500.00
        assert result.currency == "USD"
        assert result.parameters.time_unit == TimeUnit.day
        assert result.pricing_option_id == "time_usd_fixed"

    def test_time_fixed_weekly_rate(self):
        """Fixed time-based pricing with week time_unit."""
        params = {"time_unit": "week"}
        po = _make_pricing_option("time", is_fixed=True, rate=2500.00, parameters=params)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, TimeBasedPricingOption)
        assert result.parameters.time_unit == TimeUnit.week
        assert result.fixed_price == 2500.00

    def test_time_fixed_monthly_rate(self):
        """Fixed time-based pricing with month time_unit."""
        params = {"time_unit": "month"}
        po = _make_pricing_option("time", is_fixed=True, rate=8000.00, parameters=params)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, TimeBasedPricingOption)
        assert result.parameters.time_unit == TimeUnit.month
        assert result.fixed_price == 8000.00

    def test_time_fixed_hourly_rate(self):
        """Fixed time-based pricing with hour time_unit."""
        params = {"time_unit": "hour"}
        po = _make_pricing_option("time", is_fixed=True, rate=50.00, parameters=params)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, TimeBasedPricingOption)
        assert result.parameters.time_unit == TimeUnit.hour
        assert result.fixed_price == 50.00

    def test_time_with_min_max_duration(self):
        """Time-based pricing with min_duration and max_duration constraints."""
        params = {"time_unit": "day", "min_duration": 3, "max_duration": 30}
        po = _make_pricing_option("time", is_fixed=True, rate=500.00, parameters=params)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, TimeBasedPricingOption)
        assert result.parameters.min_duration == 3
        assert result.parameters.max_duration == 30

    def test_time_auction_with_floor_price(self):
        """Auction time-based pricing with floor_price from price_guidance."""
        guidance = {"floor": 200.00, "p25": 300.0, "p50": 400.0}
        params = {"time_unit": "day"}
        po = _make_pricing_option("time", is_fixed=False, price_guidance=guidance, parameters=params)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, TimeBasedPricingOption)
        assert result.floor_price == 200.00
        assert result.fixed_price is None
        assert result.pricing_option_id == "time_usd_auction"

    def test_time_auction_without_floor_price(self):
        """Auction time-based pricing without floor_price."""
        guidance = {"p25": 300.0, "p50": 400.0}
        params = {"time_unit": "week"}
        po = _make_pricing_option("time", is_fixed=False, price_guidance=guidance, parameters=params)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, TimeBasedPricingOption)
        assert result.floor_price is None
        assert result.fixed_price is None

    def test_time_missing_parameters_raises(self):
        """Time-based pricing without parameters raises ValueError."""
        po = _make_pricing_option("time", is_fixed=True, rate=500.00)
        with pytest.raises(ValueError, match="requires parameters.time_unit"):
            convert_pricing_option_to_adcp(po)

    def test_time_missing_time_unit_in_parameters_raises(self):
        """Time-based pricing with parameters dict missing time_unit raises ValueError."""
        params = {"min_duration": 3}
        po = _make_pricing_option("time", is_fixed=True, rate=500.00, parameters=params)
        with pytest.raises(ValueError, match="requires parameters.time_unit"):
            convert_pricing_option_to_adcp(po)

    def test_time_unknown_time_unit_raises(self):
        """Time-based pricing with unknown time_unit raises ValueError."""
        params = {"time_unit": "fortnight"}
        po = _make_pricing_option("time", is_fixed=True, rate=500.00, parameters=params)
        with pytest.raises(ValueError, match="unknown time_unit"):
            convert_pricing_option_to_adcp(po)

    def test_time_fixed_missing_rate_raises(self):
        """Fixed time-based pricing without rate raises ValueError."""
        params = {"time_unit": "day"}
        po = _make_pricing_option("time", is_fixed=True, parameters=params)
        with pytest.raises(ValueError, match="requires rate"):
            convert_pricing_option_to_adcp(po)

    def test_time_non_usd_currency(self):
        """Time-based pricing with EUR currency."""
        params = {"time_unit": "day"}
        po = _make_pricing_option("time", is_fixed=True, rate=400.00, currency="EUR", parameters=params)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, TimeBasedPricingOption)
        assert result.currency == "EUR"
        assert result.pricing_option_id == "time_eur_fixed"

    def test_time_with_min_spend(self):
        """Time-based pricing with min_spend_per_package."""
        params = {"time_unit": "week"}
        po = _make_pricing_option("time", is_fixed=True, rate=2000.00, parameters=params, min_spend_per_package=4000.0)
        result = convert_pricing_option_to_adcp(po)

        assert isinstance(result, TimeBasedPricingOption)
        assert result.min_spend_per_package == 4000.0


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
