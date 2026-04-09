"""Test helpers for creating AdCP-compliant test objects."""

from __future__ import annotations


def assert_effective_properties_normalized(
    effective: list[dict],
    raw: list[dict],
    expected_selection_type: str,
) -> None:
    """Assert effective_properties is a non-destructive superset of raw profile data.

    Verifies:
    1. Every key/value from the raw profile dict is preserved in the output
    2. selection_type was added with the expected value
    3. Length matches (no entries dropped or added)
    """
    assert len(effective) == len(raw), f"Length mismatch: {len(effective)} != {len(raw)}"
    for i, (eff, orig) in enumerate(zip(effective, raw, strict=True)):
        for key, value in orig.items():
            assert key in eff, f"[{i}] Missing key {key!r} from original"
            assert eff[key] == value, f"[{i}] {key!r}: {eff[key]!r} != {value!r}"
        assert eff.get("selection_type") == expected_selection_type, (
            f"[{i}] selection_type: {eff.get('selection_type')!r} != {expected_selection_type!r}"
        )


from tests.helpers.adcp_factories import (
    create_minimal_product,
    create_product_with_empty_pricing,
    create_test_brand_manifest,
    create_test_creative_asset,
    create_test_format,
    create_test_format_id,
    create_test_media_buy_dict,
    create_test_media_buy_request_dict,
    create_test_package,
    create_test_package_request,
    create_test_package_request_dict,
    create_test_pricing_option,
    create_test_product,
    create_test_property,
    create_test_property_dict,
)

__all__ = [
    # Product factories
    "create_test_product",
    "create_minimal_product",
    "create_product_with_empty_pricing",
    # Format factories
    "create_test_format_id",
    "create_test_format",
    # Property factories
    "create_test_property_dict",
    "create_test_property",
    # Package factories
    "create_test_package",
    "create_test_package_request",
    "create_test_package_request_dict",
    # Media buy factories (dict-based due to schema duplication issues)
    "create_test_media_buy_request_dict",
    "create_test_media_buy_dict",
    # Other object factories
    "create_test_creative_asset",
    "create_test_brand_manifest",
    "create_test_pricing_option",
]
