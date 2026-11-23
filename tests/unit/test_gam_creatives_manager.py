"""
Unit tests for GAMCreativesManager class.

Tests creative validation logic including 1x1 wildcard placeholder handling
and how creative associations resolve their line item.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, call, patch

import pytest

from src.adapters.gam.managers.creatives import GAMCreativesManager
from src.core.exceptions import AdCPConfigurationError
from tests.helpers.gam_client import SoapObject, gam_line_item, stub_gam_client_manager


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


# =============================================================================
# Tests for the native creative template id (GH #1516)
# =============================================================================


def _manager() -> GAMCreativesManager:
    return GAMCreativesManager(MagicMock(), "adv-1")


def _native_asset() -> dict:
    """A native asset the way the manager types it: template variables, no snippet, no media.

    Its package was created with line item 1001, the stand-in GAM order's one line item.
    """
    return {
        "creative_id": "cr_native_1",
        "name": "Native 1",
        "width": 300,
        "height": 250,
        "template_variables": {"headline": "Buy now"},
        "package_assignments": [{"package_id": "pkg_native_1", "weight": 100, "platform_line_item_id": "1001"}],
    }


def _native_placeholder(template_id: int | None = None) -> SoapObject:
    """A 1x1 placeholder as GAM reports it off a line item: native with its template, or programmatic without."""
    placeholder = SoapObject(size=SoapObject(width=1, height=1), creativeSizeType="NATIVE" if template_id else "PIXEL")
    if template_id is not None:
        placeholder["creativeTemplateId"] = template_id
    return placeholder


@pytest.mark.parametrize(
    "placeholders",
    [
        pytest.param([_native_placeholder(987654)], id="one-native-placeholder"),
        pytest.param([_native_placeholder(987654), _native_placeholder(987654)], id="two-line-items-agree"),
        pytest.param([_native_placeholder(987654), _native_placeholder()], id="beside-a-programmatic-1x1"),
    ],
)
def test_the_template_gam_reports_on_the_native_placeholder_builds_the_creative(placeholders):
    """GAM's own answer, the placeholder's creativeTemplateId (an int off the SOAP object), is the template."""
    creative = _manager()._create_gam_creative(_native_asset(), "native", placeholders)

    assert creative is not None
    assert creative["xsi_type"] == "TemplateCreative"
    assert creative["creativeTemplateId"] == "987654"


@pytest.mark.parametrize(
    ("placeholders", "logged"),
    [
        pytest.param([], "found none", id="no-placeholders"),
        pytest.param([_native_placeholder()], "found none", id="programmatic-1x1-only"),
        pytest.param([_native_placeholder(222), _native_placeholder(111)], "found ['111', '222']", id="two-templates"),
    ],
)
def test_no_single_template_on_the_placeholders_is_a_configuration_error(placeholders, logged, caplog):
    """A seller misconfiguration with no fallback id: CONFIGURATION_ERROR naming native_style_id.

    The code carries no details (AdCP 3.2.7 ``L3/error-handling.mdx``), and the template ids are
    the seller's, so they reach the log and never the error a buyer may be answered with.
    """
    with pytest.raises(AdCPConfigurationError) as excinfo:
        _manager()._create_gam_creative(_native_asset(), "native", placeholders)

    assert excinfo.value.field == "native_style_id"
    assert excinfo.value.details is None
    assert f"Native creative cr_native_1: no single GAM creative template, {logged}" in caplog.text


def _manager_over_a_native_line_item(*, template_id: int | None = None) -> tuple[GAMCreativesManager, MagicMock]:
    """A manager over a stand-in GAM client whose order holds one 1x1 (native) line item.

    The line item is 1001, the one ``_native_asset``'s package was created with, which is how
    its placeholders are found. ``template_id`` is what GAM reports on the placeholder, when
    the line item carries one. The returned CreativeService stand-in is what the payload
    assertions read.
    """
    line_item = gam_line_item("prod_native", sizes=((1, 1),))
    if template_id is not None:
        line_item["creativePlaceholders"][0]["creativeTemplateId"] = template_id
    client_manager = stub_gam_client_manager(line_items=(line_item,))
    return GAMCreativesManager(client_manager, "adv-1"), client_manager.get_service("CreativeService")


def test_add_creative_assets_builds_the_template_creative_from_the_line_item_placeholder():
    """The placeholder reaches the native builder: looked up by the line item the package was created with."""
    manager, creative_service = _manager_over_a_native_line_item(template_id=987654)

    statuses = manager.add_creative_assets("9000001", [_native_asset()], datetime.now(UTC))

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


def test_add_creative_assets_reports_the_missing_template_on_the_status():
    """The per-asset handler carries the field into the status and the error into its slot; nothing reaches GAM."""
    manager, creative_service = _manager_over_a_native_line_item()

    statuses = manager.add_creative_assets("9000001", [_native_asset()], datetime.now(UTC))

    assert len(statuses) == 1
    assert statuses[0].status == "failed"
    assert statuses[0].message is not None and "field: native_style_id" in statuses[0].message
    assert isinstance(statuses[0].error, AdCPConfigurationError) and statuses[0].error.details is None
    creative_service.createCreatives.assert_not_called()
