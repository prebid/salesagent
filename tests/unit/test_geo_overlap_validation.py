"""Tests for geo inclusion/exclusion same-value overlap validation.

Implements the AdCP SHOULD requirement from adcp PR #1010:
> Sellers SHOULD reject requests where the same value appears in both
> the inclusion and exclusion field at the same level.

Updated for : validation now accepts Targeting model directly.
"""

from src.core.schemas import Targeting
from src.services.targeting_capabilities import geo_overlap_conflicts


def _flat(record: dict) -> str:
    """Every value in one conflict record, joined -- so a test can name a field or a value.

    The validator returns {include, exclude, values} (plus `system` for the structured
    pairs) rather than a sentence: these reach the buyer through errors[0].details, and a
    rendered sentence there is the message smuggled back in (salesagent-3dawm.9). Tests
    that used to substring-match the sentence now match against the record's own values.
    """
    parts = [str(record.get("include", "")), str(record.get("exclude", "")), str(record.get("system", ""))]
    parts.extend(str(v) for v in record.get("values", []))
    return " ".join(parts)


class TestCountryOverlap:
    """Same country in geo_countries and geo_countries_exclude."""

    def test_same_country_rejected(self):
        targeting = Targeting(
            geo_countries=["US", "CA"],
            geo_countries_exclude=["US"],
        )
        violations = geo_overlap_conflicts(targeting)
        assert len(violations) == 1
        assert "US" in _flat(violations[0])
        assert "geo_countries" in _flat(violations[0])

    def test_multiple_overlapping_countries(self):
        targeting = Targeting(
            geo_countries=["US", "CA", "GB"],
            geo_countries_exclude=["US", "GB"],
        )
        violations = geo_overlap_conflicts(targeting)
        assert len(violations) == 1  # One violation message for the field pair
        assert "US" in _flat(violations[0])
        assert "GB" in _flat(violations[0])

    def test_no_overlap_passes(self):
        targeting = Targeting(
            geo_countries=["US", "CA"],
            geo_countries_exclude=["GB", "DE"],
        )
        violations = geo_overlap_conflicts(targeting)
        assert violations == []

    def test_include_only_passes(self):
        targeting = Targeting(geo_countries=["US", "CA"])
        violations = geo_overlap_conflicts(targeting)
        assert violations == []

    def test_exclude_only_passes(self):
        targeting = Targeting(geo_countries_exclude=["US"])
        violations = geo_overlap_conflicts(targeting)
        assert violations == []


class TestRegionOverlap:
    """Same region in geo_regions and geo_regions_exclude."""

    def test_same_region_rejected(self):
        targeting = Targeting(
            geo_regions=["US-CA", "US-NY"],
            geo_regions_exclude=["US-CA"],
        )
        violations = geo_overlap_conflicts(targeting)
        assert len(violations) == 1
        assert "US-CA" in _flat(violations[0])
        assert "geo_regions" in _flat(violations[0])

    def test_no_overlap_passes(self):
        targeting = Targeting(
            geo_regions=["US-CA", "US-NY"],
            geo_regions_exclude=["US-TX"],
        )
        violations = geo_overlap_conflicts(targeting)
        assert violations == []


class TestMetroOverlap:
    """Same metro code within same system in geo_metros and geo_metros_exclude."""

    def test_same_system_same_value_rejected(self):
        targeting = Targeting(
            geo_metros=[{"system": "nielsen_dma", "values": ["501", "502"]}],
            geo_metros_exclude=[{"system": "nielsen_dma", "values": ["501"]}],
        )
        violations = geo_overlap_conflicts(targeting)
        assert len(violations) == 1
        assert "501" in _flat(violations[0])
        assert "geo_metros" in _flat(violations[0])

    def test_different_systems_no_conflict(self):
        """Different metro systems can have the same code without conflict."""
        targeting = Targeting(
            geo_metros=[{"system": "nielsen_dma", "values": ["501"]}],
            geo_metros_exclude=[{"system": "uk_itl1", "values": ["501"]}],
        )
        violations = geo_overlap_conflicts(targeting)
        assert violations == []

    def test_same_system_no_overlap(self):
        targeting = Targeting(
            geo_metros=[{"system": "nielsen_dma", "values": ["501", "502"]}],
            geo_metros_exclude=[{"system": "nielsen_dma", "values": ["503"]}],
        )
        violations = geo_overlap_conflicts(targeting)
        assert violations == []

    def test_multiple_systems_overlap_in_one(self):
        """Overlap detected only within the matching system."""
        targeting = Targeting(
            geo_metros=[
                {"system": "nielsen_dma", "values": ["501", "502"]},
                {"system": "uk_itl1", "values": ["100"]},
            ],
            geo_metros_exclude=[
                {"system": "nielsen_dma", "values": ["501"]},
                {"system": "uk_itl1", "values": ["200"]},
            ],
        )
        violations = geo_overlap_conflicts(targeting)
        assert len(violations) == 1
        assert "501" in _flat(violations[0])
        assert "nielsen_dma" in _flat(violations[0])


class TestPostalAreaOverlap:
    """Same postal code within same system in geo_postal_areas and geo_postal_areas_exclude."""

    def test_same_system_same_value_rejected(self):
        targeting = Targeting(
            geo_postal_areas=[{"system": "us_zip", "values": ["10001", "10002"]}],
            geo_postal_areas_exclude=[{"system": "us_zip", "values": ["10001"]}],
        )
        violations = geo_overlap_conflicts(targeting)
        assert len(violations) == 1
        assert "10001" in _flat(violations[0])
        assert "geo_postal_areas" in _flat(violations[0])

    def test_different_systems_no_conflict(self):
        targeting = Targeting(
            geo_postal_areas=[{"system": "us_zip", "values": ["10001"]}],
            geo_postal_areas_exclude=[{"system": "gb_outward", "values": ["10001"]}],
        )
        violations = geo_overlap_conflicts(targeting)
        assert violations == []

    def test_no_overlap_passes(self):
        targeting = Targeting(
            geo_postal_areas=[{"system": "us_zip", "values": ["10001"]}],
            geo_postal_areas_exclude=[{"system": "us_zip", "values": ["90210"]}],
        )
        violations = geo_overlap_conflicts(targeting)
        assert violations == []


class TestMultipleLevelOverlap:
    """Overlaps at multiple geo levels produce multiple violations."""

    def test_country_and_region_overlap(self):
        targeting = Targeting(
            geo_countries=["US"],
            geo_countries_exclude=["US"],
            geo_regions=["US-CA"],
            geo_regions_exclude=["US-CA"],
        )
        violations = geo_overlap_conflicts(targeting)
        assert len(violations) == 2


class TestEdgeCases:
    """Edge cases for geo overlap validation."""

    def test_empty_targeting(self):
        violations = geo_overlap_conflicts(Targeting())
        assert violations == []

    def test_empty_lists_no_overlap(self):
        # geo_countries/geo_countries_exclude have MinLen(1) constraint
        # So "empty" means None (not []) in adcp 3.6.0
        targeting = Targeting(
            geo_countries=None,
            geo_countries_exclude=None,
        )
        violations = geo_overlap_conflicts(targeting)
        assert violations == []

    def test_non_geo_fields_ignored(self):
        targeting = Targeting(
            device_type_any_of=["mobile"],
            content_cat_any_of=["IAB1"],
        )
        violations = geo_overlap_conflicts(targeting)
        assert violations == []
