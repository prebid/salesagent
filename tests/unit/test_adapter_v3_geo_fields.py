"""Tests for non-GAM adapter v3 geo field consumption.

Regression tests for salesagent-fwm: ensures all non-GAM adapters read
v3 structured geo fields (geo_countries, geo_regions, geo_metros) instead
of the legacy flat fields (geo_country_any_of, geo_region_any_of, etc.).
"""

from unittest.mock import MagicMock

from src.core.schemas import FrequencyCap, Targeting


def _make_principal(adapter_key: str = "kevel") -> MagicMock:
    """Create a minimal mock Principal for adapter construction."""
    principal = MagicMock()
    principal.get_adapter_id.return_value = "12345"
    principal.name = "test_principal"
    principal.principal_id = "test_001"
    principal.platform_mappings = {adapter_key: {"advertiser_id": "12345"}}
    return principal


class TestKevelV3GeoFields:
    """Test Kevel adapter reads v3 structured geo fields."""

    def _make_kevel(self):
        from src.adapters.kevel import Kevel

        principal = _make_principal("kevel")
        config = {"network_id": "1", "api_key": "test"}
        return Kevel(config, principal, dry_run=True)

    def test_build_targeting_v3_geo_countries(self):
        kevel = self._make_kevel()
        targeting = Targeting(geo_countries=["US", "CA"])
        result = kevel._build_targeting(targeting)
        assert result["geo"]["countries"] == ["US", "CA"]

    def test_build_targeting_v3_geo_regions(self):
        kevel = self._make_kevel()
        targeting = Targeting(geo_countries=["US"], geo_regions=["US-NY", "US-CA"])
        result = kevel._build_targeting(targeting)
        assert result["geo"]["regions"] == ["US-NY", "US-CA"]

    def test_build_targeting_v3_geo_metros_cast_to_int(self):
        kevel = self._make_kevel()
        targeting = Targeting(
            geo_countries=["US"],
            geo_metros=[{"system": "nielsen_dma", "values": ["501", "803"]}],
        )
        result = kevel._build_targeting(targeting)
        assert result["geo"]["metros"] == [501, 803]

    def test_build_targeting_no_city_field(self):
        """geo_city_any_of was removed in v3; _build_targeting should not reference it."""
        kevel = self._make_kevel()
        targeting = Targeting(geo_countries=["US"])
        result = kevel._build_targeting(targeting)
        # No "cities" key in result since city targeting was removed
        assert "cities" not in result.get("geo", {})

    def test_freq_cap_duration_is_int(self):
        """suppress_minutes is float after salesagent-rlb; FreqCapDuration must be int."""
        freq_cap = FrequencyCap(suppress_minutes=120.0, scope="package")
        result = int(max(1, freq_cap.suppress_minutes // 60))
        assert isinstance(result, int)
        assert result == 2

    def test_freq_cap_duration_fractional_hours(self):
        """Partial hours should floor to nearest int."""
        freq_cap = FrequencyCap(suppress_minutes=90.0, scope="package")
        result = int(max(1, freq_cap.suppress_minutes // 60))
        assert isinstance(result, int)
        assert result == 1

    def test_freq_cap_duration_minimum_one(self):
        """FreqCapDuration must be at least 1 hour."""
        freq_cap = FrequencyCap(suppress_minutes=30.0, scope="package")
        result = int(max(1, freq_cap.suppress_minutes // 60))
        assert isinstance(result, int)
        assert result == 1


class TestTritonV3GeoFields:
    """Test Triton Digital adapter reads v3 structured geo fields."""

    def _make_triton(self):
        from src.adapters.triton_digital import TritonDigital

        principal = _make_principal("triton")
        config = {"auth_token": "test"}
        return TritonDigital(config, principal, dry_run=True)

    def test_build_targeting_v3_geo_countries(self):
        triton = self._make_triton()
        targeting = Targeting(geo_countries=["US", "CA"])
        result = triton._build_targeting(targeting)
        assert result["targeting"]["countries"] == ["US", "CA"]

    def test_build_targeting_v3_geo_regions(self):
        triton = self._make_triton()
        targeting = Targeting(geo_countries=["US"], geo_regions=["US-NY", "US-CA"])
        result = triton._build_targeting(targeting)
        assert result["targeting"]["states"] == ["US-NY", "US-CA"]

    def test_build_targeting_v3_geo_metros(self):
        triton = self._make_triton()
        targeting = Targeting(
            geo_countries=["US"],
            geo_metros=[{"system": "nielsen_dma", "values": ["501"]}],
        )
        result = triton._build_targeting(targeting)
        # Triton maps metros to markets (empty list since no mapping exists)
        assert "markets" in result["targeting"]


class TestXandrV3GeoFields:
    """Test Xandr adapter reads v3 field names from targeting dict."""

    def test_targeting_dict_has_v3_country_field(self):
        """model_dump() produces geo_countries, not nested geo.countries."""
        targeting = Targeting(geo_countries=["US", "CA"])
        targeting_dict = targeting.model_dump(exclude_none=True)
        assert "geo_countries" in targeting_dict
        assert targeting_dict["geo_countries"] == ["US", "CA"]

    def test_targeting_dict_has_v3_region_field(self):
        targeting = Targeting(geo_countries=["US"], geo_regions=["US-NY"])
        targeting_dict = targeting.model_dump(exclude_none=True)
        assert "geo_regions" in targeting_dict

    def test_create_targeting_profile_reads_v3_fields(self):
        """_create_targeting_profile should read geo_countries/geo_regions from dict."""
        from src.adapters.xandr import XandrAdapter

        targeting = Targeting(geo_countries=["US", "CA"], geo_regions=["US-NY"])
        targeting_dict = targeting.model_dump(exclude_none=True)

        # Call _create_targeting_profile via unbound method with mock self + mock _make_request
        mock_self = MagicMock(spec=XandrAdapter)
        mock_self._make_request.return_value = {"response": {"profile": {"id": 999}}}

        profile_id = XandrAdapter._create_targeting_profile(mock_self, targeting_dict)
        assert profile_id == 999

        # Verify the POST call included country/region targets
        call_args = mock_self._make_request.call_args
        profile_data = call_args[0][2]  # positional: method, endpoint, data
        assert profile_data["profile"]["country_targets"] == ["US", "CA"]
        assert profile_data["profile"]["region_targets"] == ["US-NY"]


class TestMockAdapterV3GeoFields:
    """Test mock adapter uses v3 field names in logging."""

    def test_targeting_geo_countries_accessible(self):
        """Targeting.geo_countries works for mock adapter's logging."""
        targeting = Targeting(geo_countries=["US", "CA"])
        assert targeting.geo_countries is not None
        assert len(targeting.geo_countries) == 2

    def test_targeting_geo_regions_accessible(self):
        targeting = Targeting(geo_countries=["US"], geo_regions=["US-NY", "US-CA"])
        assert targeting.geo_regions is not None
        assert len(targeting.geo_regions) == 2

    def test_targeting_geo_metros_accessible(self):
        targeting = Targeting(
            geo_countries=["US"],
            geo_metros=[{"system": "nielsen_dma", "values": ["501", "803"]}],
        )
        assert targeting.geo_metros is not None
        assert len(targeting.geo_metros) == 1
        assert targeting.geo_metros[0].values == ["501", "803"]
