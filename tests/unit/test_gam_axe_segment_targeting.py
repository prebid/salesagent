"""Unit tests for GAM AXE segment targeting translation.

Tests that axe_include_segment and axe_exclude_segment fields (AdCP pre-3.0;
deprecated in 3.0.x in favor of TMP provider fields) are correctly translated
to GAM custom targeting key-value pairs using three separate custom targeting
keys per AdCP spec.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.adapters.gam.managers.targeting import GAMTargetingManager
from src.core.exceptions import AdCPCapabilityNotSupportedError
from src.core.schemas import Targeting
from tests.helpers.gam_client import gam_criteria_set, gam_criterion, stub_gam_client_manager


@pytest.fixture
def mock_adapter_config_three_keys():
    """Fixture for mocked adapter config with all three AXE keys configured."""
    mock_config = MagicMock()
    mock_config.axe_include_key = "audience_include"
    mock_config.axe_exclude_key = "audience_exclude"
    mock_config.axe_macro_key = "audience_macro"
    # Mock custom_targeting_keys with ID mappings for the AXE keys and custom keys
    mock_config.custom_targeting_keys = {
        "audience_include": "12345",
        "audience_exclude": "67890",
        "audience_macro": "11111",
        "custom_key1": "22222",  # Custom key for test_axe_segments_combine_with_other_custom_targeting
        "custom_key2": "33333",  # Custom key for test_axe_segments_combine_with_other_custom_targeting
    }
    return mock_config


@pytest.fixture
def mock_adapter_config_no_keys():
    """Fixture for mocked adapter config with no AXE keys configured."""
    mock_config = MagicMock()
    mock_config.axe_include_key = None
    mock_config.axe_exclude_key = None
    mock_config.axe_macro_key = None
    # Empty custom_targeting_keys (no keys synced from GAM)
    mock_config.custom_targeting_keys = {}
    return mock_config


#: Segment and key-value names the GAM stand-in holds, by (key_id, name), and their value IDs.
INCLUDE_SEGMENT, EXCLUDE_SEGMENT, FOOTBALL = 901, 902, 903
GAM_VALUES = {(12345, "x8dj3k"): INCLUDE_SEGMENT, (67890, "y9kl4m"): EXCLUDE_SEGMENT, (22222, "football"): FOOTBALL}


def _custom_targeting_built(adapter_config, targeting_overlay: Targeting) -> dict:
    """The ``customTargeting`` the targeting manager builds for ``targeting_overlay``."""
    with patch("src.core.database.database_session.get_db_session") as mock_session:
        mock_session.return_value.__enter__.return_value.scalars.return_value.first.return_value = adapter_config
        gam_client = stub_gam_client_manager(custom_targeting_values=GAM_VALUES).get_client()
        manager = GAMTargetingManager("tenant_123", gam_client=gam_client)
        return manager.build_targeting(targeting_overlay)["customTargeting"]


def test_axe_include_segment_translates_to_custom_targeting(mock_adapter_config_three_keys):
    """axe_include_segment targets the configured include key (12345 in the fixture)."""
    sent = _custom_targeting_built(
        mock_adapter_config_three_keys, Targeting(geo_countries=["US"], axe_include_segment="x8dj3k")
    )

    assert sent == gam_criteria_set("OR", [gam_criteria_set("AND", [gam_criterion(12345, [INCLUDE_SEGMENT])])])


def test_axe_exclude_segment_translates_to_negative_custom_targeting(mock_adapter_config_three_keys):
    """axe_exclude_segment excludes on the configured exclude key (67890 in the fixture)."""
    sent = _custom_targeting_built(
        mock_adapter_config_three_keys, Targeting(geo_countries=["US"], axe_exclude_segment="y9kl4m")
    )

    assert sent == gam_criteria_set(
        "OR", [gam_criteria_set("AND", [gam_criterion(67890, [EXCLUDE_SEGMENT], "IS_NOT")])]
    )


def test_axe_segments_both_include_and_exclude(mock_adapter_config_three_keys):
    """Both segments are ANDed in one branch, each on its own key."""
    sent = _custom_targeting_built(
        mock_adapter_config_three_keys,
        Targeting(geo_countries=["US"], axe_include_segment="x8dj3k", axe_exclude_segment="y9kl4m"),
    )

    assert sent == gam_criteria_set(
        "OR",
        [
            gam_criteria_set(
                "AND", [gam_criterion(12345, [INCLUDE_SEGMENT]), gam_criterion(67890, [EXCLUDE_SEGMENT], "IS_NOT")]
            )
        ],
    )


def test_axe_segments_combine_with_other_custom_targeting(mock_adapter_config_three_keys):
    """A segment is ANDed with the buyer's own GAM key-values in the same branch."""
    sent = _custom_targeting_built(
        mock_adapter_config_three_keys,
        Targeting(
            geo_countries=["US"],
            custom={"gam": {"key_values": {"22222": "football"}}},
            axe_include_segment="x8dj3k",
        ),
    )

    assert sent == gam_criteria_set(
        "OR",
        [gam_criteria_set("AND", [gam_criterion(22222, [FOOTBALL]), gam_criterion(12345, [INCLUDE_SEGMENT])])],
    )


def test_axe_segments_optional(mock_adapter_config_three_keys):
    """Test that AXE segments are optional and don't affect other targeting."""
    with patch("src.core.database.database_session.get_db_session") as mock_session:
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_db.scalars.return_value.first.return_value = mock_adapter_config_three_keys

        manager = GAMTargetingManager("tenant_123")

        targeting_overlay = Targeting(
            geo_countries=["US"],
            # No axe_include_segment or axe_exclude_segment
        )

        result = manager.build_targeting(targeting_overlay)

        # Verify geo targeting is present but no custom targeting for AXE
        assert "geoTargeting" in result
        assert "customTargeting" not in result


def test_axe_include_segment_fails_if_key_not_configured(mock_adapter_config_no_keys):
    """Test that axe_include_segment fails with clear error if axe_include_key not configured."""
    with patch("src.core.database.database_session.get_db_session") as mock_session:
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_db.scalars.return_value.first.return_value = mock_adapter_config_no_keys

        manager = GAMTargetingManager("tenant_123")

        targeting_overlay = Targeting(
            geo_countries=["US"],
            axe_include_segment="x8dj3k",
        )

        with pytest.raises(AdCPCapabilityNotSupportedError) as exc_info:
            manager.build_targeting(targeting_overlay)


def test_axe_exclude_segment_fails_if_key_not_configured(mock_adapter_config_no_keys):
    """Test that axe_exclude_segment fails with clear error if axe_exclude_key not configured."""
    with patch("src.core.database.database_session.get_db_session") as mock_session:
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_db.scalars.return_value.first.return_value = mock_adapter_config_no_keys

        manager = GAMTargetingManager("tenant_123")

        targeting_overlay = Targeting(
            geo_countries=["US"],
            axe_exclude_segment="y9kl4m",
        )

        with pytest.raises(AdCPCapabilityNotSupportedError) as exc_info:
            manager.build_targeting(targeting_overlay)
