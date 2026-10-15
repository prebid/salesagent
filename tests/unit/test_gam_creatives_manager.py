"""
Unit tests for GAMCreativesManager class.

Tests creative validation logic including 1x1 wildcard placeholder handling
and line item matching for creative associations.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, call, patch

import pytest

from src.adapters.gam.managers.creatives import GAMCreativesManager, _extract_product_id_from_package
from src.core.exceptions import AdCPConfigurationError
from tests.helpers.gam_client import gam_line_item, stub_gam_client_manager


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


# =============================================================================
# Tests for the native creative template id (GH #1516)
# =============================================================================

_NATIVE_PACKAGE = "pkg_prod_native_aa11bb22_1"


def _manager() -> GAMCreativesManager:
    return GAMCreativesManager(MagicMock(), "adv-1")


def _native_asset(*packages: str) -> dict:
    """A native asset the way the manager types it: template variables, no snippet, no media.

    Package ids follow production's ``pkg_<product_id>_<hex>_<n>`` shape so the helpers
    that derive the product id from a package id keep working.
    """
    return {
        "creative_id": "cr_native_1",
        "name": "Native 1",
        "width": 300,
        "height": 250,
        "template_variables": {"headline": "Buy now"},
        "package_assignments": [{"package_id": pkg, "weight": 100} for pkg in (packages or (_NATIVE_PACKAGE,))],
    }


@pytest.mark.parametrize(
    ("asset", "native_template_ids"),
    [
        pytest.param(_native_asset(), {_NATIVE_PACKAGE: "987654"}, id="single-package"),
        pytest.param(_native_asset("pkg_a", "pkg_b"), {"pkg_a": "987654", "pkg_b": "987654"}, id="two-packages-agree"),
        pytest.param(
            {**_native_asset(), "package_assignments": [_NATIVE_PACKAGE]},
            {_NATIVE_PACKAGE: "987654"},
            id="legacy-string-assignments",
        ),
    ],
)
def test_configured_native_style_id_becomes_the_creative_template_id(asset, native_template_ids):
    """AC: the configured native_style_id flows to creativeTemplateId on the TemplateCreative."""
    creative = _manager()._create_gam_creative(asset, "native", [], native_template_ids=native_template_ids)

    assert creative is not None
    assert creative["xsi_type"] == "TemplateCreative"
    assert creative["creativeTemplateId"] == "987654"


@pytest.mark.parametrize(
    "native_template_ids",
    [None, {}, {_NATIVE_PACKAGE: ""}, {"pkg_some_other_package": "111"}],
    ids=["no-map", "empty-map", "empty-string", "other-package-only"],
)
def test_missing_native_style_id_raises_configuration_error(native_template_ids):
    """AC: no configured native_style_id is a typed seller misconfiguration, with no fallback id."""
    with pytest.raises(AdCPConfigurationError) as excinfo:
        _manager()._create_gam_creative(_native_asset(), "native", [], native_template_ids=native_template_ids)

    err = excinfo.value
    assert err.field == "native_style_id"
    assert err.details is not None
    assert err.details.creative_id == "cr_native_1"
    assert err.details.package_id == _NATIVE_PACKAGE
    assert err.details.rejected_value is None


def test_packages_that_disagree_on_the_template_raise_configuration_error():
    """One GAM creative carries one template, so two configured templates cannot both be honoured.

    The inputs are deliberately out of order so the assertion pins the sorted ``rejected_value``.
    """
    with pytest.raises(AdCPConfigurationError) as excinfo:
        _manager()._create_gam_creative(
            _native_asset("pkg_a", "pkg_b"), "native", [], native_template_ids={"pkg_a": "222", "pkg_b": "111"}
        )

    err = excinfo.value
    assert err.field == "native_style_id"
    assert err.details is not None
    assert err.details.rejected_value == ["111", "222"]
    assert err.details.package_id is None


def test_package_without_a_template_beside_a_configured_one_raises_configuration_error():
    """One creative serves every package it is assigned to, so a template-less package cannot ride along."""
    with pytest.raises(AdCPConfigurationError) as excinfo:
        _manager()._create_gam_creative(
            _native_asset("pkg_a", "pkg_b"), "native", [], native_template_ids={"pkg_a": "111"}
        )

    err = excinfo.value
    assert err.field == "native_style_id"
    assert err.details is not None
    assert err.details.package_id == "pkg_b"
    assert err.details.rejected_value == ["111"]


def _manager_over_a_native_line_item() -> tuple[GAMCreativesManager, MagicMock]:
    """A manager over a stand-in GAM client whose order holds one 1x1 (native) line item.

    The line item is named after the package's product, which is how association finds it.
    The returned CreativeService stand-in is what the payload assertions read.
    """
    client_manager = stub_gam_client_manager(line_items=(gam_line_item("prod_native", sizes=((1, 1),)),))
    return GAMCreativesManager(client_manager, "adv-1"), client_manager.get_service("CreativeService")


def test_add_creative_assets_builds_the_template_creative_from_the_configured_id():
    manager, creative_service = _manager_over_a_native_line_item()

    statuses = manager.add_creative_assets(
        "9000001", [_native_asset()], datetime.now(UTC), native_template_ids={_NATIVE_PACKAGE: "987654"}
    )

    assert [status.status for status in statuses] == ["approved"]
    creative_service.createCreatives.assert_called_once_with(
        [
            {
                "xsi_type": "TemplateCreative",
                "name": "Native 1",
                "advertiserId": "adv-1",
                "creativeTemplateId": "987654",
                "creativeTemplateVariableValues": [
                    {
                        "uniqueName": "headline",
                        "value": {"xsi_type": "StringCreativeTemplateVariableValue", "value": "Buy now"},
                    }
                ],
            }
        ]
    )


def test_add_creative_assets_reports_the_missing_native_style_id_on_the_status():
    """The per-asset handler carries the field into the status; nothing reaches GAM."""
    manager, creative_service = _manager_over_a_native_line_item()

    statuses = manager.add_creative_assets("9000001", [_native_asset()], datetime.now(UTC))

    assert len(statuses) == 1
    assert statuses[0].status == "failed"
    assert statuses[0].message is not None and "field: native_style_id" in statuses[0].message
    creative_service.createCreatives.assert_not_called()
