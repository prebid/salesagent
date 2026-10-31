"""Tests for v3 structured geo targeting types, serialization, and inheritance.

Covers:
- GeoCountry/GeoRegion (RootModel[str]) construction
- GeoMetro/GeoPostalArea (structured with system enum) construction
- Targeting model_dump JSON safety (regression: Bug A — MetroAreaSystem enum serialization)
- FrequencyCap inheritance from library type, and that no undeclared key (``scope``) survives it
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
from tests.factories import FrequencyCapFactory


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
            # Through the factory because this test DOES assert on max_impressions,
            # which the pin only admits alongside per and window.
            frequency_cap=FrequencyCapFactory.build(max_impressions=5, suppress_minutes=60),
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

    # REMOVED: test_model_dump_internal_json_safe. It dumped through
    # ``model_dump_internal`` and seeded ``key_value_pairs``; neither exists. JSON safety is
    # a property of the document that is SENT, and test_model_dump_json_safe above grades
    # exactly that over the same geo inputs (CLAUDE.md pattern 4 — one serializer seat).

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

    # REMOVED: test_model_dump_excludes_managed_fields and
    # test_model_dump_internal_includes_managed_fields. Both seeded ``key_value_pairs``,
    # which Targeting no longer declares (src/core/schemas/_base.py — the pinned
    # core/targeting.json declares no managed-only field), and the second one graded the
    # deleted ``model_dump_internal`` seat. Keeping a managed field off the wire stopped
    # being a serialization property when the field was deleted: an undeclared key is now
    # refused on construction in dev and dropped in production by
    # ``get_pydantic_extra_mode()`` (CLAUDE.md pattern 7), so there is nothing here to dump.

    def test_model_dump_mode_override(self):
        """Explicit mode='python' still works when caller needs it."""
        from adcp.types.domains.enums.metro_system import MetroAreaSystem

        t = Targeting(geo_metros=[{"system": "nielsen_dma", "values": ["501"]}])
        d = t.model_dump(exclude_none=True, mode="python")
        # In python mode, system is the enum object (a StrEnum member as of adcp 6.6, so it is
        # also a str — assert the enum type rather than "not str" to keep the intent).
        assert isinstance(d["geo_metros"][0]["system"], MetroAreaSystem)


# ---------------------------------------------------------------------------
# FrequencyCap Inheritance
# ---------------------------------------------------------------------------
class TestFrequencyCapInheritance:
    # Every test here is about the SUBCLASS — that it inherits the library type, that
    # ``suppress_minutes`` takes an int or a float, and that no undeclared key survives
    # it. None of them asserts anything about ``max_impressions``, which these fixtures
    # used to carry. They cannot carry it alone: ``core/frequency-cap.json`` makes
    # ``max_impressions`` depend on ``per`` and ``window``, so the old fixtures were
    # documents the pin forbids (salesagent-8qela.120). ``suppress_minutes`` on its
    # own satisfies the schema's anyOf, so dropping the field costs no assertion and
    # leaves each test with only what it grades.
    #
    # THREE TESTS ASSERTING ``scope`` ARE REPLACED by their inverse, because they graded a
    # defect. ``core/frequency-cap.json`` declares five properties and no ``scope``; the pin
    # decides the level POSITIONALLY ("the package flight for a targeting overlay, or the
    # MediaBuy flight for a root cap" — the ``max_impressions`` description), and carries the
    # MediaBuy level as a different field of a different type
    # (``CreateMediaBuyRequest.frequency_cap`` → ``core/media-buy-frequency-cap.json``).
    # ``enums/frequency-cap-scope.json`` exists but its SOLE member is "package" and no schema
    # in the bundle references it, so the old ``test_scope_default_media_buy`` pinned a
    # default the pin's own vocabulary does not contain.
    def test_isinstance_library_freq_cap(self):
        fc = FrequencyCap(suppress_minutes=60)
        assert isinstance(fc, LibraryFrequencyCap)

    def test_scope_is_not_a_field(self):
        """``scope`` is not declared, so it cannot be read back off a cap."""
        assert "scope" not in FrequencyCap.model_fields

    def test_scope_passed_at_construction_never_reaches_the_wire(self):
        """The generated parent is ``extra="allow"``; the subclass's config is what refuses.

        Without ``extra="ignore"`` here, deleting the declared ``scope`` would have PROMOTED
        it from a field that could never be serialized (``exclude=True`` was never on it, but
        a Literal bound it to two values) to an unknown key stored and emitted verbatim.
        """
        fc = FrequencyCap(suppress_minutes=60, scope="package")
        assert fc.model_dump(exclude_none=True) == {"suppress_minutes": 60.0}

    def test_suppress_minutes_accepts_float(self):
        fc = FrequencyCap(suppress_minutes=60.5)
        assert fc.suppress_minutes == 60.5

    def test_suppress_minutes_accepts_int(self):
        fc = FrequencyCap(suppress_minutes=60)
        assert isinstance(fc.suppress_minutes, float)

    def test_freq_cap_in_targeting(self):
        t = Targeting(frequency_cap={"suppress_minutes": 60})
        assert isinstance(t.frequency_cap, FrequencyCap)
        assert isinstance(t.frequency_cap, LibraryFrequencyCap)

    def test_stale_stored_scope_is_dropped_on_readback(self):
        """A targeting overlay persisted before the deletion carries ``"scope": "media_buy"``.

        The field had that DEFAULT, so every stored overlay has the key. The narrowing on
        :class:`Targeting` is what applies the subclass's ``extra="ignore"`` to the slot — drop
        it and the readback puts a non-spec key back on the wire for every stored media buy.
        """
        t = Targeting(frequency_cap={"suppress_minutes": 60, "scope": "media_buy"})
        assert t.frequency_cap.model_dump(exclude_none=True) == {"suppress_minutes": 60.0}
        assert "scope" not in Targeting(**t.model_dump(exclude_none=True)).model_dump(exclude_none=True).get(
            "frequency_cap", {}
        )

    def test_library_instance_fills_the_narrowed_slot(self):
        """``from_attributes`` is what lets a parent-class cap through the narrowing.

        Adapters and the GAM discovery readers build ``adcp.types.FrequencyCap``; pydantic
        refuses a parent instance in a subclass slot by identity without this.
        """
        t = Targeting(frequency_cap=LibraryFrequencyCap(suppress_minutes=90))
        assert isinstance(t.frequency_cap, FrequencyCap)
        assert t.frequency_cap.suppress_minutes == 90.0
