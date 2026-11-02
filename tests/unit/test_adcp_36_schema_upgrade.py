"""Schema boundary tests for adcp 3.6.0 upgrade .

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

    def test_creative_variants_is_refused_not_stripped(self):
        """Passing variants= (from the old delivery base) is REJECTED, not stripped.

        The pinned ``creative/list-creatives-response.json`` item declares
        ``[account, assets, assignments, concept_id, concept_name, created_date,
        creative_id, format_id, items, name, pricing_options, purge, snapshot,
        snapshot_unavailable_reason, status, tags, updated_date, variables,
        webhook_activity]`` — no ``variants``. In 3.1 that field belongs to a DIFFERENT
        object: the per-creative delivery breakdown in
        ``creative/get-creative-delivery-response.json`` and the build groups in
        ``media-buy/build-creative-response.json``.

        This case previously asserted a SILENT STRIP. That is the production-mode half of
        CLAUDE.md pattern #7 and never the dev-mode one: an undeclared field is a hard
        rejection in dev exactly so a spec field this seller has not implemented is loud,
        and dropped only in production. Stripping is also not a model-construction
        behavior at all — ``deep_strip_to_schema`` runs at the boundary.
        """
        from src.core.schemas import Creative, FormatId

        with pytest.raises(ValidationError, match="variants"):
            Creative(
                creative_id="c1",
                name="Test Creative",
                format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
                variants=[],
            )

    def test_creative_without_creative_id_is_rejected(self):
        """creative_id is REQUIRED — missing it must raise ValidationError."""
        from src.core.schemas import Creative, FormatId

        with pytest.raises(ValidationError, match="creative_id"):
            Creative(
                format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
            )

    def test_creative_variant_without_variant_id_is_rejected(self):
        """CreativeVariant.variant_id is REQUIRED — missing it must raise ValidationError."""
        from adcp.types import CreativeVariant

        with pytest.raises(ValidationError, match="variant_id"):
            CreativeVariant()

    def test_creative_variant_with_optional_metrics_is_valid(self):
        """CreativeVariant accepts optional delivery metrics alongside variant_id."""
        from adcp.types import CreativeVariant

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

    def test_creative_principal_id_present_on_the_model(self):
        """principal_id is carried on the model even though it never reaches the wire.

        The attribute IS what existing means for a ``Field(exclude=True)`` field; there is
        no second dump shape to read it out of (CLAUDE.md pattern 4 — one serializer seat).
        """
        from src.core.schemas import Creative, FormatId

        c = Creative(
            creative_id="c1",
            name="Test Creative",
            format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
            principal_id="p1",
        )
        assert c.principal_id == "p1"


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
