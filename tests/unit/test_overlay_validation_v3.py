"""Tests for validate_property_targeting_allowed.

The managed-only-dimension tests that used to share this module went with the function
they tested: the pinned core/targeting.json declares no managed-only concept and no seller
key/value field, so the check and the field are deleted (salesagent-3cs7o.22). An
undeclared targeting field is refused at model construction, and BR-UC-002 grades that
on the wire.
"""

from types import SimpleNamespace

from src.core.schemas import Targeting
from src.services.targeting_capabilities import validate_property_targeting_allowed


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
