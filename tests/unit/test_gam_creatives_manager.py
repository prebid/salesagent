"""
Unit tests for GAMCreativesManager class.

Tests creative validation logic including 1x1 wildcard placeholder handling
and line item matching for creative associations.
"""

from unittest.mock import MagicMock, call, patch

from src.adapters.gam.managers.creatives import GAMCreativesManager, _extract_product_id_from_package


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
        "package_assignments": ["package_1"],
    }

    # Creative placeholders with 1x1 + template_id (GAM native template)
    creative_placeholders = {
        "package_1": [
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
        "package_assignments": ["package_2"],
    }

    # Creative placeholders with 1x1 only (programmatic/third-party)
    creative_placeholders = {
        "package_2": [
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
        "package_assignments": ["package_3"],
    }

    # Creative placeholders expecting 300x250
    creative_placeholders = {
        "package_3": [
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
        "package_assignments": ["package_4"],
    }

    # Creative placeholders expecting 300x250
    creative_placeholders = {
        "package_4": [
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
        "package_assignments": ["package_5"],
    }

    # Creative placeholders with both standard and 1x1
    creative_placeholders = {
        "package_5": [
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
# Tests for _extract_product_id_from_package helper
# =============================================================================


def test_extract_product_id_from_package_standard_format():
    """Extract product ID from standard package ID format."""
    package_id = "pkg_prod_291a023d_f8d1c060_1"
    result = _extract_product_id_from_package(package_id)
    assert result == "prod_291a023d"


def test_extract_product_id_from_package_different_suffix():
    """Extract product ID from package with different hash suffix."""
    package_id = "pkg_prod_2215c038_63e4864a_2"
    result = _extract_product_id_from_package(package_id)
    assert result == "prod_2215c038"


def test_extract_product_id_from_package_invalid_prefix():
    """Return None for package IDs without pkg_prod_ prefix."""
    package_id = "invalid_package_id"
    result = _extract_product_id_from_package(package_id)
    assert result is None


def test_extract_product_id_from_package_empty_string():
    """Return None for empty package ID."""
    result = _extract_product_id_from_package("")
    assert result is None


# =============================================================================
# Tests for line item matching in _associate_creative_with_line_items
# =============================================================================


def _associate(line_item_map, *, asset, placement_targeting_map=None):
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
        line_item_map=line_item_map,
        lica_service=lica_service,
        placement_targeting_map=placement_targeting_map,
    )
    return lica_service


def test_line_item_matching_exact_match():
    """Line item name exactly equals product_id (default template)."""
    asset = {
        "creative_id": "creative_123",
        "package_assignments": [{"package_id": "pkg_prod_291a023d_f8d1c060_1", "weight": 100}],
    }

    # Line item map where name equals product_id (default template: {product_name})
    lica_service = _associate({"prod_291a023d": "7211798767"}, asset=asset)

    lica_service.createLineItemCreativeAssociations.assert_called_once_with(
        [{"creativeId": "12345", "lineItemId": "7211798767"}]
    )


def test_line_item_matching_ends_with_product_id():
    """Line item name ends with ' - {product_id}' (custom template)."""
    asset = {
        "creative_id": "creative_456",
        "package_assignments": [{"package_id": "pkg_prod_291a023d_f8d1c060_1", "weight": 100}],
    }

    lica_service = _associate({"Campaign Name - prod_291a023d": "7211798768"}, asset=asset)

    lica_service.createLineItemCreativeAssociations.assert_called_once_with(
        [{"creativeId": "12345", "lineItemId": "7211798768"}]
    )


def test_line_item_matching_starts_with_product_id():
    """Line item name starts with '{product_id} ' (alternative template)."""
    asset = {
        "creative_id": "creative_789",
        "package_assignments": [{"package_id": "pkg_prod_291a023d_f8d1c060_1", "weight": 100}],
    }

    lica_service = _associate({"prod_291a023d - Extra Info": "7211798769"}, asset=asset)

    lica_service.createLineItemCreativeAssociations.assert_called_once_with(
        [{"creativeId": "12345", "lineItemId": "7211798769"}]
    )


def test_line_item_matching_no_match_logs_warning():
    """When no line item matches, nothing is associated and a warning is logged."""
    asset = {
        "creative_id": "creative_999",
        "package_assignments": [{"package_id": "pkg_prod_291a023d_f8d1c060_1", "weight": 100}],
    }

    # Line item map with no matching names
    line_item_map = {
        "Completely Different Name": "7211798770",
        "Another Unrelated Name": "7211798771",
    }

    with patch("src.adapters.gam.managers.creatives.logger") as mock_logger:
        lica_service = _associate(line_item_map, asset=asset)

    # Nothing is associated, and the unmatched package is named in the warning
    lica_service.createLineItemCreativeAssociations.assert_not_called()
    mock_logger.warning.assert_called_once_with(
        "Line item not found for package pkg_prod_291a023d_f8d1c060_1. line_item_map has 2 entries"
    )


def test_line_item_matching_multiple_packages():
    """Each package assignment is associated with its own line item, carrying its weight."""
    asset = {
        "creative_id": "creative_multi",
        "package_assignments": [
            {"package_id": "pkg_prod_111111_aaaaaa_1", "weight": 50},
            {"package_id": "pkg_prod_222222_bbbbbb_1", "weight": 50},
        ],
    }

    # Line item map with both products (using exact match format)
    lica_service = _associate({"prod_111111": "1001", "prod_222222": "1002"}, asset=asset)

    # One call per assignment; a non-default weight rides the association as GAM's
    # manualCreativeRotationWeight.
    assert lica_service.createLineItemCreativeAssociations.call_args_list == [
        call([{"creativeId": "12345", "lineItemId": "1001", "manualCreativeRotationWeight": 50}]),
        call([{"creativeId": "12345", "lineItemId": "1002", "manualCreativeRotationWeight": 50}]),
    ]


def test_line_item_matching_takes_the_first_matching_line_item():
    """The first line item in the map that matches ANY strategy wins.

    The three name strategies are tried per LINE ITEM, not per strategy across the
    map, so when two names could both match the same product the map's order decides
    — here the exact-match name comes first and takes it. This pins the behaviour
    rather than the older intention that the more specific " - {product_id}" suffix
    should be preferred; nothing asks GAM for two line items whose names collide this
    way, so the ordering is unspecified rather than wrong.
    """
    asset = {
        "creative_id": "creative_priority",
        "package_assignments": [{"package_id": "pkg_prod_291a023d_f8d1c060_1", "weight": 100}],
    }

    lica_service = _associate(
        {"prod_291a023d": "exact_match_id", "Campaign - prod_291a023d": "ends_with_match_id"},
        asset=asset,
    )

    lica_service.createLineItemCreativeAssociations.assert_called_once_with(
        [{"creativeId": "12345", "lineItemId": "exact_match_id"}]
    )
