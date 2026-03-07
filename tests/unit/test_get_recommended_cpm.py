"""Unit tests for get_recommended_cpm() helper function.

Tests the pure function that extracts the p75 CPM from a product's
pricing_options. No database or external services needed.
"""

from unittest.mock import MagicMock

from src.core.tools.products import get_recommended_cpm


def _make_pricing_option(pricing_model: str, price_guidance=None):
    """Build a mock PricingOption (RootModel wrapper around a typed option).

    Mirrors adcp library's PricingOption which is a RootModel — .root gives the
    inner typed option (CpmPricingOption, etc.).
    """
    inner = MagicMock()
    inner.pricing_model = pricing_model
    inner.price_guidance = price_guidance

    wrapper = MagicMock()
    wrapper.root = inner
    return wrapper


def _make_price_guidance(p75=None, p50=None, p25=None, p90=None):
    """Build a mock PriceGuidance with named percentile fields."""
    pg = MagicMock()
    pg.p25 = p25
    pg.p50 = p50
    pg.p75 = p75
    pg.p90 = p90
    return pg


class TestGetRecommendedCpm:
    """Unit tests for get_recommended_cpm()."""

    def test_extracts_p75_from_cpm_option(self):
        """get_recommended_cpm returns p75 from a CPM pricing option with price_guidance."""
        pg = _make_price_guidance(p75=20.0, p50=15.0)
        product = MagicMock()
        product.pricing_options = [_make_pricing_option("CPM", price_guidance=pg)]

        result = get_recommended_cpm(product)
        assert result == 20.0

    def test_extracts_p75_from_lowercase_cpm(self):
        """get_recommended_cpm handles lowercase 'cpm' pricing_model."""
        pg = _make_price_guidance(p75=25.5)
        product = MagicMock()
        product.pricing_options = [_make_pricing_option("cpm", price_guidance=pg)]

        result = get_recommended_cpm(product)
        assert result == 25.5

    def test_returns_none_when_no_cpm_option(self):
        """get_recommended_cpm returns None when no CPM pricing option exists."""
        pg = _make_price_guidance(p75=30.0)
        product = MagicMock()
        product.pricing_options = [_make_pricing_option("CPC", price_guidance=pg)]

        result = get_recommended_cpm(product)
        assert result is None

    def test_returns_none_when_no_pricing_options(self):
        """get_recommended_cpm returns None when product has empty pricing_options."""
        product = MagicMock()
        product.pricing_options = []

        result = get_recommended_cpm(product)
        assert result is None

    def test_returns_none_when_price_guidance_is_none(self):
        """get_recommended_cpm returns None when CPM option has no price_guidance."""
        product = MagicMock()
        product.pricing_options = [_make_pricing_option("CPM", price_guidance=None)]

        result = get_recommended_cpm(product)
        assert result is None

    def test_returns_none_when_p75_is_none(self):
        """get_recommended_cpm returns None when price_guidance.p75 is None."""
        pg = _make_price_guidance(p75=None, p50=15.0)
        product = MagicMock()
        product.pricing_options = [_make_pricing_option("CPM", price_guidance=pg)]

        result = get_recommended_cpm(product)
        assert result is None

    def test_returns_float(self):
        """get_recommended_cpm always returns a float (not Decimal or int)."""
        pg = _make_price_guidance(p75=20)
        product = MagicMock()
        product.pricing_options = [_make_pricing_option("CPM", price_guidance=pg)]

        result = get_recommended_cpm(product)
        assert isinstance(result, float)
        assert result == 20.0

    def test_selects_first_cpm_option(self):
        """get_recommended_cpm returns p75 from the first CPM option found."""
        pg1 = _make_price_guidance(p75=10.0)
        pg2 = _make_price_guidance(p75=30.0)
        product = MagicMock()
        product.pricing_options = [
            _make_pricing_option("CPC", price_guidance=_make_price_guidance(p75=99.0)),
            _make_pricing_option("CPM", price_guidance=pg1),
            _make_pricing_option("CPM", price_guidance=pg2),
        ]

        result = get_recommended_cpm(product)
        assert result == 10.0
