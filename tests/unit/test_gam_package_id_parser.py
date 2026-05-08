"""Tests for the GAM package_id → product_id parser.

Covers tescoboy issue #153: the pre-fix parser used
``package_id.split('_')[2]`` which only produced the correct product_id
when the product had zero internal underscores. Any product_id with
an underscore (e.g. ``prod_topbanner_300x250``) collapsed to
``prod_topbanner`` and the placeholder lookup returned nothing — the
local validator then reported a phantom size mismatch even when the
GAM line item was built with the correct placeholder.

The fix anchors on the trailing ``_<8hex>_<idx>`` suffix the package_id
builder always emits (`f"pkg_{product_id}_{secrets.token_hex(4)}_{idx}"`),
treating everything between ``pkg_`` and that suffix as the product_id.
"""

import secrets

import pytest

from src.adapters.gam.managers.creatives import (
    _extract_product_id_from_package,
    _extract_product_id_from_package_id,
)


def _build_package_id(product_id: str, idx: int = 0) -> str:
    """Mirror the format used by media_buy_create."""
    return f"pkg_{product_id}_{secrets.token_hex(4)}_{idx}"


class TestExtractProductIdFromPackageId:
    @pytest.mark.parametrize(
        "product_id",
        [
            "prod_2215c038",  # zero internal underscores (legacy assumption)
            "prod_topbanner_300x250",  # one internal underscore (the #153 case)
            "prod_topbanner_300x250_v2",  # two internal underscores
            "homepage-hero",  # hyphens, no underscores
            "homepage-hero_v3",  # mixed hyphens + underscores
            "p1",  # short id
        ],
    )
    def test_recovers_product_id_for_various_shapes(self, product_id):
        package_id = _build_package_id(product_id)
        assert _extract_product_id_from_package_id(package_id) == product_id

    def test_specific_failure_case_from_issue(self):
        # The exact case from the 2026-05-07 live test that needed a
        # rename workaround.
        package_id = "pkg_prod_topbanner_300x250_a3f9b2c1_0"
        assert _extract_product_id_from_package_id(package_id) == "prod_topbanner_300x250"

    @pytest.mark.parametrize("higher_idx", [1, 7, 99, 1234])
    def test_non_zero_index(self, higher_idx):
        package_id = f"pkg_prod_xyz_{secrets.token_hex(4)}_{higher_idx}"
        assert _extract_product_id_from_package_id(package_id) == "prod_xyz"

    @pytest.mark.parametrize(
        "bad_input",
        [
            "not-a-package",
            "pkg_",
            "pkg_no-trailing-suffix",
            "pkg_prod_xyz",  # missing rand+idx
            "pkg_prod_xyz_NOT_HEX_1",  # rand isn't 8 lowercase hex
            "pkg_prod_xyz_aaaa_1",  # rand too short
        ],
    )
    def test_returns_none_on_mismatch(self, bad_input):
        assert _extract_product_id_from_package_id(bad_input) is None


class TestExtractProductIdFromPackageDelegates:
    """The sibling helper `_extract_product_id_from_package` had the same
    bug; verify it now delegates to the regex parser."""

    def test_delegates_to_anchored_parser(self):
        package_id = _build_package_id("prod_underscored_id")
        assert _extract_product_id_from_package(package_id) == "prod_underscored_id"

    def test_returns_none_for_legacy_line_item_name(self):
        # Some callers pass line item names directly (no `pkg_` prefix);
        # the helper should fail closed so callers fall back to the
        # direct lookup path.
        assert _extract_product_id_from_package("MyLineItem") is None
