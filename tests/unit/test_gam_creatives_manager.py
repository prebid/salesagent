"""
Unit tests for GAMCreativesManager class.

Tests creative validation logic including 1x1 wildcard placeholder handling
and how creative associations resolve their line item.
"""

from unittest.mock import MagicMock, call, patch

from src.adapters.gam.managers.creatives import GAMCreativesManager


def test_1x1_placeholder_accepts_any_creative_size_native_template():
    """1x1 placeholder with template_id should accept any creative size."""
    # Setup manager
    client_manager = MagicMock()
    manager = GAMCreativesManager(client_manager, "advertiser_123")

    # Mock asset with native creative dimensions
    asset = {
        "creative_id": "creative_123",
        "format": "native",
        "width": 1200,
        "height": 627,
        "package_assignments": [{"package_id": "package_1", "weight": 100, "platform_line_item_id": "li_1"}],
    }

    # Creative placeholders with 1x1 + template_id (GAM native template)
    creative_placeholders = {
        "li_1": [
            {
                "size": {"width": 1, "height": 1},
                "creativeTemplateId": 12345678,
                "expectedCreativeCount": 1,
            }
        ]
    }

    # Should not return any validation errors
    errors = manager._validate_creative_size_against_placeholders(asset, creative_placeholders)
    assert errors == []


def test_1x1_placeholder_accepts_any_creative_size_programmatic():
    """1x1 placeholder without template_id should accept any creative size (programmatic)."""
    client_manager = MagicMock()
    manager = GAMCreativesManager(client_manager, "advertiser_123")

    # Mock asset with standard display dimensions
    asset = {
        "creative_id": "creative_456",
        "format": "display",
        "width": 300,
        "height": 250,
        "third_party_url": "https://example.com/ad",
        "package_assignments": [{"package_id": "package_2", "weight": 100, "platform_line_item_id": "li_2"}],
    }

    # Creative placeholders with 1x1 only (programmatic/third-party)
    creative_placeholders = {
        "li_2": [
            {
                "size": {"width": 1, "height": 1},
                "expectedCreativeCount": 1,
            }
        ]
    }

    # Should not return any validation errors
    errors = manager._validate_creative_size_against_placeholders(asset, creative_placeholders)
    assert errors == []


def test_standard_placeholder_requires_exact_match():
    """Non-1x1 placeholders should require exact dimension match."""
    client_manager = MagicMock()
    manager = GAMCreativesManager(client_manager, "advertiser_123")

    # Mock asset with wrong dimensions
    asset = {
        "creative_id": "creative_789",
        "format": "display",
        "width": 728,
        "height": 90,
        "package_assignments": [{"package_id": "package_3", "weight": 100, "platform_line_item_id": "li_3"}],
    }

    # Creative placeholders expecting 300x250
    creative_placeholders = {
        "li_3": [
            {
                "size": {"width": 300, "height": 250},
                "creativeSizeType": "PIXEL",
                "expectedCreativeCount": 1,
            }
        ]
    }

    # Should return validation error
    errors = manager._validate_creative_size_against_placeholders(asset, creative_placeholders)
    assert len(errors) == 1
    assert "728x90" in errors[0]
    assert "300x250" in errors[0]


def test_standard_placeholder_accepts_exact_match():
    """Non-1x1 placeholders should accept exact dimension match."""
    client_manager = MagicMock()
    manager = GAMCreativesManager(client_manager, "advertiser_123")

    # Mock asset with correct dimensions
    asset = {
        "creative_id": "creative_999",
        "format": "display",
        "width": 300,
        "height": 250,
        "package_assignments": [{"package_id": "package_4", "weight": 100, "platform_line_item_id": "li_4"}],
    }

    # Creative placeholders expecting 300x250
    creative_placeholders = {
        "li_4": [
            {
                "size": {"width": 300, "height": 250},
                "creativeSizeType": "PIXEL",
                "expectedCreativeCount": 1,
            }
        ]
    }

    # Should not return any validation errors
    errors = manager._validate_creative_size_against_placeholders(asset, creative_placeholders)
    assert errors == []


def test_1x1_takes_priority_over_other_sizes():
    """When multiple placeholders exist, 1x1 should match first."""
    client_manager = MagicMock()
    manager = GAMCreativesManager(client_manager, "advertiser_123")

    # Mock asset that doesn't match 300x250 but should match 1x1
    asset = {
        "creative_id": "creative_111",
        "format": "display",
        "width": 728,
        "height": 90,
        "package_assignments": [{"package_id": "package_5", "weight": 100, "platform_line_item_id": "li_5"}],
    }

    # Creative placeholders with both standard and 1x1
    creative_placeholders = {
        "li_5": [
            {
                "size": {"width": 300, "height": 250},
                "creativeSizeType": "PIXEL",
                "expectedCreativeCount": 1,
            },
            {
                "size": {"width": 1, "height": 1},
                "expectedCreativeCount": 1,
            },
        ]
    }

    # Should not return any validation errors (matches 1x1)
    errors = manager._validate_creative_size_against_placeholders(asset, creative_placeholders)
    assert errors == []


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
