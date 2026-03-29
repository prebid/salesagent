"""Regression tests for GitHub issue #1162: selection_type inference on inventory profile path.

Product.effective_properties must infer selection_type when inventory profile
publisher_properties lack the discriminator required by AdCP 2.13.0+.

These tests construct ORM model instances in memory (no database) and verify
the effective_properties property returns normalized publisher_properties.
"""

from unittest.mock import MagicMock, PropertyMock

from src.core.database.models import Product


def _make_product_with_profile(publisher_properties: list[dict]) -> MagicMock:
    """Build a mock Product whose effective_properties calls the real property logic.

    Uses a MagicMock with the real Product.effective_properties descriptor
    to test the actual inference logic without SQLAlchemy instrumentation.
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

    # Wire the real property descriptor so we test actual production code
    type(product).effective_properties = PropertyMock(side_effect=lambda: Product.effective_properties.fget(product))
    return product


class TestEffectivePropertiesSelectionTypeInference:
    """Regression tests for #1162: selection_type inference on inventory profile path."""

    def test_profile_property_ids_without_selection_type_infers_by_id(self):
        """Profile with property_ids but no selection_type should infer 'by_id'.

        Reproduces #1162: inventory profile created via admin UI 'full JSON' mode
        without selection_type discriminator.
        """
        product = _make_product_with_profile([{"publisher_domain": "example.com", "property_ids": ["homepage"]}])
        effective = product.effective_properties

        assert effective is not None
        assert len(effective) == 1
        assert effective[0]["publisher_domain"] == "example.com"
        assert effective[0]["property_ids"] == ["homepage"]
        assert effective[0]["selection_type"] == "by_id"

    def test_profile_property_tags_without_selection_type_infers_by_tag(self):
        """Profile with property_tags but no selection_type should infer 'by_tag'."""
        product = _make_product_with_profile([{"publisher_domain": "example.com", "property_tags": ["premium"]}])
        effective = product.effective_properties

        assert effective is not None
        assert len(effective) == 1
        assert effective[0]["publisher_domain"] == "example.com"
        assert effective[0]["property_tags"] == ["premium"]
        assert effective[0]["selection_type"] == "by_tag"

    def test_profile_no_ids_no_tags_infers_all(self):
        """Profile with only publisher_domain (no IDs, no tags) should infer 'all'."""
        product = _make_product_with_profile([{"publisher_domain": "example.com"}])
        effective = product.effective_properties

        assert effective is not None
        assert len(effective) == 1
        assert effective[0]["publisher_domain"] == "example.com"
        assert effective[0]["selection_type"] == "all"

    def test_profile_legacy_fields_stripped(self):
        """Profile with legacy extra fields should strip them and infer selection_type.

        Legacy data may contain property_name, property_type, identifiers from
        older admin UI versions or direct DB manipulation.
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
        effective = product.effective_properties

        assert effective is not None
        assert len(effective) == 1
        assert effective[0]["selection_type"] == "by_id"
        assert effective[0]["publisher_domain"] == "example.com"
        assert effective[0]["property_ids"] == ["homepage"]
        # Legacy fields must be stripped
        assert "property_name" not in effective[0]
        assert "property_type" not in effective[0]
        assert "identifiers" not in effective[0]

    def test_profile_with_selection_type_already_present_passes_through(self):
        """Profile with selection_type already present should pass through unchanged."""
        product = _make_product_with_profile(
            [{"publisher_domain": "example.com", "property_tags": ["premium"], "selection_type": "by_tag"}]
        )
        effective = product.effective_properties

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
        effective = product.effective_properties

        assert effective is not None
        assert len(effective) == 1
        assert effective[0]["publisher_domain"] == "example.com"
        assert effective[0]["selection_type"] == "all"
        # Invalid property_ids should be filtered out
        assert "property_ids" not in effective[0]
