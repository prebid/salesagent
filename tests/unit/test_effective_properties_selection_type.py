"""Regression tests for GitHub issue #1162: selection_type inference on inventory profile path.

Product.resolve_publisher_properties must infer selection_type when inventory profile
publisher_properties lack the discriminator required by AdCP 2.13.0+.

These tests construct ORM model instances in memory (no database) and verify
the resolution returns normalized publisher_properties. A profile's selectors name their
own publishers, so no authorized property is consulted and none is passed.
"""

from unittest.mock import MagicMock

from src.core.database.models import Product
from tests.factories.product import authorized_refs


def _make_product_with_profile(publisher_properties: list[dict]) -> MagicMock:
    """Build a mock Product carrying an inventory profile with *publisher_properties*.

    The tests call the real ``Product.resolve_publisher_properties`` on it, so the actual
    inference logic runs without SQLAlchemy instrumentation.
    """
    profile = MagicMock()
    profile.publisher_properties = publisher_properties

    product = MagicMock(spec=Product)
    product.inventory_profile_id = 1
    product.inventory_profile = profile
    product.properties = None
    product.property_ids = None
    product.property_tags = None
    product.tenant = None
    return product


class TestEffectivePropertiesSelectionTypeInference:
    """Regression tests for #1162: selection_type inference on inventory profile path."""

    def test_profile_property_ids_without_selection_type_infers_by_id(self):
        """Profile with property_ids but no selection_type should infer 'by_id'.

        Reproduces #1162: inventory profile created via admin UI 'full JSON' mode
        without selection_type discriminator.
        """
        product = _make_product_with_profile([{"publisher_domain": "example.com", "property_ids": ["homepage"]}])
        effective = Product.resolve_publisher_properties(product, authorized_refs("example.com"))

        assert effective is not None
        assert len(effective) == 1
        assert effective[0]["publisher_domain"] == "example.com"
        assert effective[0]["property_ids"] == ["homepage"]
        assert effective[0]["selection_type"] == "by_id"

    def test_profile_property_tags_without_selection_type_infers_by_tag(self):
        """Profile with property_tags but no selection_type should infer 'by_tag'."""
        product = _make_product_with_profile([{"publisher_domain": "example.com", "property_tags": ["premium"]}])
        effective = Product.resolve_publisher_properties(product, authorized_refs("example.com"))

        assert effective is not None
        assert len(effective) == 1
        assert effective[0]["publisher_domain"] == "example.com"
        assert effective[0]["property_tags"] == ["premium"]
        assert effective[0]["selection_type"] == "by_tag"

    def test_profile_no_ids_no_tags_infers_all(self):
        """Profile with only publisher_domain (no IDs, no tags) should infer 'all'."""
        product = _make_product_with_profile([{"publisher_domain": "example.com"}])
        effective = Product.resolve_publisher_properties(product, authorized_refs("example.com"))

        assert effective is not None
        assert len(effective) == 1
        assert effective[0]["publisher_domain"] == "example.com"
        assert effective[0]["selection_type"] == "all"

    def test_profile_extra_fields_preserved(self):
        """Profile with extra metadata fields should preserve them and infer selection_type.

        Extra fields (property_name, property_type, identifiers) stored on the profile
        are kept — ensure_selection_type is non-destructive, only adds selection_type.
        """
        product = _make_product_with_profile(
            [
                {
                    "publisher_domain": "example.com",
                    "property_ids": ["homepage"],
                    "property_name": "Legacy Name",
                    "property_type": "website",
                    "identifiers": ["old_id"],
                }
            ]
        )
        effective = Product.resolve_publisher_properties(product, authorized_refs("example.com"))

        assert effective is not None
        assert len(effective) == 1
        assert effective[0]["selection_type"] == "by_id"
        assert effective[0]["publisher_domain"] == "example.com"
        assert effective[0]["property_ids"] == ["homepage"]
        # Extra fields preserved — non-destructive normalization
        assert effective[0]["property_name"] == "Legacy Name"
        assert effective[0]["property_type"] == "website"
        assert effective[0]["identifiers"] == ["old_id"]

    def test_profile_with_selection_type_already_present_passes_through(self):
        """Profile with selection_type already present should pass through unchanged."""
        product = _make_product_with_profile(
            [{"publisher_domain": "example.com", "property_tags": ["premium"], "selection_type": "by_tag"}]
        )
        effective = Product.resolve_publisher_properties(product, authorized_refs("example.com"))

        assert effective is not None
        assert len(effective) == 1
        assert effective[0]["publisher_domain"] == "example.com"
        assert effective[0]["property_tags"] == ["premium"]
        assert effective[0]["selection_type"] == "by_tag"

    def test_profile_invalid_property_ids_falls_back_to_all(self):
        """Profile with domain-style property_ids (invalid per ^[a-z0-9_]+$) should fall back to 'all'.

        This reproduces the exact error from #1162: property_ids like 'weather.com'
        contain dots which fail the AdCP PropertyId regex validation.
        """
        product = _make_product_with_profile([{"publisher_domain": "example.com", "property_ids": ["weather.com"]}])
        effective = Product.resolve_publisher_properties(product, authorized_refs("example.com"))

        assert effective is not None
        assert len(effective) == 1
        assert effective[0]["publisher_domain"] == "example.com"
        assert effective[0]["selection_type"] == "all"
        # Original property_ids preserved (non-destructive), but selection_type is "all"
        # because no valid IDs matched ^[a-z0-9_]+$
