"""Unit tests for Broadstreet config schema."""

import pytest

from src.adapters.broadstreet.config_schema import (
    BroadstreetImplementationConfig,
    CreativeSize,
    ZoneTargeting,
    parse_implementation_config,
)


class TestCreativeSize:
    """Tests for CreativeSize model."""

    def test_valid_creative_size(self):
        """Test creating a valid creative size."""
        size = CreativeSize(width=300, height=250)

        assert size.width == 300
        assert size.height == 250
        assert size.expected_count == 1

    def test_creative_size_with_expected_count(self):
        """Test creative size with custom expected count."""
        size = CreativeSize(width=728, height=90, expected_count=3)

        assert size.expected_count == 3

    def test_creative_size_requires_positive_count(self):
        """Test that expected_count must be positive."""
        with pytest.raises(ValueError):
            CreativeSize(width=300, height=250, expected_count=0)


class TestZoneTargeting:
    """Tests for ZoneTargeting model."""

    def test_minimal_zone_targeting(self):
        """Test creating zone targeting with minimal fields."""
        zone = ZoneTargeting(zone_id="zone_123")

        assert zone.zone_id == "zone_123"
        assert zone.zone_name is None
        assert zone.sizes == []
        assert zone.position is None

    def test_full_zone_targeting(self):
        """Test creating zone targeting with all fields."""
        zone = ZoneTargeting(
            zone_id="zone_123",
            zone_name="Top Banner",
            sizes=[
                CreativeSize(width=728, height=90),
                CreativeSize(width=300, height=250),
            ],
            position="above_fold",
        )

        assert zone.zone_id == "zone_123"
        assert zone.zone_name == "Top Banner"
        assert len(zone.sizes) == 2
        assert zone.position == "above_fold"


class TestBroadstreetImplementationConfig:
    """Tests for BroadstreetImplementationConfig model."""

    def test_default_config(self):
        """Test default configuration values."""
        config = BroadstreetImplementationConfig()

        assert config.targeted_zone_ids == []
        assert config.zone_targeting == []
        assert config.campaign_name_template == "AdCP-{po_number}-{product_name}"
        assert config.cost_type == "CPM"
        assert config.delivery_rate == "EVEN"
        assert config.frequency_cap is None
        assert config.ad_format == "display"
        assert config.automation_mode == "manual"

    def test_config_with_zones(self):
        """Test configuration with zone IDs."""
        config = BroadstreetImplementationConfig(
            targeted_zone_ids=["zone_1", "zone_2"],
        )

        assert config.targeted_zone_ids == ["zone_1", "zone_2"]
        # get_zone_ids returns unique zone IDs - order not guaranteed
        assert set(config.get_zone_ids()) == {"zone_1", "zone_2"}

    def test_config_with_zone_targeting(self):
        """Test configuration with detailed zone targeting."""
        config = BroadstreetImplementationConfig(
            zone_targeting=[
                ZoneTargeting(
                    zone_id="zone_3",
                    zone_name="Sidebar",
                    sizes=[CreativeSize(width=300, height=250)],
                ),
            ],
        )

        zone_ids = config.get_zone_ids()
        assert "zone_3" in zone_ids

    def test_get_zone_ids_combines_both_sources(self):
        """Test that get_zone_ids combines targeted_zone_ids and zone_targeting."""
        config = BroadstreetImplementationConfig(
            targeted_zone_ids=["zone_1", "zone_2"],
            zone_targeting=[
                ZoneTargeting(zone_id="zone_3"),
                ZoneTargeting(zone_id="zone_1"),  # Duplicate
            ],
        )

        zone_ids = config.get_zone_ids()

        # Should have unique zone IDs from both sources
        assert len(zone_ids) == 3
        assert set(zone_ids) == {"zone_1", "zone_2", "zone_3"}

    def test_cost_type_validation_cpm(self):
        """Test valid CPM cost type."""
        config = BroadstreetImplementationConfig(cost_type="cpm")
        assert config.cost_type == "CPM"

    def test_cost_type_validation_flat_rate(self):
        """Test valid FLAT_RATE cost type."""
        config = BroadstreetImplementationConfig(cost_type="flat_rate")
        assert config.cost_type == "FLAT_RATE"

    def test_cost_type_validation_invalid(self):
        """Test invalid cost type raises error."""
        with pytest.raises(ValueError) as exc_info:
            BroadstreetImplementationConfig(cost_type="invalid")

        assert "Invalid cost_type" in str(exc_info.value)

    def test_delivery_rate_validation(self):
        """Test delivery rate validation."""
        config = BroadstreetImplementationConfig(delivery_rate="frontloaded")
        assert config.delivery_rate == "FRONTLOADED"

    def test_delivery_rate_validation_invalid(self):
        """Test invalid delivery rate raises error."""
        with pytest.raises(ValueError) as exc_info:
            BroadstreetImplementationConfig(delivery_rate="invalid")

        assert "Invalid delivery_rate" in str(exc_info.value)

    def test_ad_format_validation(self):
        """Test ad format validation."""
        config = BroadstreetImplementationConfig(ad_format="HTML")
        assert config.ad_format == "html"

    def test_ad_format_validation_invalid(self):
        """Test invalid ad format raises error."""
        with pytest.raises(ValueError) as exc_info:
            BroadstreetImplementationConfig(ad_format="video")

        assert "Invalid ad_format" in str(exc_info.value)

    def test_automation_mode_validation(self):
        """Test automation mode validation."""
        config = BroadstreetImplementationConfig(automation_mode="AUTOMATIC")
        assert config.automation_mode == "automatic"

    def test_automation_mode_validation_invalid(self):
        """Test invalid automation mode raises error."""
        with pytest.raises(ValueError) as exc_info:
            BroadstreetImplementationConfig(automation_mode="invalid")

        assert "Invalid automation_mode" in str(exc_info.value)

    def test_get_creative_sizes_for_zone(self):
        """Test getting creative sizes for a specific zone."""
        config = BroadstreetImplementationConfig(
            creative_sizes=[CreativeSize(width=728, height=90)],
            zone_targeting=[
                ZoneTargeting(
                    zone_id="zone_special",
                    sizes=[CreativeSize(width=300, height=250)],
                ),
            ],
        )

        # Zone with specific sizes
        sizes = config.get_creative_sizes_for_zone("zone_special")
        assert len(sizes) == 1
        assert sizes[0].width == 300

        # Zone without specific sizes falls back to global
        sizes = config.get_creative_sizes_for_zone("zone_other")
        assert len(sizes) == 1
        assert sizes[0].width == 728


class TestParseImplementationConfig:
    """Tests for parse_implementation_config function."""

    def test_parse_none_returns_default(self):
        """Test parsing None returns default config."""
        config = parse_implementation_config(None)

        assert isinstance(config, BroadstreetImplementationConfig)
        assert config.cost_type == "CPM"

    def test_parse_empty_dict_returns_default(self):
        """Test parsing empty dict returns default config."""
        config = parse_implementation_config({})

        assert isinstance(config, BroadstreetImplementationConfig)

    def test_parse_valid_dict(self):
        """Test parsing valid config dict."""
        config = parse_implementation_config(
            {
                "targeted_zone_ids": ["zone_1"],
                "cost_type": "FLAT_RATE",
                "ad_format": "html",
            }
        )

        assert config.targeted_zone_ids == ["zone_1"]
        assert config.cost_type == "FLAT_RATE"
        assert config.ad_format == "html"

    def test_parse_nested_zone_targeting(self):
        """Test parsing config with nested zone targeting."""
        config = parse_implementation_config(
            {
                "zone_targeting": [
                    {
                        "zone_id": "zone_1",
                        "zone_name": "Banner",
                        "sizes": [{"width": 300, "height": 250}],
                    }
                ],
            }
        )

        assert len(config.zone_targeting) == 1
        assert config.zone_targeting[0].zone_id == "zone_1"
        assert len(config.zone_targeting[0].sizes) == 1
