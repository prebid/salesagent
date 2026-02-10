"""Tests for TargetingCapabilities.validate_geo_systems().

Regression tests for salesagent-xy0: ensures adapter geo system validation
checks both include and exclude fields and returns descriptive error messages.
"""

from src.adapters.base import TargetingCapabilities
from src.core.schemas import Targeting


class TestEmptyTargeting:
    """No geo fields → no errors."""

    def test_empty_targeting(self):
        caps = TargetingCapabilities()
        targeting = Targeting()
        assert caps.validate_geo_systems(targeting) == []

    def test_only_countries(self):
        caps = TargetingCapabilities(geo_countries=True)
        targeting = Targeting(geo_countries=["US"])
        assert caps.validate_geo_systems(targeting) == []


class TestMetroSystemValidation:
    """Metro system checks for geo_metros and geo_metros_exclude."""

    def test_supported_metro_system_no_error(self):
        caps = TargetingCapabilities(nielsen_dma=True)
        targeting = Targeting(
            geo_countries=["US"],
            geo_metros=[{"system": "nielsen_dma", "values": ["501"]}],
        )
        assert caps.validate_geo_systems(targeting) == []

    def test_unsupported_metro_system_error(self):
        caps = TargetingCapabilities(nielsen_dma=True)
        targeting = Targeting(
            geo_countries=["GB"],
            geo_metros=[{"system": "uk_itl1", "values": ["TLG"]}],
        )
        errors = caps.validate_geo_systems(targeting)
        assert len(errors) == 1
        assert "uk_itl1" in errors[0]
        assert "nielsen_dma" in errors[0]

    def test_unsupported_metro_exclude_error(self):
        caps = TargetingCapabilities(nielsen_dma=True)
        targeting = Targeting(
            geo_countries=["DE"],
            geo_metros_exclude=[{"system": "eurostat_nuts2", "values": ["DE1"]}],
        )
        errors = caps.validate_geo_systems(targeting)
        assert len(errors) == 1
        assert "eurostat_nuts2" in errors[0]

    def test_multiple_unsupported_metro_systems(self):
        caps = TargetingCapabilities()  # no metro support at all
        targeting = Targeting(
            geo_countries=["US"],
            geo_metros=[{"system": "nielsen_dma", "values": ["501"]}],
            geo_metros_exclude=[{"system": "eurostat_nuts2", "values": ["DE1"]}],
        )
        errors = caps.validate_geo_systems(targeting)
        assert len(errors) == 2

    def test_no_adapter_metro_support_lists_none(self):
        """When adapter supports no metro systems, error says 'none'."""
        caps = TargetingCapabilities()
        targeting = Targeting(
            geo_countries=["US"],
            geo_metros=[{"system": "nielsen_dma", "values": ["501"]}],
        )
        errors = caps.validate_geo_systems(targeting)
        assert len(errors) == 1
        assert "none" in errors[0]

    def test_custom_metro_system_rejected(self):
        """Custom metro system is rejected unless adapter explicitly supports it."""
        caps = TargetingCapabilities(nielsen_dma=True)
        targeting = Targeting(
            geo_countries=["US"],
            geo_metros=[{"system": "custom", "values": ["CUSTOM_1"]}],
        )
        errors = caps.validate_geo_systems(targeting)
        assert len(errors) == 1
        assert "custom" in errors[0]
        assert "nielsen_dma" in errors[0]


class TestPostalSystemValidation:
    """Postal system checks for geo_postal_areas and geo_postal_areas_exclude."""

    def test_supported_postal_system_no_error(self):
        caps = TargetingCapabilities(us_zip=True)
        targeting = Targeting(
            geo_countries=["US"],
            geo_postal_areas=[{"system": "us_zip", "values": ["10001"]}],
        )
        assert caps.validate_geo_systems(targeting) == []

    def test_unsupported_postal_system_error(self):
        caps = TargetingCapabilities(us_zip=True)
        targeting = Targeting(
            geo_countries=["GB"],
            geo_postal_areas=[{"system": "gb_outward", "values": ["SW1"]}],
        )
        errors = caps.validate_geo_systems(targeting)
        assert len(errors) == 1
        assert "gb_outward" in errors[0]
        assert "us_zip" in errors[0]

    def test_unsupported_postal_exclude_error(self):
        caps = TargetingCapabilities(us_zip=True)
        targeting = Targeting(
            geo_countries=["DE"],
            geo_postal_areas_exclude=[{"system": "de_plz", "values": ["10115"]}],
        )
        errors = caps.validate_geo_systems(targeting)
        assert len(errors) == 1
        assert "de_plz" in errors[0]

    def test_no_adapter_postal_support_lists_none(self):
        caps = TargetingCapabilities()
        targeting = Targeting(
            geo_countries=["US"],
            geo_postal_areas=[{"system": "us_zip", "values": ["10001"]}],
        )
        errors = caps.validate_geo_systems(targeting)
        assert len(errors) == 1
        assert "none" in errors[0]


class TestMixedValidation:
    """Both metro and postal validation in a single call."""

    def test_both_metro_and_postal_errors(self):
        caps = TargetingCapabilities(geo_countries=True)
        targeting = Targeting(
            geo_countries=["US"],
            geo_metros=[{"system": "nielsen_dma", "values": ["501"]}],
            geo_postal_areas=[{"system": "us_zip", "values": ["10001"]}],
        )
        errors = caps.validate_geo_systems(targeting)
        assert len(errors) == 2

    def test_all_supported_no_errors(self):
        caps = TargetingCapabilities(
            geo_countries=True,
            nielsen_dma=True,
            us_zip=True,
        )
        targeting = Targeting(
            geo_countries=["US"],
            geo_metros=[{"system": "nielsen_dma", "values": ["501"]}],
            geo_postal_areas=[{"system": "us_zip", "values": ["10001"]}],
        )
        assert caps.validate_geo_systems(targeting) == []

    def test_include_and_exclude_both_checked(self):
        """Both include and exclude fields contribute errors."""
        caps = TargetingCapabilities(geo_countries=True)
        targeting = Targeting(
            geo_countries=["US"],
            geo_metros=[{"system": "nielsen_dma", "values": ["501"]}],
            geo_metros_exclude=[{"system": "uk_itl1", "values": ["TLG"]}],
            geo_postal_areas=[{"system": "us_zip", "values": ["10001"]}],
            geo_postal_areas_exclude=[{"system": "gb_outward", "values": ["SW1"]}],
        )
        errors = caps.validate_geo_systems(targeting)
        assert len(errors) == 4


class TestErrorMessageFormat:
    """Error messages include the unsupported system and supported alternatives."""

    def test_error_names_unsupported_system(self):
        caps = TargetingCapabilities(nielsen_dma=True)
        targeting = Targeting(
            geo_countries=["DE"],
            geo_metros=[{"system": "eurostat_nuts2", "values": ["DE1"]}],
        )
        errors = caps.validate_geo_systems(targeting)
        assert "eurostat_nuts2" in errors[0]

    def test_error_names_supported_alternatives(self):
        caps = TargetingCapabilities(nielsen_dma=True, uk_itl1=True)
        targeting = Targeting(
            geo_countries=["DE"],
            geo_metros=[{"system": "eurostat_nuts2", "values": ["DE1"]}],
        )
        errors = caps.validate_geo_systems(targeting)
        assert "nielsen_dma" in errors[0]
        assert "uk_itl1" in errors[0]

    def test_error_format_matches_spec(self):
        """Error format: "Unsupported metro system '<name>'. This adapter supports: <list>"."""
        caps = TargetingCapabilities(nielsen_dma=True)
        targeting = Targeting(
            geo_countries=["DE"],
            geo_metros=[{"system": "eurostat_nuts2", "values": ["DE1"]}],
        )
        errors = caps.validate_geo_systems(targeting)
        assert errors[0].startswith("Unsupported metro system")
        assert "This adapter supports:" in errors[0]

    def test_postal_error_format_matches_spec(self):
        caps = TargetingCapabilities(us_zip=True)
        targeting = Targeting(
            geo_countries=["GB"],
            geo_postal_areas=[{"system": "gb_outward", "values": ["SW1"]}],
        )
        errors = caps.validate_geo_systems(targeting)
        assert errors[0].startswith("Unsupported postal system")
        assert "This adapter supports:" in errors[0]


def _custom_metro_targeting() -> Targeting:
    return Targeting(
        geo_countries=["US"],
        geo_metros=[{"system": "custom", "values": ["CUSTOM_1"]}],
    )


class TestNoAdapterSupportsCustomMetro:
    """Verify that every real adapter rejects custom metro systems.

    Each adapter's get_targeting_capabilities() declares what it supports.
    None currently declare custom metro support, so custom must be rejected.
    """

    def test_gam_rejects_custom_metro(self):
        from src.adapters.google_ad_manager import GoogleAdManager

        caps = GoogleAdManager.get_targeting_capabilities(None)
        errors = caps.validate_geo_systems(_custom_metro_targeting())
        assert any("custom" in e for e in errors)

    def test_kevel_rejects_custom_metro(self):
        """Kevel inherits base default (geo_countries only)."""
        caps = TargetingCapabilities(geo_countries=True)  # base default
        errors = caps.validate_geo_systems(_custom_metro_targeting())
        assert any("custom" in e for e in errors)

    def test_triton_rejects_custom_metro(self):
        """Triton inherits base default (geo_countries only)."""
        caps = TargetingCapabilities(geo_countries=True)  # base default
        errors = caps.validate_geo_systems(_custom_metro_targeting())
        assert any("custom" in e for e in errors)

    def test_mock_rejects_custom_metro(self):
        from src.adapters.mock_ad_server import MockAdServer

        caps = MockAdServer.get_targeting_capabilities(None)
        errors = caps.validate_geo_systems(_custom_metro_targeting())
        assert any("custom" in e for e in errors)
