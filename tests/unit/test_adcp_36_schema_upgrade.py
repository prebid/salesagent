"""Schema boundary tests for adcp 3.6.0 upgrade (salesagent-83o).

These tests define the expected behavior AFTER the upgrade to adcp 3.6.0.
They fail on 3.2.0 and must pass on 3.6.0 once our local schemas are aligned.

Covers the Creative.variants boundary matrix from the design field,
the Pagination cursor-based structure, and Property identifier/type requirement.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


class TestCreativeListingBoundary:
    """Creative extends listing Creative in adcp 3.6.0 — test boundary cases.

    The listing Creative (list_creatives_response.Creative) has required fields:
    creative_id, format_id, name, status, created_date, updated_date.
    The delivery-only field 'variants' does not exist on the listing Creative.
    """

    def test_creative_without_format_id_is_rejected(self):
        """Creative missing format_id field must raise ValidationError."""
        from src.core.schemas import Creative

        with pytest.raises(ValidationError, match="format_id"):
            Creative(creative_id="c1")

    def test_creative_with_minimal_fields_is_valid(self):
        """Creative with creative_id + format_id + name is valid (dates have default_factory)."""
        from src.core.schemas import Creative, FormatId

        c = Creative(
            creative_id="c1",
            name="Test Creative",
            format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
        )
        assert c.creative_id == "c1"
        assert c.name == "Test Creative"
        assert c.format_id.id == "display_300x250"

    def test_creative_variants_silently_stripped(self):
        """Passing variants= (from old delivery base) is silently stripped, not rejected."""
        from src.core.schemas import Creative, FormatId

        c = Creative(
            creative_id="c1",
            name="Test Creative",
            format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
            variants=[],
        )
        assert c.creative_id == "c1"
        assert not hasattr(c, "variants")

    def test_creative_without_creative_id_is_rejected(self):
        """creative_id is REQUIRED — missing it must raise ValidationError."""
        from src.core.schemas import Creative, FormatId

        with pytest.raises(ValidationError, match="creative_id"):
            Creative(
                format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
            )

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
        from src.core.schemas import Creative, FormatId

        c = Creative(
            creative_id="c1",
            name="Test Creative",
            format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
            principal_id="p1",
        )
        response = c.model_dump()
        assert "principal_id" not in response, "principal_id must not leak into AdCP response"

    def test_creative_principal_id_present_in_internal_dump(self):
        """principal_id must be present in model_dump_internal() for DB storage."""
        from src.core.schemas import Creative, FormatId

        c = Creative(
            creative_id="c1",
            name="Test Creative",
            format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
            principal_id="p1",
        )
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


class TestPropertyRequiredFields:
    """Property aligns with adcp 3.10 — property_type, name, identifiers are REQUIRED."""

    def test_property_without_property_type_is_rejected(self):
        """property_type is REQUIRED in adcp 3.10 Property."""
        from src.core.schemas import Property

        with pytest.raises(ValidationError, match="property_type"):
            Property(name="Example", identifiers=[{"type": "domain", "value": "example.com"}])

    def test_property_without_name_is_rejected(self):
        """name is REQUIRED in adcp 3.10 Property."""
        from src.core.schemas import Property

        with pytest.raises(ValidationError, match="name"):
            Property(property_type="website", identifiers=[{"type": "domain", "value": "example.com"}])

    def test_property_without_identifiers_is_rejected(self):
        """identifiers is REQUIRED in adcp 3.10 Property."""
        from src.core.schemas import Property

        with pytest.raises(ValidationError, match="identifiers"):
            Property(property_type="website", name="Example")

    def test_property_with_identifier_and_type_is_valid(self):
        """Minimum valid Property requires property_type, name, and identifiers."""
        from src.core.schemas import Property

        p = Property(
            property_type="website",
            name="Example",
            identifiers=[{"type": "domain", "value": "pub.example.com"}],
        )
        assert p.name == "Example"
        assert p.property_type.value == "website"
        assert len(p.identifiers) == 1
        assert p.identifiers[0].value == "pub.example.com"
