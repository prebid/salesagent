"""Tests for city targeting rejection.

Regression tests for salesagent-hfz: ensures geo_city_any_of/geo_city_none_of
sent in targeting_overlay are caught by validate_overlay_targeting instead of
being silently dropped.

Updated for salesagent-17b: validation now accepts Targeting model directly.
The normalizer consumes geo_city_any_of/geo_city_none_of and sets
had_city_targeting=True — both fields produce a single violation (correct
semantics: city targeting was used, regardless of which field).
"""

from src.core.schemas import Targeting
from src.services.targeting_capabilities import (
    TARGETING_CAPABILITIES,
    get_overlay_dimensions,
    validate_overlay_targeting,
)


class TestCityFieldsRejected:
    """geo_city_any_of and geo_city_none_of must produce violations."""

    def test_geo_city_any_of_violation(self):
        violations = validate_overlay_targeting(Targeting(geo_city_any_of=["New York"]))
        assert len(violations) == 1
        assert "City targeting is not supported" in violations[0]

    def test_geo_city_none_of_violation(self):
        violations = validate_overlay_targeting(Targeting(geo_city_none_of=["Los Angeles"]))
        assert len(violations) == 1
        assert "City targeting is not supported" in violations[0]

    def test_both_city_fields_produce_one_violation(self):
        """Both geo_city fields trigger the same had_city_targeting flag → 1 violation."""
        violations = validate_overlay_targeting(Targeting(geo_city_any_of=["NYC"], geo_city_none_of=["LA"]))
        assert len(violations) == 1

    def test_city_error_mentions_removed(self):
        """Error message should indicate city targeting is removed/not supported."""
        violations = validate_overlay_targeting(Targeting(geo_city_any_of=["NYC"]))
        assert "removed" in violations[0].lower() or "not supported" in violations[0].lower()


class TestCityMixedWithValidFields:
    """Valid overlay fields alongside city fields should only flag city."""

    def test_valid_geo_plus_city_only_city_flagged(self):
        violations = validate_overlay_targeting(Targeting(geo_countries=["US"], geo_city_any_of=["NYC"]))
        assert len(violations) == 1
        assert "City targeting is not supported" in violations[0]

    def test_device_plus_city_only_city_flagged(self):
        violations = validate_overlay_targeting(Targeting(device_type_any_of=["mobile"], geo_city_none_of=["LA"]))
        assert len(violations) == 1
        assert "City targeting is not supported" in violations[0]


class TestGeoCityDimensionRemoved:
    """geo_city dimension should not appear in overlay dimensions."""

    def test_geo_city_not_in_overlay_dimensions(self):
        overlay = get_overlay_dimensions()
        assert "geo_city" not in overlay

    def test_geo_city_access_is_removed(self):
        cap = TARGETING_CAPABILITIES["geo_city"]
        assert cap.access == "removed"
