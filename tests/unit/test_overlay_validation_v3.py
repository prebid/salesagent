"""Tests for validate_overlay_targeting with v3 field names.

Regression tests for salesagent-9nd: ensures overlay validation works with
v3 structured field names (geo_countries, geo_regions, etc.) without
_any_of/_none_of suffix-stripping.
"""

from src.core.schemas import Targeting
from src.services.targeting_capabilities import validate_overlay_targeting


class TestV3GeoFieldsPassValidation:
    """v3 geo inclusion fields should not produce violations."""

    def test_geo_countries_no_violation(self):
        violations = validate_overlay_targeting(Targeting(geo_countries=["US", "CA"]))
        assert violations == []

    def test_geo_regions_no_violation(self):
        violations = validate_overlay_targeting(Targeting(geo_regions=["US-NY"]))
        assert violations == []

    def test_geo_metros_no_violation(self):
        violations = validate_overlay_targeting(Targeting(geo_metros=[{"system": "nielsen_dma", "values": ["501"]}]))
        assert violations == []

    def test_geo_postal_areas_no_violation(self):
        violations = validate_overlay_targeting(Targeting(geo_postal_areas=[{"system": "us_zip", "values": ["90210"]}]))
        assert violations == []


class TestV3GeoExclusionFieldsValidated:
    """v3 geo exclusion fields must also be validated (not silently ignored)."""

    def test_geo_countries_exclude_no_violation(self):
        violations = validate_overlay_targeting(Targeting(geo_countries_exclude=["RU"]))
        assert violations == []

    def test_geo_regions_exclude_no_violation(self):
        violations = validate_overlay_targeting(Targeting(geo_regions_exclude=["US-TX"]))
        assert violations == []

    def test_geo_metros_exclude_no_violation(self):
        violations = validate_overlay_targeting(
            Targeting(geo_metros_exclude=[{"system": "nielsen_dma", "values": ["501"]}])
        )
        assert violations == []

    def test_geo_postal_areas_exclude_no_violation(self):
        violations = validate_overlay_targeting(
            Targeting(geo_postal_areas_exclude=[{"system": "us_zip", "values": ["90210"]}])
        )
        assert violations == []


class TestManagedOnlyFieldsCaught:
    """Managed-only fields must produce violations."""

    def test_key_value_pairs_violation(self):
        violations = validate_overlay_targeting(Targeting(key_value_pairs={"foo": "bar"}))
        assert len(violations) == 1
        assert "key_value_pairs" in violations[0]
        assert "managed-only" in violations[0]

    def test_mixed_overlay_and_managed(self):
        """Valid overlay fields alongside managed-only should only flag managed-only."""
        violations = validate_overlay_targeting(
            Targeting(geo_countries=["US"], device_type_any_of=["mobile"], key_value_pairs={"foo": "bar"})
        )
        assert len(violations) == 1
        assert "key_value_pairs" in violations[0]


class TestSuffixStrippingRemoved:
    """No _any_of/_none_of suffix-stripping heuristic remains."""

    def test_device_type_any_of_no_violation(self):
        """Fields still using _any_of suffix should work via explicit mapping."""
        violations = validate_overlay_targeting(Targeting(device_type_any_of=["mobile"]))
        assert violations == []

    def test_os_none_of_no_violation(self):
        """Fields using _none_of suffix should work via explicit mapping."""
        violations = validate_overlay_targeting(Targeting(os_none_of=["android"]))
        assert violations == []


class TestEdgeCases:
    """Edge cases for the validation function."""

    def test_empty_targeting_no_violations(self):
        violations = validate_overlay_targeting(Targeting())
        assert violations == []

    def test_frequency_cap_no_violation(self):
        violations = validate_overlay_targeting(Targeting(frequency_cap={"suppress_minutes": 60}))
        assert violations == []

    def test_custom_field_no_violation(self):
        violations = validate_overlay_targeting(Targeting(custom={"key": "value"}))
        assert violations == []
