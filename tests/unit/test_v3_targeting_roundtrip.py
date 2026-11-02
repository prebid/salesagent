"""Roundtrip tests for v3 structured geo targeting.

Proves data survives:
- construct -> model_dump -> reconstruct -> model_dump (identity)
- construct -> json.dumps -> json.loads -> reconstruct (DB storage simulation)
- FrequencyCap with scope through dump -> reconstruct cycle
"""

import json

from src.core.schemas import FrequencyCap, Targeting


def _roundtrip(t: Targeting) -> Targeting:
    """Dump a Targeting, reconstruct from dict, return the new instance.

    One dump shape, so no ``internal=`` switch: both helpers used to branch to
    ``model_dump_internal``, which no longer exists (CLAUDE.md pattern 4 — one
    serializer seat).
    """
    return Targeting(**t.model_dump(exclude_none=True))


def _json_roundtrip(t: Targeting) -> Targeting:
    """Simulate DB JSONB storage: model_dump -> json.dumps -> json.loads -> reconstruct."""
    raw = json.loads(json.dumps(t.model_dump(exclude_none=True)))
    return Targeting(**raw)


# ---------------------------------------------------------------------------
# V3 Construct Roundtrip
# ---------------------------------------------------------------------------
class TestV3ConstructRoundtrip:
    def test_full_v3_roundtrip(self):
        """construct -> dump -> reconstruct -> dump matches."""
        t = Targeting(
            geo_countries=["US", "CA"],
            geo_regions=["US-CA", "US-NY"],
            geo_metros=[{"system": "nielsen_dma", "values": ["501", "803"]}],
            geo_postal_areas=[{"system": "us_zip", "values": ["10001"]}],
            geo_countries_exclude=["RU"],
            geo_regions_exclude=["US-TX"],
            geo_metros_exclude=[{"system": "nielsen_dma", "values": ["602"]}],
            geo_postal_areas_exclude=[{"system": "us_zip", "values": ["90210"]}],
            device_type_any_of=["mobile", "desktop"],
            frequency_cap={"max_impressions": 5, "suppress_minutes": 60, "scope": "package"},
        )
        d1 = t.model_dump(exclude_none=True)
        t2 = _roundtrip(t)
        d2 = t2.model_dump(exclude_none=True)
        assert d1 == d2

    def test_geo_metro_roundtrip(self):
        t = Targeting(geo_metros=[{"system": "nielsen_dma", "values": ["501"]}])
        t2 = _roundtrip(t)
        assert t2.geo_metros[0].system.value == "nielsen_dma"
        assert t2.geo_metros[0].values == ["501"]

    def test_geo_postal_area_roundtrip(self):
        t = Targeting(geo_postal_areas=[{"system": "us_zip", "values": ["10001", "90210"]}])
        t2 = _roundtrip(t)
        assert t2.geo_postal_areas[0].system.value == "us_zip"
        assert t2.geo_postal_areas[0].values == ["10001", "90210"]

    def test_exclusion_fields_roundtrip(self):
        t = Targeting(
            geo_countries_exclude=["RU", "CN"],
            geo_metros_exclude=[{"system": "nielsen_dma", "values": ["803"]}],
        )
        t2 = _roundtrip(t)
        d1 = t.model_dump(exclude_none=True)
        d2 = t2.model_dump(exclude_none=True)
        assert d1 == d2

    def test_mixed_targeting_roundtrip(self):
        """Geo + device + freq_cap + audiences all survive roundtrip."""
        t = Targeting(
            geo_countries=["US"],
            geo_metros=[{"system": "nielsen_dma", "values": ["501"]}],
            device_type_any_of=["mobile"],
            browser_any_of=["chrome"],
            audiences_any_of=["seg_123"],
            frequency_cap={"max_impressions": 3, "suppress_minutes": 30},
        )
        t2 = _roundtrip(t)
        d1 = t.model_dump(exclude_none=True)
        d2 = t2.model_dump(exclude_none=True)
        assert d1 == d2


# ---------------------------------------------------------------------------
# DB Storage Simulation
# ---------------------------------------------------------------------------
class TestDBStorageSimulation:
    def test_json_dumps_model_dump(self):
        """json.dumps(t.model_dump()) must succeed — DB write proof."""
        t = Targeting(
            geo_metros=[{"system": "nielsen_dma", "values": ["501"]}],
            geo_postal_areas=[{"system": "us_zip", "values": ["10001"]}],
            geo_countries=["US"],
        )
        result = json.dumps(t.model_dump(exclude_none=True))
        assert isinstance(result, str)

    def test_json_roundtrip(self):
        """json.dumps -> json.loads -> Targeting(**data) -> json.dumps -> match."""
        t = Targeting(
            geo_countries=["US"],
            geo_metros=[{"system": "nielsen_dma", "values": ["501"]}],
            geo_postal_areas=[{"system": "us_zip", "values": ["10001"]}],
        )
        d1 = t.model_dump(exclude_none=True)
        s1 = json.dumps(d1, sort_keys=True)
        t2 = _json_roundtrip(t)
        d2 = t2.model_dump(exclude_none=True)
        s2 = json.dumps(d2, sort_keys=True)
        assert s1 == s2

    # REMOVED: test_model_dump_internal_json_roundtrip. It dumped through
    # ``model_dump_internal`` and seeded ``key_value_pairs``; neither exists. There is one
    # serializer seat now (CLAUDE.md pattern 4), so there is no second dump shape for a
    # roundtrip to disagree about, and test_json_roundtrip above grades the one there is.

    def test_manual_approval_flow(self):
        """Targeting -> model_dump -> store -> Targeting(**raw) -> MediaPackage roundtrip."""
        t = Targeting(
            geo_countries=["US"],
            geo_metros=[{"system": "nielsen_dma", "values": ["501"]}],
            device_type_any_of=["mobile"],
        )
        # Simulate DB write (what media_buy_create does). One serializer seat: the shape
        # that persists is the shape model_dump produces, so this is that call.
        stored = t.model_dump(exclude_none=True)
        stored_json = json.dumps(stored)

        # Simulate DB read + reconstruction
        raw = json.loads(stored_json)
        t_reconstructed = Targeting(**raw)

        assert t_reconstructed.geo_countries[0].root == "US"
        assert t_reconstructed.geo_metros[0].system.value == "nielsen_dma"
        assert t_reconstructed.device_type_any_of == ["mobile"]

    def test_exclusion_survives_json_roundtrip(self):
        t = Targeting(
            geo_countries_exclude=["RU"],
            geo_metros_exclude=[{"system": "nielsen_dma", "values": ["803"]}],
        )
        t2 = _json_roundtrip(t)
        d1 = t.model_dump(exclude_none=True)
        d2 = t2.model_dump(exclude_none=True)
        assert d1 == d2


# ---------------------------------------------------------------------------
# REMOVED: TestLegacyNormalizerRoundtrip (flat country / metro / zip / bare region).
#
# All four constructed a Targeting from a v2 FLAT geo spelling
# (``geo_country_any_of``, ``geo_metro_any_of``, ``geo_zip_any_of``, bare region codes)
# and asserted a normalizer had turned it into the v3 structured field. adcp 3.1.1
# core/targeting.json declares none of those spellings, and Targeting now reshapes
# nothing on the way in (src/core/schemas/_base.py:1697), so every one of them raises
# extra_forbidden. The rejection itself is graded by
# test_unknown_targeting_fields.py::test_v2_flat_field_rejected; the v3 spellings these
# were the legacy half of round-trip in TestV3ConstructRoundtrip above.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# FrequencyCap Roundtrip
# ---------------------------------------------------------------------------
class TestFrequencyCapRoundtrip:
    def test_freq_cap_scope_roundtrip(self):
        """scope='package' survives dump -> reconstruct."""
        t = Targeting(frequency_cap={"max_impressions": 5, "suppress_minutes": 60, "scope": "package"})
        d1 = t.model_dump(exclude_none=True)
        t2 = _roundtrip(t)
        d2 = t2.model_dump(exclude_none=True)
        assert d1["frequency_cap"]["scope"] == "package"
        assert d1 == d2

    def test_freq_cap_suppress_float_roundtrip(self):
        """Float suppress_minutes value survives roundtrip."""
        t = Targeting(frequency_cap={"max_impressions": 3, "suppress_minutes": 45.5, "scope": "media_buy"})
        t2 = _json_roundtrip(t)
        assert t2.frequency_cap.suppress_minutes == 45.5

    def test_freq_cap_json_roundtrip(self):
        """FrequencyCap through JSON storage roundtrip."""
        t = Targeting(frequency_cap={"max_impressions": 10, "suppress_minutes": 120, "scope": "package"})
        t2 = _json_roundtrip(t)
        assert t2.frequency_cap.max_impressions == 10
        assert t2.frequency_cap.suppress_minutes == 120.0
        assert t2.frequency_cap.scope == "package"
        assert isinstance(t2.frequency_cap, FrequencyCap)
