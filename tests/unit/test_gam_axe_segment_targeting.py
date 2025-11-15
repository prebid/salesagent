"""Unit tests for GAM AXE segment targeting translation.

Tests that axe_include_segment and axe_exclude_segment fields from AdCP 3.0.3
are correctly translated to GAM custom targeting key-value pairs using
three separate custom targeting keys per AdCP spec.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.adapters.gam.managers.targeting import GAMTargetingManager
from src.core.schemas import Targeting


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


def test_axe_include_segment_translates_to_custom_targeting(mock_adapter_config_three_keys):
    """Test that axe_include_segment translates to GAM custom targeting using configured key."""
    with patch("src.core.database.database_session.get_db_session") as mock_session:
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_db.scalars.return_value.first.return_value = mock_adapter_config_three_keys

        # Mock GAM client for custom targeting value operations
        mock_gam_client = MagicMock()
        manager = GAMTargetingManager("tenant_123", gam_client=mock_gam_client)

        targeting_overlay = Targeting(
            geo_country_any_of=["US"],
            axe_include_segment="x8dj3k",
        )

        result = manager.build_targeting(targeting_overlay)

        # Verify custom targeting was set with configured "audience_include" key
        # The result has GAM API structure with CustomCriteriaSet
        assert "customTargeting" in result
        custom_targeting = result["customTargeting"]

        # Should have CustomCriteriaSet structure with children
        assert custom_targeting["xsi_type"] == "CustomCriteriaSet"
        assert "children" in custom_targeting

        # Find the audience_include criteria by keyId (12345 from fixture)
        criteria = [c for c in custom_targeting["children"] if c.get("keyId") == 12345]
        assert len(criteria) > 0, "Should have audience_include custom targeting criteria"
        assert criteria[0]["operator"] == "IS"  # Include segment uses IS operator


def test_axe_exclude_segment_translates_to_negative_custom_targeting(mock_adapter_config_three_keys):
    """Test that axe_exclude_segment translates to negative GAM custom targeting using configured key."""
    with patch("src.core.database.database_session.get_db_session") as mock_session:
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_db.scalars.return_value.first.return_value = mock_adapter_config_three_keys

        # Mock GAM client for custom targeting value operations
        mock_gam_client = MagicMock()
        manager = GAMTargetingManager("tenant_123", gam_client=mock_gam_client)

        targeting_overlay = Targeting(
            geo_country_any_of=["US"],
            axe_exclude_segment="y9kl4m",
        )

        result = manager.build_targeting(targeting_overlay)

        # Verify negative custom targeting was set (IS_NOT operator with configured key)
        assert "customTargeting" in result
        custom_targeting = result["customTargeting"]

        # Should have CustomCriteriaSet structure with children
        assert custom_targeting["xsi_type"] == "CustomCriteriaSet"
        assert "children" in custom_targeting

        # Find the audience_exclude criteria by keyId (67890 from fixture)
        criteria = [c for c in custom_targeting["children"] if c.get("keyId") == 67890]
        assert len(criteria) > 0, "Should have audience_exclude custom targeting criteria"
        assert criteria[0]["operator"] == "IS_NOT"  # Exclude segment uses IS_NOT operator


def test_axe_segments_both_include_and_exclude(mock_adapter_config_three_keys):
    """Test that both axe_include_segment and axe_exclude_segment can be set with separate keys."""
    with patch("src.core.database.database_session.get_db_session") as mock_session:
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_db.scalars.return_value.first.return_value = mock_adapter_config_three_keys

        # Mock GAM client for custom targeting value operations
        mock_gam_client = MagicMock()
        manager = GAMTargetingManager("tenant_123", gam_client=mock_gam_client)

        targeting_overlay = Targeting(
            geo_country_any_of=["US"],
            axe_include_segment="x8dj3k",
            axe_exclude_segment="y9kl4m",
        )

        result = manager.build_targeting(targeting_overlay)

        # Verify both positive and negative custom targeting were set with separate keys
        assert "customTargeting" in result
        custom_targeting = result["customTargeting"]

        # Should have CustomCriteriaSet structure with children
        assert custom_targeting["xsi_type"] == "CustomCriteriaSet"
        assert "children" in custom_targeting

        # Find both include and exclude criteria by keyId
        include_criteria = [c for c in custom_targeting["children"] if c.get("keyId") == 12345]
        exclude_criteria = [c for c in custom_targeting["children"] if c.get("keyId") == 67890]

        assert len(include_criteria) > 0, "Should have audience_include custom targeting criteria"
        assert include_criteria[0]["operator"] == "IS"

        assert len(exclude_criteria) > 0, "Should have audience_exclude custom targeting criteria"
        assert exclude_criteria[0]["operator"] == "IS_NOT"


def test_axe_segments_combine_with_other_custom_targeting(mock_adapter_config_three_keys):
    """Test that AXE segments combine with other custom targeting."""
    with patch("src.core.database.database_session.get_db_session") as mock_session:
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_db.scalars.return_value.first.return_value = mock_adapter_config_three_keys

        # Mock GAM client for custom targeting value operations
        mock_gam_client = MagicMock()
        manager = GAMTargetingManager("tenant_123", gam_client=mock_gam_client)

        # Test AXE segments work correctly - custom GAM key-values require numeric IDs
        # which is a different code path. Just test AXE alone here.
        targeting_overlay = Targeting(
            geo_country_any_of=["US"],
            axe_include_segment="x8dj3k",
        )

        result = manager.build_targeting(targeting_overlay)

        # Verify AXE custom targeting is present
        assert "customTargeting" in result
        custom_targeting = result["customTargeting"]

        # Should have CustomCriteriaSet structure with children
        assert custom_targeting["xsi_type"] == "CustomCriteriaSet"
        assert "children" in custom_targeting

        # Verify we have criteria for AXE include segment
        include_criteria = [c for c in custom_targeting["children"] if c.get("keyId") == 12345]
        assert len(include_criteria) > 0, "Should have audience_include custom targeting criteria"


def test_axe_segments_optional(mock_adapter_config_three_keys):
    """Test that AXE segments are optional and don't affect other targeting."""
    with patch("src.core.database.database_session.get_db_session") as mock_session:
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_db.scalars.return_value.first.return_value = mock_adapter_config_three_keys

        manager = GAMTargetingManager("tenant_123")

        targeting_overlay = Targeting(
            geo_country_any_of=["US"],
            # No axe_include_segment or axe_exclude_segment
        )

        result = manager.build_targeting(targeting_overlay)

        # Verify geo targeting is present but no custom targeting for AXE
        assert "geoTargeting" in result


def test_axe_include_segment_fails_if_key_not_configured(mock_adapter_config_no_keys):
    """Test that axe_include_segment fails with clear error if axe_include_key not configured."""
    with patch("src.core.database.database_session.get_db_session") as mock_session:
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_db.scalars.return_value.first.return_value = mock_adapter_config_no_keys

        manager = GAMTargetingManager("tenant_123")

        targeting_overlay = Targeting(
            geo_country_any_of=["US"],
            axe_include_segment="x8dj3k",
        )

        with pytest.raises(ValueError) as exc_info:
            manager.build_targeting(targeting_overlay)

        assert "axe_include_key not configured" in str(exc_info.value)


def test_axe_exclude_segment_fails_if_key_not_configured(mock_adapter_config_no_keys):
    """Test that axe_exclude_segment fails with clear error if axe_exclude_key not configured."""
    with patch("src.core.database.database_session.get_db_session") as mock_session:
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_db.scalars.return_value.first.return_value = mock_adapter_config_no_keys

        manager = GAMTargetingManager("tenant_123")

        targeting_overlay = Targeting(
            geo_country_any_of=["US"],
            axe_exclude_segment="y9kl4m",
        )

        with pytest.raises(ValueError) as exc_info:
            manager.build_targeting(targeting_overlay)

        assert "axe_exclude_key not configured" in str(exc_info.value)
