"""Tests for geo inclusion/exclusion same-value overlap validation.

Implements the AdCP SHOULD requirement from adcp PR #1010:
> Sellers SHOULD reject requests where the same value appears in both
> the inclusion and exclusion field at the same level.

Beads: salesagent-suj
"""

from src.services.targeting_capabilities import validate_geo_overlap


class TestCountryOverlap:
    """Same country in geo_countries and geo_countries_exclude."""

    def test_same_country_rejected(self):
        targeting = {
            "geo_countries": ["US", "CA"],
            "geo_countries_exclude": ["US"],
        }
        violations = validate_geo_overlap(targeting)
        assert len(violations) == 1
        assert "US" in violations[0]
        assert "geo_countries" in violations[0]

    def test_multiple_overlapping_countries(self):
        targeting = {
            "geo_countries": ["US", "CA", "GB"],
            "geo_countries_exclude": ["US", "GB"],
        }
        violations = validate_geo_overlap(targeting)
        assert len(violations) == 1  # One violation message for the field pair
        assert "US" in violations[0]
        assert "GB" in violations[0]

    def test_no_overlap_passes(self):
        targeting = {
            "geo_countries": ["US", "CA"],
            "geo_countries_exclude": ["GB", "DE"],
        }
        violations = validate_geo_overlap(targeting)
        assert violations == []

    def test_include_only_passes(self):
        targeting = {"geo_countries": ["US", "CA"]}
        violations = validate_geo_overlap(targeting)
        assert violations == []

    def test_exclude_only_passes(self):
        targeting = {"geo_countries_exclude": ["US"]}
        violations = validate_geo_overlap(targeting)
        assert violations == []


class TestRegionOverlap:
    """Same region in geo_regions and geo_regions_exclude."""

    def test_same_region_rejected(self):
        targeting = {
            "geo_regions": ["US-CA", "US-NY"],
            "geo_regions_exclude": ["US-CA"],
        }
        violations = validate_geo_overlap(targeting)
        assert len(violations) == 1
        assert "US-CA" in violations[0]
        assert "geo_regions" in violations[0]

    def test_no_overlap_passes(self):
        targeting = {
            "geo_regions": ["US-CA", "US-NY"],
            "geo_regions_exclude": ["US-TX"],
        }
        violations = validate_geo_overlap(targeting)
        assert violations == []


class TestMetroOverlap:
    """Same metro code within same system in geo_metros and geo_metros_exclude."""

    def test_same_system_same_value_rejected(self):
        targeting = {
            "geo_metros": [{"system": "nielsen_dma", "values": ["501", "502"]}],
            "geo_metros_exclude": [{"system": "nielsen_dma", "values": ["501"]}],
        }
        violations = validate_geo_overlap(targeting)
        assert len(violations) == 1
        assert "501" in violations[0]
        assert "geo_metros" in violations[0]

    def test_different_systems_no_conflict(self):
        """Different metro systems can have the same code without conflict."""
        targeting = {
            "geo_metros": [{"system": "nielsen_dma", "values": ["501"]}],
            "geo_metros_exclude": [{"system": "ofcom_itv", "values": ["501"]}],
        }
        violations = validate_geo_overlap(targeting)
        assert violations == []

    def test_same_system_no_overlap(self):
        targeting = {
            "geo_metros": [{"system": "nielsen_dma", "values": ["501", "502"]}],
            "geo_metros_exclude": [{"system": "nielsen_dma", "values": ["503"]}],
        }
        violations = validate_geo_overlap(targeting)
        assert violations == []

    def test_multiple_systems_overlap_in_one(self):
        """Overlap detected only within the matching system."""
        targeting = {
            "geo_metros": [
                {"system": "nielsen_dma", "values": ["501", "502"]},
                {"system": "ofcom_itv", "values": ["100"]},
            ],
            "geo_metros_exclude": [
                {"system": "nielsen_dma", "values": ["501"]},
                {"system": "ofcom_itv", "values": ["200"]},
            ],
        }
        violations = validate_geo_overlap(targeting)
        assert len(violations) == 1
        assert "501" in violations[0]
        assert "nielsen_dma" in violations[0]


class TestPostalAreaOverlap:
    """Same postal code within same system in geo_postal_areas and geo_postal_areas_exclude."""

    def test_same_system_same_value_rejected(self):
        targeting = {
            "geo_postal_areas": [{"system": "us_zip", "values": ["10001", "10002"]}],
            "geo_postal_areas_exclude": [{"system": "us_zip", "values": ["10001"]}],
        }
        violations = validate_geo_overlap(targeting)
        assert len(violations) == 1
        assert "10001" in violations[0]
        assert "geo_postal_areas" in violations[0]

    def test_different_systems_no_conflict(self):
        targeting = {
            "geo_postal_areas": [{"system": "us_zip", "values": ["10001"]}],
            "geo_postal_areas_exclude": [{"system": "uk_postcode", "values": ["10001"]}],
        }
        violations = validate_geo_overlap(targeting)
        assert violations == []

    def test_no_overlap_passes(self):
        targeting = {
            "geo_postal_areas": [{"system": "us_zip", "values": ["10001"]}],
            "geo_postal_areas_exclude": [{"system": "us_zip", "values": ["90210"]}],
        }
        violations = validate_geo_overlap(targeting)
        assert violations == []


class TestMultipleLevelOverlap:
    """Overlaps at multiple geo levels produce multiple violations."""

    def test_country_and_region_overlap(self):
        targeting = {
            "geo_countries": ["US"],
            "geo_countries_exclude": ["US"],
            "geo_regions": ["US-CA"],
            "geo_regions_exclude": ["US-CA"],
        }
        violations = validate_geo_overlap(targeting)
        assert len(violations) == 2


class TestEdgeCases:
    """Edge cases for geo overlap validation."""

    def test_empty_targeting(self):
        violations = validate_geo_overlap({})
        assert violations == []

    def test_none_values_ignored(self):
        targeting = {
            "geo_countries": None,
            "geo_countries_exclude": None,
        }
        violations = validate_geo_overlap(targeting)
        assert violations == []

    def test_empty_lists_no_overlap(self):
        targeting = {
            "geo_countries": [],
            "geo_countries_exclude": [],
        }
        violations = validate_geo_overlap(targeting)
        assert violations == []

    def test_non_geo_fields_ignored(self):
        targeting = {
            "device_type_any_of": ["mobile"],
            "content_cat_any_of": ["IAB1"],
        }
        violations = validate_geo_overlap(targeting)
        assert violations == []
