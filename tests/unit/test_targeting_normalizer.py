"""Tests for Targeting.normalize_legacy_geo() model validator.

Regression tests for salesagent-uca: ensures the legacy normalizer correctly
converts bare region codes to ISO 3166-2, drops v2 keys when v3 present,
and sets had_city_targeting flag for city fields.
"""

from src.core.schemas import Targeting


class TestBareRegionCodeConversion:
    """Bare US state codes must be converted to ISO 3166-2 format."""

    def test_bare_codes_get_us_prefix(self):
        t = Targeting(**{"geo_region_any_of": ["CA", "NY"]})
        assert t.geo_regions is not None
        codes = [r.root if hasattr(r, "root") else str(r) for r in t.geo_regions]
        assert codes == ["US-CA", "US-NY"]

    def test_already_iso_codes_unchanged(self):
        t = Targeting(**{"geo_region_any_of": ["US-CA", "US-NY"]})
        codes = [r.root if hasattr(r, "root") else str(r) for r in t.geo_regions]
        assert codes == ["US-CA", "US-NY"]

    def test_mixed_bare_and_iso(self):
        t = Targeting(**{"geo_region_any_of": ["CA", "US-NY"]})
        codes = [r.root if hasattr(r, "root") else str(r) for r in t.geo_regions]
        assert codes == ["US-CA", "US-NY"]

    def test_exclude_variant_converted(self):
        t = Targeting(**{"geo_region_none_of": ["TX", "FL"]})
        assert t.geo_regions_exclude is not None
        codes = [r.root if hasattr(r, "root") else str(r) for r in t.geo_regions_exclude]
        assert codes == ["US-TX", "US-FL"]


class TestBothPresentGuard:
    """When both v2 and v3 keys present, v2 must be dropped (no model_extra leak)."""

    def test_country_v2_dropped_when_v3_present(self):
        t = Targeting(**{"geo_country_any_of": ["US"], "geo_countries": ["CA"]})
        # v3 preserved
        codes = [c.root if hasattr(c, "root") else str(c) for c in t.geo_countries]
        assert codes == ["CA"]
        # v2 not in model_extra
        assert "geo_country_any_of" not in t.model_extra

    def test_country_exclude_v2_dropped(self):
        t = Targeting(**{"geo_country_none_of": ["RU"], "geo_countries_exclude": ["CN"]})
        codes = [c.root if hasattr(c, "root") else str(c) for c in t.geo_countries_exclude]
        assert codes == ["CN"]
        assert "geo_country_none_of" not in t.model_extra

    def test_region_v2_dropped_when_v3_present(self):
        t = Targeting(**{"geo_region_any_of": ["CA"], "geo_regions": ["US-NY"]})
        codes = [r.root if hasattr(r, "root") else str(r) for r in t.geo_regions]
        assert codes == ["US-NY"]
        assert "geo_region_any_of" not in t.model_extra

    def test_region_exclude_v2_dropped(self):
        t = Targeting(**{"geo_region_none_of": ["TX"], "geo_regions_exclude": ["US-FL"]})
        codes = [r.root if hasattr(r, "root") else str(r) for r in t.geo_regions_exclude]
        assert codes == ["US-FL"]
        assert "geo_region_none_of" not in t.model_extra

    def test_metro_v2_dropped_when_v3_present(self):
        v3_metros = [{"system": "nielsen_dma", "values": ["501"]}]
        t = Targeting(**{"geo_metro_any_of": ["600"], "geo_metros": v3_metros})
        assert len(t.geo_metros) == 1
        assert "geo_metro_any_of" not in t.model_extra

    def test_metro_exclude_v2_dropped(self):
        v3 = [{"system": "nielsen_dma", "values": ["501"]}]
        t = Targeting(**{"geo_metro_none_of": ["600"], "geo_metros_exclude": v3})
        assert len(t.geo_metros_exclude) == 1
        assert "geo_metro_none_of" not in t.model_extra

    def test_zip_v2_dropped_when_v3_present(self):
        v3 = [{"system": "us_zip", "values": ["10001"]}]
        t = Targeting(**{"geo_zip_any_of": ["90210"], "geo_postal_areas": v3})
        assert len(t.geo_postal_areas) == 1
        assert "geo_zip_any_of" not in t.model_extra

    def test_zip_exclude_v2_dropped(self):
        v3 = [{"system": "us_zip", "values": ["90210"]}]
        t = Targeting(**{"geo_zip_none_of": ["10001"], "geo_postal_areas_exclude": v3})
        assert len(t.geo_postal_areas_exclude) == 1
        assert "geo_zip_none_of" not in t.model_extra

    def test_empty_v2_list_also_dropped(self):
        t = Targeting(**{"geo_country_any_of": [], "geo_countries": ["US"]})
        codes = [c.root if hasattr(c, "root") else str(c) for c in t.geo_countries]
        assert codes == ["US"]
        assert "geo_country_any_of" not in t.model_extra

    def test_empty_v2_without_v3_does_not_set_v3(self):
        t = Targeting(**{"geo_country_any_of": []})
        assert t.geo_countries is None


class TestCityTargetingFlag:
    """City fields must set had_city_targeting flag instead of being silently dropped."""

    def test_city_any_of_sets_flag(self):
        t = Targeting(**{"geo_city_any_of": ["Chicago"]})
        assert t.had_city_targeting is True

    def test_city_none_of_sets_flag(self):
        t = Targeting(**{"geo_city_none_of": ["LA"]})
        assert t.had_city_targeting is True

    def test_both_city_fields_set_flag(self):
        t = Targeting(**{"geo_city_any_of": ["NYC"], "geo_city_none_of": ["LA"]})
        assert t.had_city_targeting is True

    def test_no_city_fields_no_flag(self):
        t = Targeting(**{"geo_countries": ["US"]})
        assert t.had_city_targeting is False

    def test_flag_excluded_from_model_dump(self):
        t = Targeting(**{"geo_city_any_of": ["Chicago"], "geo_countries": ["US"]})
        d = t.model_dump()
        assert "had_city_targeting" not in d

    def test_flag_excluded_from_model_dump_internal(self):
        t = Targeting(**{"geo_city_any_of": ["Chicago"], "geo_countries": ["US"]})
        d = t.model_dump_internal()
        assert "had_city_targeting" not in d

    def test_flag_accessible_as_attribute(self):
        t = Targeting(**{"geo_city_any_of": ["NYC"]})
        assert t.had_city_targeting is True


class TestRoundtrip:
    """model_dump → Targeting(**data) should not leak v2 keys."""

    def test_roundtrip_no_v2_keys(self):
        t1 = Targeting(**{"geo_country_any_of": ["US"], "geo_region_any_of": ["CA"]})
        d = t1.model_dump(exclude_none=True)
        # No v2 keys in output
        assert "geo_country_any_of" not in d
        assert "geo_region_any_of" not in d
        # Reconstruct
        t2 = Targeting(**d)
        d2 = t2.model_dump(exclude_none=True)
        assert d2 == d

    def test_roundtrip_city_flag_not_persisted(self):
        t1 = Targeting(**{"geo_city_any_of": ["NYC"], "geo_countries": ["US"]})
        d = t1.model_dump(exclude_none=True)
        assert "had_city_targeting" not in d
        assert "geo_city_any_of" not in d
        # Reconstruct — no flag on the new object
        t2 = Targeting(**d)
        assert t2.had_city_targeting is False
