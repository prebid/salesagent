"""Tests for validate_overlay_targeting with v3 field names.

Regression tests for salesagent-9nd: ensures overlay validation works with
v3 structured field names (geo_countries, geo_regions, etc.) without
_any_of/_none_of suffix-stripping.
"""

from types import SimpleNamespace

from src.core.schemas import Targeting
from src.services.targeting_capabilities import (
    validate_overlay_targeting,
    validate_property_targeting_allowed,
)


class TestV3GeoFieldsPassValidation:
    """v3 geo inclusion fields should not produce violations."""

    def test_geo_countries_no_violation(self):
        violations = validate_overlay_targeting(Targeting(geo_countries=["US", "CA"]))
        assert violations == []

    def test_geo_regions_no_violation(self):
        violations = validate_overlay_targeting(Targeting(geo_regions=["US-NY"]))
        assert violations == []

    def test_geo_metros_no_violation(self):
        violations = validate_overlay_targeting(Targeting(geo_metros=[{"system": "nielsen_dma", "values": ["501"]}]))
        assert violations == []

    def test_geo_postal_areas_no_violation(self):
        violations = validate_overlay_targeting(Targeting(geo_postal_areas=[{"system": "us_zip", "values": ["90210"]}]))
        assert violations == []


class TestV3GeoExclusionFieldsValidated:
    """v3 geo exclusion fields must also be validated (not silently ignored)."""

    def test_geo_countries_exclude_no_violation(self):
        violations = validate_overlay_targeting(Targeting(geo_countries_exclude=["RU"]))
        assert violations == []

    def test_geo_regions_exclude_no_violation(self):
        violations = validate_overlay_targeting(Targeting(geo_regions_exclude=["US-TX"]))
        assert violations == []

    def test_geo_metros_exclude_no_violation(self):
        violations = validate_overlay_targeting(
            Targeting(geo_metros_exclude=[{"system": "nielsen_dma", "values": ["501"]}])
        )
        assert violations == []

    def test_geo_postal_areas_exclude_no_violation(self):
        violations = validate_overlay_targeting(
            Targeting(geo_postal_areas_exclude=[{"system": "us_zip", "values": ["90210"]}])
        )
        assert violations == []


class TestManagedOnlyFieldsCaught:
    """Managed-only fields must produce violations."""

    def test_key_value_pairs_violation(self):
        violations = validate_overlay_targeting(Targeting(key_value_pairs={"foo": "bar"}))
        assert len(violations) == 1
        assert "key_value_pairs" in violations[0]
        assert "managed-only" in violations[0]

    def test_mixed_overlay_and_managed(self):
        """Valid overlay fields alongside managed-only should only flag managed-only."""
        violations = validate_overlay_targeting(
            Targeting(geo_countries=["US"], device_type_any_of=["mobile"], key_value_pairs={"foo": "bar"})
        )
        assert len(violations) == 1
        assert "key_value_pairs" in violations[0]


class TestSuffixStrippingRemoved:
    """No _any_of/_none_of suffix-stripping heuristic remains."""

    def test_device_type_any_of_no_violation(self):
        """Fields still using _any_of suffix should work via explicit mapping."""
        violations = validate_overlay_targeting(Targeting(device_type_any_of=["mobile"]))
        assert violations == []

    def test_os_none_of_no_violation(self):
        """Fields using _none_of suffix should work via explicit mapping."""
        violations = validate_overlay_targeting(Targeting(os_none_of=["android"]))
        assert violations == []


class TestEdgeCases:
    """Edge cases for the validation function."""

    def test_empty_targeting_no_violations(self):
        violations = validate_overlay_targeting(Targeting())
        assert violations == []

    def test_frequency_cap_no_violation(self):
        violations = validate_overlay_targeting(Targeting(frequency_cap={"suppress_minutes": 60}))
        assert violations == []

    def test_custom_field_no_violation(self):
        violations = validate_overlay_targeting(Targeting(custom={"key": "value"}))
        assert violations == []


class TestValidatePropertyTargetingAllowed:
    """Regression coverage for validate_property_targeting_allowed.

    Specifically guards against the None-product crash: the update path loads
    the product from the DB and can legitimately get None (deleted product
    referenced by an existing package), so the validator must not assume the
    product attribute is accessible.
    """

    def _make_product(self, *, product_id: str = "prod_1", allowed: bool = False):
        """Minimal stand-in for the Product ORM row — only the attrs the validator reads."""
        return SimpleNamespace(product_id=product_id, property_targeting_allowed=allowed)

    def _make_overlay_with_property_list(self) -> Targeting:
        return Targeting(property_list={"agent_url": "https://gov.example", "list_id": "v1"})

    def test_product_none_returns_none_not_crash(self):
        """N1 regression: None product must not raise AttributeError.

        Reachable when an admin deletes a product referenced by an existing
        package, and the buyer then calls update_media_buy with property_list.
        The validator must let the not-found error surface from a separate
        path rather than crashing with a 500.
        """
        # Pre-fix: this raised AttributeError accessing product.product_id
        result = validate_property_targeting_allowed(None, self._make_overlay_with_property_list())
        assert result is None

    def test_product_none_with_overlay_none_returns_none(self):
        """Defensive: both args None must also be safe."""
        assert validate_property_targeting_allowed(None, None) is None

    def test_overlay_none_returns_none(self):
        """No targeting overlay → no violation regardless of product flag."""
        product = self._make_product(allowed=False)
        assert validate_property_targeting_allowed(product, None) is None

    def test_no_property_list_returns_none(self):
        """Targeting without property_list → no violation."""
        product = self._make_product(allowed=False)
        overlay = Targeting(geo_countries=["US"])
        assert validate_property_targeting_allowed(product, overlay) is None

    def test_allowed_true_returns_none(self):
        """property_targeting_allowed=True → no violation even with property_list."""
        product = self._make_product(allowed=True)
        assert validate_property_targeting_allowed(product, self._make_overlay_with_property_list()) is None

    def test_allowed_false_returns_violation_message(self):
        """property_targeting_allowed=False with property_list → returns message naming the product."""
        product = self._make_product(product_id="prod_X", allowed=False)
        result = validate_property_targeting_allowed(product, self._make_overlay_with_property_list())
        assert result is not None
        assert "prod_X" in result
        assert "property_targeting_allowed=false" in result
