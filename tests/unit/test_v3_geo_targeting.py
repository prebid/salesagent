"""Tests for v3 structured geo targeting types, serialization, and inheritance.

Covers:
- GeoCountry/GeoRegion (RootModel[str]) construction
- GeoMetro/GeoPostalArea (structured with system enum) construction
- Targeting model_dump JSON safety (regression: Bug A — MetroAreaSystem enum serialization)
- Targeting model_dump_internal JSON safety
- FrequencyCap inheritance from library type + scope field
- Exclusion field construction and serialization
"""

import json

from adcp.types import (
    FrequencyCap as LibraryFrequencyCap,
)
from adcp.types import (
    GeoCountry,
    GeoMetro,
    GeoPostalArea,
    GeoRegion,
    TargetingOverlay,
)

from src.core.schemas import FrequencyCap, Targeting


# ---------------------------------------------------------------------------
# Geo Type Construction
# ---------------------------------------------------------------------------
class TestGeoTypeConstruction:
    def test_geo_country_root_model_string(self):
        c = GeoCountry("US")
        assert c.root == "US"

    def test_geo_region_iso_format(self):
        r = GeoRegion("US-CA")
        assert r.root == "US-CA"

    def test_geo_metro_structured(self):
        m = GeoMetro(system="nielsen_dma", values=["501", "803"])
        assert m.system.value == "nielsen_dma"
        assert m.values == ["501", "803"]

    def test_geo_postal_area_structured(self):
        p = GeoPostalArea(system="us_zip", values=["10001", "90210"])
        assert p.system.value == "us_zip"
        assert p.values == ["10001", "90210"]


# ---------------------------------------------------------------------------
# Targeting V3 Construction
# ---------------------------------------------------------------------------
class TestTargetingV3Construction:
    def test_construct_with_all_v3_geo_fields(self):
        """All 4 inclusion + 4 exclusion geo fields + device + freq_cap."""
        t = Targeting(
            geo_countries=["US", "CA"],
            geo_regions=["US-CA", "US-NY"],
            geo_metros=[{"system": "nielsen_dma", "values": ["501"]}],
            geo_postal_areas=[{"system": "us_zip", "values": ["10001"]}],
            geo_countries_exclude=["RU"],
            geo_regions_exclude=["US-TX"],
            geo_metros_exclude=[{"system": "nielsen_dma", "values": ["803"]}],
            geo_postal_areas_exclude=[{"system": "us_zip", "values": ["90210"]}],
            device_type_any_of=["mobile", "desktop"],
            frequency_cap={"max_impressions": 5, "suppress_minutes": 60},
        )
        assert len(t.geo_countries) == 2
        assert len(t.geo_regions) == 2
        assert len(t.geo_metros) == 1
        assert len(t.geo_postal_areas) == 1
        assert len(t.geo_countries_exclude) == 1
        assert len(t.geo_regions_exclude) == 1
        assert len(t.geo_metros_exclude) == 1
        assert len(t.geo_postal_areas_exclude) == 1
        assert t.device_type_any_of == ["mobile", "desktop"]
        assert t.frequency_cap.max_impressions == 5

    def test_isinstance_targeting_overlay(self):
        t = Targeting(geo_countries=["US"])
        assert isinstance(t, TargetingOverlay)

    def test_exclusion_fields_in_model_dump(self):
        t = Targeting(
            geo_countries=["US"],
            geo_countries_exclude=["RU"],
            geo_metros=[{"system": "nielsen_dma", "values": ["501"]}],
            geo_metros_exclude=[{"system": "nielsen_dma", "values": ["803"]}],
        )
        d = t.model_dump(exclude_none=True)
        assert "geo_countries_exclude" in d
        assert "geo_metros_exclude" in d
        assert d["geo_countries_exclude"] == ["RU"]

    def test_non_geo_fields_unchanged(self):
        """Device, audience, and signal fields preserved through construction."""
        t = Targeting(
            device_type_any_of=["mobile", "ctv"],
            audiences_any_of=["seg_123"],
            content_cat_any_of=["IAB1"],
        )
        assert t.device_type_any_of == ["mobile", "ctv"]
        assert t.audiences_any_of == ["seg_123"]
        assert t.content_cat_any_of == ["IAB1"]


# ---------------------------------------------------------------------------
# Targeting model_dump Serialization (Bug A regression tests)
# ---------------------------------------------------------------------------
class TestTargetingModelDumpSerialization:
    def test_model_dump_json_safe(self):
        """json.dumps(t.model_dump()) must succeed — regression for Bug A."""
        t = Targeting(
            geo_metros=[{"system": "nielsen_dma", "values": ["501"]}],
            geo_postal_areas=[{"system": "us_zip", "values": ["10001"]}],
            geo_countries=["US"],
        )
        d = t.model_dump(exclude_none=True)
        # Must not raise TypeError for enum objects
        json.dumps(d)

    def test_model_dump_internal_json_safe(self):
        """json.dumps(t.model_dump_internal()) must succeed — regression for Bug A."""
        t = Targeting(
            geo_metros=[{"system": "nielsen_dma", "values": ["501"]}],
            geo_postal_areas=[{"system": "us_zip", "values": ["10001"]}],
            geo_countries=["US"],
            key_value_pairs={"k": "v"},
        )
        d = t.model_dump_internal(exclude_none=True)
        json.dumps(d)

    def test_model_dump_geo_country_is_string(self):
        t = Targeting(geo_countries=["US", "CA"])
        d = t.model_dump(exclude_none=True)
        assert d["geo_countries"] == ["US", "CA"]
        assert isinstance(d["geo_countries"][0], str)

    def test_model_dump_geo_metro_system_is_string(self):
        """System field must serialize as string, not MetroAreaSystem enum."""
        t = Targeting(geo_metros=[{"system": "nielsen_dma", "values": ["501"]}])
        d = t.model_dump(exclude_none=True)
        assert isinstance(d["geo_metros"][0]["system"], str)
        assert d["geo_metros"][0]["system"] == "nielsen_dma"

    def test_model_dump_geo_postal_system_is_string(self):
        """System field must serialize as string, not PostalCodeSystem enum."""
        t = Targeting(geo_postal_areas=[{"system": "us_zip", "values": ["10001"]}])
        d = t.model_dump(exclude_none=True)
        assert isinstance(d["geo_postal_areas"][0]["system"], str)
        assert d["geo_postal_areas"][0]["system"] == "us_zip"

    def test_model_dump_exclude_none(self):
        t = Targeting(geo_countries=["US"])
        d = t.model_dump(exclude_none=True)
        assert "geo_regions" not in d
        assert "geo_metros" not in d
        assert "frequency_cap" not in d

    def test_model_dump_excludes_managed_fields(self):
        t = Targeting(geo_countries=["US"], key_value_pairs={"k": "v"})
        d = t.model_dump(exclude_none=True)
        assert "key_value_pairs" not in d

    def test_model_dump_internal_includes_managed_fields(self):
        t = Targeting(geo_countries=["US"], key_value_pairs={"k": "v"})
        d = t.model_dump_internal(exclude_none=True)
        assert "key_value_pairs" in d
        assert d["key_value_pairs"] == {"k": "v"}

    def test_model_dump_mode_override(self):
        """Explicit mode='python' still works when caller needs it."""
        t = Targeting(geo_metros=[{"system": "nielsen_dma", "values": ["501"]}])
        d = t.model_dump(exclude_none=True, mode="python")
        # In python mode, system is the enum object
        assert not isinstance(d["geo_metros"][0]["system"], str)


# ---------------------------------------------------------------------------
# FrequencyCap Inheritance
# ---------------------------------------------------------------------------
class TestFrequencyCapInheritance:
    def test_isinstance_library_freq_cap(self):
        fc = FrequencyCap(max_impressions=5, suppress_minutes=60)
        assert isinstance(fc, LibraryFrequencyCap)

    def test_scope_field_preserved(self):
        fc = FrequencyCap(max_impressions=5, suppress_minutes=60, scope="package")
        assert fc.scope == "package"

    def test_scope_default_media_buy(self):
        fc = FrequencyCap(max_impressions=5, suppress_minutes=60)
        assert fc.scope == "media_buy"

    def test_suppress_minutes_accepts_float(self):
        fc = FrequencyCap(max_impressions=5, suppress_minutes=60.5)
        assert fc.suppress_minutes == 60.5

    def test_suppress_minutes_accepts_int(self):
        fc = FrequencyCap(max_impressions=5, suppress_minutes=60)
        assert isinstance(fc.suppress_minutes, float)

    def test_model_dump_includes_scope(self):
        fc = FrequencyCap(max_impressions=5, suppress_minutes=60, scope="package")
        d = fc.model_dump()
        assert d["scope"] == "package"

    def test_freq_cap_in_targeting(self):
        t = Targeting(frequency_cap={"max_impressions": 5, "suppress_minutes": 60, "scope": "package"})
        assert t.frequency_cap.scope == "package"
        assert isinstance(t.frequency_cap, FrequencyCap)
        assert isinstance(t.frequency_cap, LibraryFrequencyCap)
