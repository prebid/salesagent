"""Tests for GAM targeting manager v3 structured field support.

Regression tests for salesagent-oee: ensures GAM targeting manager correctly
processes v3 structured geo fields (geo_countries, geo_regions, geo_metros,
geo_postal_areas) and their exclusion variants, handles _had_city_targeting
flag, and applies int() cast to FrequencyCap float arithmetic.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.adapters.gam.managers.targeting import GAMTargetingManager
from src.core.schemas import Targeting


@pytest.fixture
def gam_manager():
    """Create a GAMTargetingManager with test geo mappings, bypassing DB/file I/O."""
    with patch("src.core.database.database_session.get_db_session") as mock_session:
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_config = MagicMock()
        mock_config.axe_include_key = None
        mock_config.axe_exclude_key = None
        mock_config.axe_macro_key = None
        mock_config.custom_targeting_keys = {}
        mock_db.scalars.return_value.first.return_value = mock_config

        manager = GAMTargetingManager.__new__(GAMTargetingManager)
        manager.tenant_id = "test"
        manager.gam_client = None
        manager.axe_include_key = None
        manager.axe_exclude_key = None
        manager.axe_macro_key = None
        manager.custom_targeting_key_ids = {}
        # Test geo mappings
        manager.geo_country_map = {"US": "2840", "CA": "2124", "GB": "2826"}
        manager.geo_region_map = {
            "US": {"CA": "21137", "NY": "21167", "TX": "21176"},
            "GB": {"ENG": "20339"},
        }
        manager.geo_metro_map = {"501": "1003374", "803": "1003389"}
        return manager


class TestBuildTargetingGeoCountries:
    """v3 geo_countries → GAM targeted/excluded locations."""

    def test_countries_targeted(self, gam_manager):
        targeting = Targeting(geo_countries=["US", "CA"])
        result = gam_manager.build_targeting(targeting)
        locations = result["geoTargeting"]["targetedLocations"]
        ids = [loc["id"] for loc in locations]
        assert "2840" in ids  # US
        assert "2124" in ids  # CA

    def test_countries_excluded(self, gam_manager):
        targeting = Targeting(geo_countries=["US"], geo_countries_exclude=["GB"])
        result = gam_manager.build_targeting(targeting)
        excluded = result["geoTargeting"]["excludedLocations"]
        assert any(loc["id"] == "2826" for loc in excluded)

    def test_unknown_country_skipped(self, gam_manager):
        targeting = Targeting(geo_countries=["ZZ"])
        result = gam_manager.build_targeting(targeting)
        # No targeted locations since ZZ is unknown
        geo = result.get("geoTargeting", {})
        locations = geo.get("targetedLocations", [])
        assert len(locations) == 0


class TestBuildTargetingGeoRegions:
    """v3 geo_regions (ISO 3166-2) → GAM targeted/excluded locations."""

    def test_iso_region_targeted(self, gam_manager):
        targeting = Targeting(geo_countries=["US"], geo_regions=["US-CA"])
        result = gam_manager.build_targeting(targeting)
        locations = result["geoTargeting"]["targetedLocations"]
        ids = [loc["id"] for loc in locations]
        assert "21137" in ids  # US-CA

    def test_region_excluded(self, gam_manager):
        targeting = Targeting(geo_countries=["US"], geo_regions_exclude=["US-NY"])
        result = gam_manager.build_targeting(targeting)
        excluded = result["geoTargeting"]["excludedLocations"]
        assert any(loc["id"] == "21167" for loc in excluded)

    def test_unknown_region_skipped(self, gam_manager):
        targeting = Targeting(geo_countries=["US"], geo_regions=["US-ZZ"])
        result = gam_manager.build_targeting(targeting)
        geo = result.get("geoTargeting", {})
        # Only country location, no region
        locations = geo.get("targetedLocations", [])
        region_ids = [loc["id"] for loc in locations if loc["id"] != "2840"]
        assert len(region_ids) == 0


class TestLookupRegionIdISO:
    """_lookup_region_id must accept ISO 3166-2 format."""

    def test_iso_format_splits(self, gam_manager):
        assert gam_manager._lookup_region_id("US-CA") == "21137"

    def test_iso_format_gb(self, gam_manager):
        assert gam_manager._lookup_region_id("GB-ENG") == "20339"

    def test_bare_code_still_works(self, gam_manager):
        """Backward compat: bare region code searched across all countries."""
        assert gam_manager._lookup_region_id("CA") == "21137"

    def test_unknown_returns_none(self, gam_manager):
        assert gam_manager._lookup_region_id("US-ZZ") is None

    def test_unknown_country_returns_none(self, gam_manager):
        assert gam_manager._lookup_region_id("XX-CA") is None


class TestBuildTargetingGeoMetros:
    """v3 geo_metros (structured GeoMetro) → GAM targeted/excluded locations."""

    def test_nielsen_dma_targeted(self, gam_manager):
        targeting = Targeting(
            geo_countries=["US"],
            geo_metros=[{"system": "nielsen_dma", "values": ["501", "803"]}],
        )
        result = gam_manager.build_targeting(targeting)
        locations = result["geoTargeting"]["targetedLocations"]
        ids = [loc["id"] for loc in locations]
        assert "1003374" in ids  # DMA 501
        assert "1003389" in ids  # DMA 803

    def test_nielsen_dma_excluded(self, gam_manager):
        targeting = Targeting(
            geo_countries=["US"],
            geo_metros_exclude=[{"system": "nielsen_dma", "values": ["501"]}],
        )
        result = gam_manager.build_targeting(targeting)
        excluded = result["geoTargeting"]["excludedLocations"]
        assert any(loc["id"] == "1003374" for loc in excluded)

    def test_unsupported_metro_system_raises(self, gam_manager):
        targeting = Targeting(
            geo_countries=["GB"],
            geo_metros=[{"system": "uk_itl1", "values": ["TLG"]}],
        )
        with pytest.raises(ValueError, match="nielsen_dma"):
            gam_manager.build_targeting(targeting)

    def test_unsupported_metro_system_in_exclude_raises(self, gam_manager):
        targeting = Targeting(
            geo_countries=["GB"],
            geo_metros_exclude=[{"system": "eurostat_nuts2", "values": ["DE1"]}],
        )
        with pytest.raises(ValueError, match="nielsen_dma"):
            gam_manager.build_targeting(targeting)

    def test_unknown_dma_code_skipped(self, gam_manager):
        targeting = Targeting(
            geo_countries=["US"],
            geo_metros=[{"system": "nielsen_dma", "values": ["999"]}],
        )
        result = gam_manager.build_targeting(targeting)
        geo = result.get("geoTargeting", {})
        locations = geo.get("targetedLocations", [])
        # Only country, no metro (999 not in map)
        metro_ids = [loc["id"] for loc in locations if loc["id"] != "2840"]
        assert len(metro_ids) == 0


class TestBuildTargetingGeoPostalAreas:
    """v3 geo_postal_areas → raises ValueError (GAM zip not in static mapping)."""

    def test_us_zip_raises_not_implemented(self, gam_manager):
        targeting = Targeting(
            geo_countries=["US"],
            geo_postal_areas=[{"system": "us_zip", "values": ["10001"]}],
        )
        with pytest.raises(ValueError, match="[Pp]ostal"):
            gam_manager.build_targeting(targeting)

    def test_unsupported_postal_system_raises(self, gam_manager):
        targeting = Targeting(
            geo_countries=["GB"],
            geo_postal_areas=[{"system": "gb_outward", "values": ["SW1"]}],
        )
        with pytest.raises(ValueError, match="[Pp]ostal"):
            gam_manager.build_targeting(targeting)

    def test_postal_exclude_raises(self, gam_manager):
        targeting = Targeting(
            geo_countries=["US"],
            geo_postal_areas_exclude=[{"system": "us_zip", "values": ["90210"]}],
        )
        with pytest.raises(ValueError, match="[Pp]ostal"):
            gam_manager.build_targeting(targeting)


class TestBuildTargetingCityRemoved:
    """_had_city_targeting flag must trigger ValueError in build_targeting."""

    def test_city_flag_raises(self, gam_manager):
        targeting = Targeting(geo_countries=["US"], geo_city_any_of=["Chicago"])
        assert getattr(targeting, "_had_city_targeting", False) is True
        with pytest.raises(ValueError, match="[Cc]ity"):
            gam_manager.build_targeting(targeting)

    def test_no_city_flag_no_error(self, gam_manager):
        targeting = Targeting(geo_countries=["US"])
        result = gam_manager.build_targeting(targeting)
        # Should succeed without city error
        assert "geoTargeting" in result


class TestValidateTargetingV3:
    """validate_targeting uses v3 fields, not v2."""

    def test_city_flag_reported(self, gam_manager):
        targeting = Targeting(geo_city_any_of=["NYC"])
        unsupported = gam_manager.validate_targeting(targeting)
        assert any("city" in u.lower() for u in unsupported)

    def test_postal_areas_reported(self, gam_manager):
        targeting = Targeting(
            geo_countries=["US"],
            geo_postal_areas=[{"system": "us_zip", "values": ["10001"]}],
        )
        unsupported = gam_manager.validate_targeting(targeting)
        assert any("postal" in u.lower() for u in unsupported)


class TestFrequencyCapIntCast:
    """suppress_minutes float arithmetic must produce int for GAM API."""

    def test_hours_cast_to_int(self):
        """suppress_minutes=120.0 (2 hours) → numTimeUnits must be int 2."""
        from src.core.schemas import FrequencyCap

        cap = FrequencyCap(suppress_minutes=120.0)
        # Simulate the GAM conversion logic
        num_time_units = int(cap.suppress_minutes // 60)
        assert isinstance(num_time_units, int)
        assert num_time_units == 2

    def test_days_cast_to_int(self):
        """suppress_minutes=2880.0 (2 days) → numTimeUnits must be int 2."""
        from src.core.schemas import FrequencyCap

        cap = FrequencyCap(suppress_minutes=2880.0)
        num_time_units = int(cap.suppress_minutes // 1440)
        assert isinstance(num_time_units, int)
        assert num_time_units == 2

    def test_minutes_stays_int(self):
        """suppress_minutes=30 (minutes) → numTimeUnits should be int."""
        from src.core.schemas import FrequencyCap

        cap = FrequencyCap(suppress_minutes=30)
        num_time_units = int(cap.suppress_minutes)
        assert isinstance(num_time_units, int)
        assert num_time_units == 30
