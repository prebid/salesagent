"""Unit tests for GAM schemas (GAMProductConfig + parse helper).

Reconciled in #1239: GAMImplementationConfig (BaseModel, never imported in
src/) replaced by GAMProductConfig (BaseProductConfig, registered as
GoogleAdManager.product_config_class) with adapter_type discriminator and
two fields surfaced by the read-site audit (supported_format_types,
time_zone).
"""

import pytest
from pydantic import ValidationError

from src.adapters.gam.schemas import (
    EXAMPLE_DISPLAY_CONFIG,
    EXAMPLE_VIDEO_CONFIG,
    CreativePlaceholder,
    FrequencyCap,
    GAMProductConfig,
    PlacementTargeting,
    parse_implementation_config,
)


class TestSubmodels:
    """Tests for the nested submodels."""

    def test_creative_placeholder_minimal(self):
        cp = CreativePlaceholder(width=300, height=250)
        assert cp.width == 300
        assert cp.height == 250
        assert cp.expected_creative_count == 1
        assert cp.is_native is False

    def test_frequency_cap_minimal(self):
        fc = FrequencyCap(max_impressions=3, time_unit="DAY")
        assert fc.max_impressions == 3
        assert fc.time_unit == "DAY"
        assert fc.time_range == 1

    def test_placement_targeting_defaults(self):
        pt = PlacementTargeting(placement_id="hero_atf", targeting_name="hero-above-fold")
        assert pt.placement_id == "hero_atf"
        assert pt.targeting == {}


class TestGAMProductConfig:
    """Tests for the canonical GAM per-product config."""

    def test_defaults_validate(self):
        """Empty construction is valid — all fields have safe defaults including the audit-added ones."""
        config = GAMProductConfig()

        assert config.adapter_type == "google_ad_manager"
        assert config.line_item_type == "STANDARD"
        assert config.priority == 8
        assert config.cost_type == "CPM"
        assert config.creative_placeholders == []
        # Audit-added defaults match production-observed behavior
        assert config.supported_format_types == ["display", "video", "native"]
        assert config.time_zone == "America/New_York"

    def test_adapter_type_locked(self):
        """adapter_type Literal rejects other adapter discriminators."""
        with pytest.raises(ValidationError):
            GAMProductConfig(adapter_type="broadstreet")

    def test_extra_field_rejected(self):
        """Inherits extra='forbid' from BaseProductConfig — typos are rejected at the boundary."""
        with pytest.raises(ValidationError):
            GAMProductConfig(unknwon_field="oops")

    def test_round_trip_preserves_discriminator_and_audit_fields(self):
        config = GAMProductConfig.model_validate(EXAMPLE_DISPLAY_CONFIG)
        round_tripped = GAMProductConfig.model_validate(config.model_dump())

        assert round_tripped.adapter_type == "google_ad_manager"
        assert round_tripped.supported_format_types == ["display", "video", "native"]
        assert round_tripped.time_zone == "America/New_York"
        assert round_tripped == config

    def test_video_example_validates(self):
        config = GAMProductConfig.model_validate(EXAMPLE_VIDEO_CONFIG)
        assert config.environment_type == "VIDEO_PLAYER"
        assert config.video_max_duration == 30000
        assert config.skip_offset == 5000

    def test_line_item_type_validation(self):
        with pytest.raises(ValidationError, match="Invalid line_item_type"):
            GAMProductConfig(line_item_type="BOGUS")

    def test_priority_range(self):
        with pytest.raises(ValidationError, match="Priority"):
            GAMProductConfig(priority=99)

    def test_cost_type_validation(self):
        with pytest.raises(ValidationError, match="Invalid cost_type"):
            GAMProductConfig(cost_type="GTM")

    def test_non_guaranteed_automation_validation(self):
        with pytest.raises(ValidationError, match="Invalid non_guaranteed_automation"):
            GAMProductConfig(non_guaranteed_automation="instant")

    def test_supported_format_types_override(self):
        config = GAMProductConfig(supported_format_types=["display"])
        assert config.supported_format_types == ["display"]

    def test_time_zone_override(self):
        config = GAMProductConfig(time_zone="Europe/London")
        assert config.time_zone == "Europe/London"

    def test_nested_creative_placeholders_validate(self):
        config = GAMProductConfig(creative_placeholders=[{"width": 300, "height": 250, "is_native": True}])
        assert isinstance(config.creative_placeholders[0], CreativePlaceholder)
        assert config.creative_placeholders[0].is_native is True

    def test_nested_frequency_caps_validate(self):
        config = GAMProductConfig(frequency_caps=[{"max_impressions": 5, "time_unit": "WEEK", "time_range": 2}])
        assert config.frequency_caps[0].max_impressions == 5

    def test_nested_placement_targeting_validate(self):
        config = GAMProductConfig(
            placement_targeting=[
                {"placement_id": "atf", "targeting_name": "above-fold"},
            ]
        )
        assert isinstance(config.placement_targeting[0], PlacementTargeting)


class TestParseImplementationConfig:
    """Tests for the GAM parse_implementation_config helper.

    Helper returns None on empty/None input (GAM has no useful default —
    most fields are tenant-specific GAM IDs, even though defaults exist).
    """

    def test_none_returns_none(self):
        assert parse_implementation_config(None) is None

    def test_empty_dict_returns_none(self):
        assert parse_implementation_config({}) is None

    def test_valid_dict_returns_validated_model(self):
        config = parse_implementation_config(EXAMPLE_DISPLAY_CONFIG)
        assert isinstance(config, GAMProductConfig)
        assert config.line_item_type == "STANDARD"
        assert config.priority == 8

    def test_invalid_dict_raises(self):
        with pytest.raises(ValidationError):
            parse_implementation_config({**EXAMPLE_DISPLAY_CONFIG, "priority": 99})


class TestRegisteredOnAdapter:
    """Sanity check: GAMProductConfig is reachable through the adapter registry."""

    def test_get_adapter_schemas_returns_gam_product_config(self):
        from src.adapters import get_adapter_schemas

        schemas = get_adapter_schemas("google_ad_manager")
        assert schemas is not None
        assert schemas.product_config is GAMProductConfig

    def test_alias_gam_resolves_to_same_schema(self):
        from src.adapters import get_adapter_schemas

        schemas_alias = get_adapter_schemas("gam")
        assert schemas_alias is not None
        assert schemas_alias.product_config is GAMProductConfig
