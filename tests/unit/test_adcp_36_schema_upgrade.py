"""Schema boundary tests for adcp 3.6.0 upgrade (salesagent-83o).

These tests define the expected behavior AFTER the upgrade to adcp 3.6.0.
They fail on 3.2.0 and must pass on 3.6.0 once our local schemas are aligned.

Covers the Creative.variants boundary matrix from the design field,
the Pagination cursor-based structure, and Property identifier/type requirement.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


class TestCreativeVariantsBoundary:
    """Creative.variants is REQUIRED in adcp 3.6.0 — test all boundary cases."""

    def test_creative_without_variants_is_rejected(self):
        """Creative missing variants field must raise ValidationError."""
        from src.core.schemas import Creative

        with pytest.raises(ValidationError, match="variants"):
            Creative(creative_id="c1")

    def test_creative_with_empty_variants_is_valid(self):
        """Empty variants list is valid — library docs: 'Empty when creative has no variants yet'."""
        from src.core.schemas import Creative

        c = Creative(creative_id="c1", variants=[])
        assert c.creative_id == "c1"
        assert c.variants == []

    def test_creative_with_single_variant_is_valid(self):
        """Minimum valid Creative: one variant with only variant_id (the only required field)."""
        from adcp.types.generated_poc.core.creative_variant import CreativeVariant

        from src.core.schemas import Creative

        variant = CreativeVariant(variant_id="v1")
        c = Creative(creative_id="c1", variants=[variant])
        assert len(c.variants) == 1
        assert c.variants[0].variant_id == "v1"

    def test_creative_with_multiple_variants_is_valid(self):
        """Multiple variants are valid."""
        from adcp.types.generated_poc.core.creative_variant import CreativeVariant

        from src.core.schemas import Creative

        variants = [CreativeVariant(variant_id="v1"), CreativeVariant(variant_id="v2")]
        c = Creative(creative_id="c1", variants=variants)
        assert len(c.variants) == 2

    def test_creative_without_creative_id_is_rejected(self):
        """creative_id is REQUIRED — missing it must raise ValidationError."""
        from src.core.schemas import Creative

        with pytest.raises(ValidationError, match="creative_id"):
            Creative(variants=[])

    def test_creative_variant_without_variant_id_is_rejected(self):
        """CreativeVariant.variant_id is REQUIRED — missing it must raise ValidationError."""
        from adcp.types.generated_poc.core.creative_variant import CreativeVariant

        with pytest.raises(ValidationError, match="variant_id"):
            CreativeVariant()

    def test_creative_variant_with_optional_metrics_is_valid(self):
        """CreativeVariant accepts optional delivery metrics alongside variant_id."""
        from adcp.types.generated_poc.core.creative_variant import CreativeVariant

        variant = CreativeVariant(variant_id="v1", impressions=1000, clicks=50)
        assert variant.variant_id == "v1"
        assert variant.impressions == 1000
        assert variant.clicks == 50

    def test_creative_principal_id_still_excluded_from_response(self):
        """principal_id must remain an internal field excluded from model_dump() output."""
        from src.core.schemas import Creative

        c = Creative(creative_id="c1", variants=[], principal_id="p1")
        response = c.model_dump()
        assert "principal_id" not in response, "principal_id must not leak into AdCP response"

    def test_creative_principal_id_present_in_internal_dump(self):
        """principal_id must be present in model_dump_internal() for DB storage."""
        from src.core.schemas import Creative

        c = Creative(creative_id="c1", variants=[], principal_id="p1")
        internal = c.model_dump_internal()
        assert internal.get("principal_id") == "p1"


class TestPaginationCursorBased:
    """Pagination aligns with PaginationResponse in adcp 3.6.0 — cursor-based, has_more required."""

    def test_pagination_has_more_is_required(self):
        """has_more is REQUIRED in PaginationResponse — missing it must raise ValidationError."""
        from src.core.schemas import Pagination

        with pytest.raises(ValidationError, match="has_more"):
            Pagination()

    def test_pagination_with_has_more_false_is_valid(self):
        """Pagination with has_more=False is a valid terminal page."""
        from src.core.schemas import Pagination

        p = Pagination(has_more=False)
        assert p.has_more is False
        assert p.cursor is None
        assert p.total_count is None

    def test_pagination_with_cursor_is_valid(self):
        """Pagination with cursor string for continuation is valid."""
        from src.core.schemas import Pagination

        p = Pagination(has_more=True, cursor="next-page-token", total_count=100)
        assert p.has_more is True
        assert p.cursor == "next-page-token"
        assert p.total_count == 100


class TestPropertyIdentifierRequired:
    """Property aligns with adcp 3.6.0 — identifier and type are REQUIRED."""

    def test_property_without_identifier_is_rejected(self):
        """identifier is REQUIRED in adcp 3.6.0 Property."""
        from src.core.schemas import Property

        with pytest.raises(ValidationError, match="identifier"):
            Property(type="website")

    def test_property_without_type_is_rejected(self):
        """type is REQUIRED in adcp 3.6.0 Property."""
        from src.core.schemas import Property

        with pytest.raises(ValidationError, match="type"):
            Property(identifier="pub.example.com")

    def test_property_with_identifier_and_type_is_valid(self):
        """Minimum valid Property requires only identifier and type."""
        from src.core.schemas import Property

        p = Property(identifier="pub.example.com", type="website")
        assert p.identifier == "pub.example.com"
        assert str(p.type) == "website" or p.type.value == "website"  # type is an enum in 3.6.0
