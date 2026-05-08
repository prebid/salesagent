"""Tests for GAM creative rotation weight support (AdCP 2.5).

Issue #827: GAM adapter should support creative rotation weights.
"""

import logging

import pytest

from src.adapters.gam.managers.creatives import (
    GAMCreativesManager,
    _extract_package_info,
    _get_package_ids,
)


@pytest.fixture(autouse=True)
def capture_gam_logs(caplog):
    """Ensure GAM creatives manager logs are captured."""
    caplog.set_level(logging.INFO, logger="src.adapters.gam.managers.creatives")


class TestPackageInfoExtraction:
    """Test helper functions for extracting package info from adapter input.

    The adapter receives package_assignments in one of two formats:
    - String format: ["pkg_id1", "pkg_id2"] - weights default to 100
    - Dict format: [{"package_id": "...", "weight": N}] - explicit weights

    Both formats are internal to the adapter interface, not AdCP spec formats.
    AdCP uses CreativeAssignment for weights in update_media_buy.
    """

    def test_string_format_defaults_weight(self):
        """String-only format defaults weight to 100."""
        assignments = ["pkg_prod_abc_a3f9b2c1_1", "pkg_prod_def_b7e2d3a4_2"]
        result = _extract_package_info(assignments)

        assert result == [
            ("pkg_prod_abc_a3f9b2c1_1", 100),  # Default weight
            ("pkg_prod_def_b7e2d3a4_2", 100),
        ]

    def test_dict_format_with_weights(self):
        """Dict format with explicit weights."""
        assignments = [
            {"package_id": "pkg_prod_abc_a3f9b2c1_1", "weight": 70},
            {"package_id": "pkg_prod_def_b7e2d3a4_2", "weight": 30},
        ]
        result = _extract_package_info(assignments)

        assert result == [
            ("pkg_prod_abc_a3f9b2c1_1", 70),
            ("pkg_prod_def_b7e2d3a4_2", 30),
        ]

    def test_dict_format_missing_weight_defaults(self):
        """Dict format with missing weight defaults to 100."""
        assignments = [
            {"package_id": "pkg_prod_abc_a3f9b2c1_1"},  # No weight
            {"package_id": "pkg_prod_def_b7e2d3a4_2", "weight": 50},
        ]
        result = _extract_package_info(assignments)

        assert result == [
            ("pkg_prod_abc_a3f9b2c1_1", 100),  # Default
            ("pkg_prod_def_b7e2d3a4_2", 50),
        ]

    def test_empty_assignments(self):
        """Empty list returns empty result."""
        assert _extract_package_info([]) == []

    def test_mixed_formats_handles_gracefully(self):
        """Mixed formats are unusual but handled gracefully."""
        # In practice all assignments should be same format
        assignments = [
            "pkg_prod_string_1",
            {"package_id": "pkg_prod_dict_2", "weight": 60},
        ]
        result = _extract_package_info(assignments)

        assert result == [
            ("pkg_prod_string_1", 100),
            ("pkg_prod_dict_2", 60),
        ]

    def test_get_package_ids_extracts_only_ids(self):
        """_get_package_ids should return just the IDs, ignoring weights."""
        assignments = [
            {"package_id": "pkg1", "weight": 70},
            {"package_id": "pkg2", "weight": 30},
        ]
        assert _get_package_ids(assignments) == ["pkg1", "pkg2"]

    def test_get_package_ids_string_format(self):
        """_get_package_ids works with string format."""
        assignments = ["pkg1", "pkg2", "pkg3"]
        assert _get_package_ids(assignments) == ["pkg1", "pkg2", "pkg3"]


class TestCreativeRotationLogic:
    """Test creative rotation type determination and LICA creation."""

    @pytest.fixture
    def mock_client_manager(self, mocker):
        """Create a mock GAM client manager."""
        client_manager = mocker.MagicMock()
        client_manager.get_statement_builder.return_value = mocker.MagicMock()
        return client_manager

    @pytest.fixture
    def creatives_manager(self, mock_client_manager):
        """Create a GAMCreativesManager instance for testing."""
        return GAMCreativesManager(
            client_manager=mock_client_manager,
            advertiser_id="12345",
            dry_run=True,  # Use dry run for unit tests
        )

    def test_all_default_weights_keeps_even_rotation(self, creatives_manager, caplog):
        """When all creatives have default weight (100), keep EVEN rotation."""
        assets = [
            {
                "creative_id": "cr_1",
                "package_assignments": [
                    {"package_id": "pkg_prod_abc_a3f9b2c1_1", "weight": 100},
                    {"package_id": "pkg_prod_abc_456_2", "weight": 100},
                ],
            },
            {
                "creative_id": "cr_2",
                "package_assignments": [
                    {"package_id": "pkg_prod_abc_a3f9b2c1_1", "weight": 100},
                ],
            },
        ]

        line_item_map = {"Campaign - prod_abc": "li_123"}

        creatives_manager._update_line_items_for_weighted_creatives(assets, line_item_map, None)

        assert "keeping EVEN rotation" in caplog.text

    def test_varying_weights_triggers_manual_rotation(self, creatives_manager, caplog):
        """When creatives have different weights, switch to MANUAL rotation."""
        assets = [
            {
                "creative_id": "cr_1",
                "package_assignments": [
                    {"package_id": "pkg_prod_abc_a3f9b2c1_1", "weight": 70},
                ],
            },
            {
                "creative_id": "cr_2",
                "package_assignments": [
                    {"package_id": "pkg_prod_abc_a3f9b2c1_1", "weight": 30},
                ],
            },
        ]

        line_item_map = {"Campaign - prod_abc": "li_123"}

        creatives_manager._update_line_items_for_weighted_creatives(assets, line_item_map, None)

        assert "will use MANUAL rotation" in caplog.text
        assert "Would update line item" in caplog.text

    def test_non_default_weight_triggers_manual(self, creatives_manager, caplog):
        """Even a single non-default weight triggers MANUAL rotation."""
        assets = [
            {
                "creative_id": "cr_1",
                "package_assignments": [
                    {"package_id": "pkg_prod_abc_a3f9b2c1_1", "weight": 50},  # Non-default
                ],
            },
        ]

        line_item_map = {"Campaign - prod_abc": "li_123"}

        creatives_manager._update_line_items_for_weighted_creatives(assets, line_item_map, None)

        # Even single non-default weight should trigger MANUAL
        assert "will use MANUAL rotation" in caplog.text

    def test_uniform_non_default_weights_trigger_manual(self, creatives_manager, caplog):
        """Uniform non-default weights (e.g., all 50) should also trigger MANUAL."""
        assets = [
            {
                "creative_id": "cr_1",
                "package_assignments": [
                    {"package_id": "pkg_prod_abc_a3f9b2c1_1", "weight": 50},
                ],
            },
            {
                "creative_id": "cr_2",
                "package_assignments": [
                    {"package_id": "pkg_prod_abc_a3f9b2c1_1", "weight": 50},
                ],
            },
        ]

        line_item_map = {"Campaign - prod_abc": "li_123"}

        creatives_manager._update_line_items_for_weighted_creatives(assets, line_item_map, None)

        # Uniform but non-default should still trigger MANUAL
        assert "will use MANUAL rotation" in caplog.text


class TestLICACreationWithWeights:
    """Test that LICA creation includes weights correctly."""

    @pytest.fixture
    def mock_client_manager(self, mocker):
        """Create a mock GAM client manager."""
        client_manager = mocker.MagicMock()
        client_manager.get_statement_builder.return_value = mocker.MagicMock()
        return client_manager

    @pytest.fixture
    def creatives_manager(self, mock_client_manager):
        """Create a GAMCreativesManager instance for testing."""
        return GAMCreativesManager(
            client_manager=mock_client_manager,
            advertiser_id="12345",
            dry_run=True,
        )

    def test_lica_dry_run_logs_weight(self, creatives_manager, caplog):
        """In dry run, LICA with non-default weight should log the weight."""
        asset = {
            "creative_id": "cr_1",
            "package_assignments": [
                {"package_id": "pkg_prod_abc_a3f9b2c1_1", "weight": 70},
            ],
        }

        line_item_map = {"Campaign - prod_abc": "li_123"}

        creatives_manager._associate_creative_with_line_items(
            gam_creative_id="gam_cr_999",
            asset=asset,
            line_item_map=line_item_map,
            lica_service=None,
        )

        assert "with weight 70" in caplog.text

    def test_lica_dry_run_default_weight_no_extra_log(self, creatives_manager, caplog):
        """Default weight (100) should not log extra weight info."""
        asset = {
            "creative_id": "cr_1",
            "package_assignments": [
                {"package_id": "pkg_prod_abc_a3f9b2c1_1", "weight": 100},
            ],
        }

        line_item_map = {"Campaign - prod_abc": "li_123"}

        creatives_manager._associate_creative_with_line_items(
            gam_creative_id="gam_cr_999",
            asset=asset,
            line_item_map=line_item_map,
            lica_service=None,
        )

        assert "with weight" not in caplog.text
        assert "Would associate creative" in caplog.text


class TestLICACreationActualPayload:
    """Test that LICA creation sends correct payload to GAM API."""

    @pytest.fixture
    def mock_client_manager(self, mocker):
        """Create a mock GAM client manager."""
        client_manager = mocker.MagicMock()
        client_manager.get_statement_builder.return_value = mocker.MagicMock()
        return client_manager

    @pytest.fixture
    def mock_lica_service(self, mocker):
        """Create a mock LICA service."""
        return mocker.MagicMock()

    @pytest.fixture
    def creatives_manager_non_dry_run(self, mock_client_manager):
        """Create a non-dry-run GAMCreativesManager for testing actual API calls."""
        return GAMCreativesManager(
            client_manager=mock_client_manager,
            advertiser_id="12345",
            dry_run=False,
        )

    def test_lica_payload_includes_weight_when_non_default(self, creatives_manager_non_dry_run, mock_lica_service):
        """Verify manualCreativeRotationWeight is set when weight != 100."""
        asset = {
            "creative_id": "cr_1",
            "package_assignments": [
                {"package_id": "pkg_prod_abc_a3f9b2c1_1", "weight": 70},
            ],
        }

        line_item_map = {"Campaign - prod_abc": "li_123"}

        creatives_manager_non_dry_run._associate_creative_with_line_items(
            gam_creative_id="gam_cr_999",
            asset=asset,
            line_item_map=line_item_map,
            lica_service=mock_lica_service,
        )

        # Verify the LICA service was called with correct payload
        mock_lica_service.createLineItemCreativeAssociations.assert_called_once()
        call_args = mock_lica_service.createLineItemCreativeAssociations.call_args[0][0]

        assert len(call_args) == 1
        association = call_args[0]
        assert association["creativeId"] == "gam_cr_999"
        assert association["lineItemId"] == "li_123"
        assert association["manualCreativeRotationWeight"] == 70

    def test_lica_payload_excludes_weight_when_default(self, creatives_manager_non_dry_run, mock_lica_service):
        """Verify manualCreativeRotationWeight is NOT set when weight == 100."""
        asset = {
            "creative_id": "cr_1",
            "package_assignments": [
                {"package_id": "pkg_prod_abc_a3f9b2c1_1", "weight": 100},
            ],
        }

        line_item_map = {"Campaign - prod_abc": "li_123"}

        creatives_manager_non_dry_run._associate_creative_with_line_items(
            gam_creative_id="gam_cr_999",
            asset=asset,
            line_item_map=line_item_map,
            lica_service=mock_lica_service,
        )

        # Verify the LICA service was called
        mock_lica_service.createLineItemCreativeAssociations.assert_called_once()
        call_args = mock_lica_service.createLineItemCreativeAssociations.call_args[0][0]

        assert len(call_args) == 1
        association = call_args[0]
        assert association["creativeId"] == "gam_cr_999"
        assert association["lineItemId"] == "li_123"
        # Weight should NOT be included for default value
        assert "manualCreativeRotationWeight" not in association


class TestBackwardCompatibility:
    """Ensure backward compatibility with legacy formats."""

    @pytest.fixture
    def mock_client_manager(self, mocker):
        """Create a mock GAM client manager."""
        client_manager = mocker.MagicMock()
        return client_manager

    @pytest.fixture
    def creatives_manager(self, mock_client_manager):
        """Create a GAMCreativesManager instance for testing."""
        return GAMCreativesManager(
            client_manager=mock_client_manager,
            advertiser_id="12345",
            dry_run=True,
        )

    def test_string_assignments_work(self, creatives_manager, caplog):
        """String format should still work (with default weight)."""
        asset = {
            "creative_id": "cr_1",
            # String format: just package IDs
            "package_assignments": ["pkg_prod_abc_a3f9b2c1_1"],
        }

        line_item_map = {"Campaign - prod_abc": "li_123"}

        creatives_manager._associate_creative_with_line_items(
            gam_creative_id="gam_cr_999",
            asset=asset,
            line_item_map=line_item_map,
            lica_service=None,
        )

        # Should work without error
        assert "Would associate creative" in caplog.text
