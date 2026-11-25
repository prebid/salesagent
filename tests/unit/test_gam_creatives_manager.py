"""
Unit tests for GAMCreativesManager class: how creative associations resolve their line item.
"""

from unittest.mock import MagicMock, call, patch

from src.adapters.gam.managers.creatives import GAMCreativesManager

# =============================================================================
# Tests for line item resolution in _associate_creative_with_line_items
# =============================================================================


def _associate(*, asset, placement_targeting_map=None):
    """Run the association and return the stand-in LICA service it called.

    A line item creative association exists only in GAM, so the LICA service is what
    a unit test stands in for, and the call it receives is what these tests grade:
    WHICH line item the creative was associated with.
    """
    lica_service = MagicMock()
    manager = GAMCreativesManager(MagicMock(), "advertiser_123")
    manager._associate_creative_with_line_items(
        gam_creative_id="12345",
        asset=asset,
        lica_service=lica_service,
        placement_targeting_map=placement_targeting_map,
    )
    return lica_service


def test_line_item_matching_exact_match():
    """The association names the line item the package was created with, whatever its id."""
    asset = {
        "creative_id": "creative_123",
        "package_assignments": [
            {"package_id": "pkg_display_8f2a_1", "weight": 100, "platform_line_item_id": "7211798767"}
        ],
    }

    lica_service = _associate(asset=asset)

    lica_service.createLineItemCreativeAssociations.assert_called_once_with(
        [{"creativeId": "12345", "lineItemId": "7211798767"}]
    )


def test_line_item_matching_no_match_logs_warning():
    """A package with no line item is not associated, and the package is named in the warning."""
    asset = {
        "creative_id": "creative_999",
        "package_assignments": [{"package_id": "pkg_display_8f2a_1", "weight": 100, "platform_line_item_id": None}],
    }

    with patch("src.adapters.gam.managers.creatives.logger") as mock_logger:
        lica_service = _associate(asset=asset)

    lica_service.createLineItemCreativeAssociations.assert_not_called()
    mock_logger.warning.assert_called_once_with("Package pkg_display_8f2a_1 has no GAM line item; assignment skipped")


def test_line_item_matching_multiple_packages():
    """Each package assignment is associated with its own line item, carrying its weight."""
    asset = {
        "creative_id": "creative_multi",
        "package_assignments": [
            {"package_id": "pkg_a_1", "weight": 50, "platform_line_item_id": "1001"},
            {"package_id": "pkg_b_2", "weight": 50, "platform_line_item_id": "1002"},
        ],
    }

    lica_service = _associate(asset=asset)

    # One call per assignment; a non-default weight rides the association as GAM's
    # manualCreativeRotationWeight.
    assert lica_service.createLineItemCreativeAssociations.call_args_list == [
        call([{"creativeId": "12345", "lineItemId": "1001", "manualCreativeRotationWeight": 50}]),
        call([{"creativeId": "12345", "lineItemId": "1002", "manualCreativeRotationWeight": 50}]),
    ]
