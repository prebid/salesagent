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
        assignments = ["pkg_prod_abc_123_1", "pkg_prod_def_456_2"]
        result = _extract_package_info(assignments)

        assert result == [
            ("pkg_prod_abc_123_1", 100),  # Default weight
            ("pkg_prod_def_456_2", 100),
        ]

    def test_dict_format_with_weights(self):
        """Dict format with explicit weights."""
        assignments = [
            {"package_id": "pkg_prod_abc_123_1", "weight": 70},
            {"package_id": "pkg_prod_def_456_2", "weight": 30},
        ]
        result = _extract_package_info(assignments)

        assert result == [
            ("pkg_prod_abc_123_1", 70),
            ("pkg_prod_def_456_2", 30),
        ]

    def test_dict_format_missing_weight_defaults(self):
        """Dict format with missing weight defaults to 100."""
        assignments = [
            {"package_id": "pkg_prod_abc_123_1"},  # No weight
            {"package_id": "pkg_prod_def_456_2", "weight": 50},
        ]
        result = _extract_package_info(assignments)

        assert result == [
            ("pkg_prod_abc_123_1", 100),  # Default
            ("pkg_prod_def_456_2", 50),
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
    """Test creative rotation type determination and LICA creation.

    Four tests became the one parametrized test below. Three of them
    (``test_all_default_weights_keeps_even_rotation``,
    ``test_non_default_weight_triggers_manual``,
    ``test_uniform_non_default_weights_trigger_manual``) passed
    ``line_item_service=None`` and asserted LOG STRINGS — "keeping EVEN rotation",
    "will use MANUAL rotation". With no service, production logs its decision and then
    returns at ``if not line_item_service``, a path ``add_creative_assets`` cannot take:
    it always passes the service it just asked the client manager for. So they graded a
    sentence on a dead branch, and none of them observed the one thing that matters —
    whether GAM was actually told to switch.

    What the four differed in was only the SHAPE of the ``any(w != 100)`` condition, so
    that is what is parametrized, and the assertion is now GAM's ``updateLineItems``
    call.
    """

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
        )

    @pytest.mark.parametrize(
        ("weights", "expect_manual"),
        [
            pytest.param([100, 100, 100], False, id="all-default"),
            pytest.param([70, 30], True, id="varying"),
            pytest.param([50], True, id="single-non-default"),
            pytest.param([50, 50], True, id="uniform-non-default"),
        ],
    )
    def test_line_item_rotation_switches_to_manual_for_any_non_default_weight(
        self, creatives_manager, mocker, weights, expect_manual
    ):
        """GAM honours creative weights only under MANUAL rotation (AdCP 2.5, #827).

        So any weight other than the default 100 — one of them, or all of them, or a
        mix — must switch the line item, and an all-default set must leave it alone.
        The line item id is numeric because production binds it as ``int(line_item_id)``,
        the way a GAM id is shaped.
        """
        assets = [
            {
                "creative_id": f"cr_{index}",
                "package_assignments": [{"package_id": "pkg_prod_abc_123_1", "weight": weight}],
            }
            for index, weight in enumerate(weights)
        ]
        line_item_map = {"Campaign - prod_abc": "5551234"}
        line_item = {"id": "5551234", "creativeRotationType": "EVEN"}
        line_item_service = mocker.MagicMock()
        line_item_service.getLineItemsByStatement.return_value = mocker.MagicMock(results=[line_item])

        creatives_manager._update_line_items_for_weighted_creatives(assets, line_item_map, line_item_service)

        if expect_manual:
            line_item_service.updateLineItems.assert_called_once_with(
                [{"id": "5551234", "creativeRotationType": "MANUAL"}]
            )
        else:
            line_item_service.updateLineItems.assert_not_called()


class TestLICACreationActualPayload:
    """Test that LICA creation sends correct payload to GAM API.

    This class absorbed ``TestLICACreationWithWeights``, whose two tests passed
    ``lica_service=None`` and asserted the dry-run log lines "with weight 70" and
    "Would associate creative". There is no dry-run branch, and the obligation those
    two approximated — the weight is on the association when it is not the default,
    and absent when it is — is exactly what the two tests below grade on the real
    call, so they were duplicates rather than coverage.
    """

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
        )

    def test_lica_payload_includes_weight_when_non_default(self, creatives_manager_non_dry_run, mock_lica_service):
        """Verify manualCreativeRotationWeight is set when weight != 100."""
        asset = {
            "creative_id": "cr_1",
            "package_assignments": [
                {"package_id": "pkg_prod_abc_123_1", "weight": 70},
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
                {"package_id": "pkg_prod_abc_123_1", "weight": 100},
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
        )

    def test_string_assignments_work(self, creatives_manager, mocker):
        """String format still associates, at the default weight (so no weight field)."""
        asset = {
            "creative_id": "cr_1",
            # String format: just package IDs
            "package_assignments": ["pkg_prod_abc_123_1"],
        }

        line_item_map = {"Campaign - prod_abc": "li_123"}
        lica_service = mocker.MagicMock()

        creatives_manager._associate_creative_with_line_items(
            gam_creative_id="gam_cr_999",
            asset=asset,
            line_item_map=line_item_map,
            lica_service=lica_service,
        )

        lica_service.createLineItemCreativeAssociations.assert_called_once_with(
            [{"creativeId": "gam_cr_999", "lineItemId": "li_123"}]
        )
