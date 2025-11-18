"""Test that ListCreativesResponse properly excludes internal fields from nested Creative objects.

This test ensures that Creative's custom model_dump() is called when serializing
ListCreativesResponse, preventing internal fields from leaking to clients.

Related:
- Original bug: SyncCreativesResponse (f5bd7b8a)
- Systematic fix: ListCreativesResponse nested serialization
- Pattern: All response models with nested Pydantic models need explicit serialization
"""

from datetime import UTC, datetime

from src.core.schemas import Creative, ListCreativesResponse, Pagination, QuerySummary


def test_list_creatives_response_excludes_internal_fields_from_nested_creatives():
    """Test that ListCreativesResponse excludes Creative internal fields.

    After refactoring Creative to extend library type:
    - principal_id: Internal advertiser association (excluded via exclude=True)
    - created_at/updated_at: Legacy aliases (removed from serialization)
    - status, created_date, updated_date: Now part of AdCP spec (included in responses)

    Creative.model_dump() handles exclusions, and ListCreativesResponse
    explicitly calls it for nested creatives.
    """
    # Create Creative with internal fields populated
    creative = Creative(
        creative_id="test_123",
        name="Test Banner",
        format={"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
        assets={"banner": {"asset_type": "image", "url": "https://example.com/banner.jpg"}},
        # Internal fields - should be excluded from response
        principal_id="principal_456",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        status="approved",
    )

    # Create response with the creative
    response = ListCreativesResponse(
        creatives=[creative],
        query_summary=QuerySummary(total_matching=1, returned=1, filters_applied=["format: display_300x250"]),
        pagination=Pagination(limit=50, offset=0, has_more=False),
    )

    # Dump to dict (what clients receive)
    result = response.model_dump()

    # Verify internal fields are excluded from nested creative
    creative_in_response = result["creatives"][0]

    assert "principal_id" not in creative_in_response, "Internal field 'principal_id' should be excluded"
    assert "created_at" not in creative_in_response, "Legacy alias 'created_at' should be excluded"
    assert "updated_at" not in creative_in_response, "Legacy alias 'updated_at' should be excluded"

    # Verify required AdCP spec fields are present (library Creative includes these)
    assert "creative_id" in creative_in_response
    assert creative_in_response["creative_id"] == "test_123"
    assert "name" in creative_in_response
    assert "format_id" in creative_in_response, "Spec field format_id should be present"
    assert "assets" in creative_in_response
    assert "status" in creative_in_response, "Status is now a spec field, should be present"
    assert "created_date" in creative_in_response, "Spec field created_date should be present"
    assert "updated_date" in creative_in_response, "Spec field updated_date should be present"


def test_list_creatives_response_with_multiple_creatives():
    """Test that internal fields are excluded from all creatives in the list."""
    # Create multiple creatives with internal fields
    creatives = [
        Creative(
            creative_id=f"creative_{i}",
            name=f"Test Creative {i}",
            format={"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
            assets={"banner": {"asset_type": "image", "url": f"https://example.com/banner{i}.jpg"}},
            principal_id=f"principal_{i}",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            status="approved" if i % 2 == 0 else "pending_review",
        )
        for i in range(3)
    ]

    response = ListCreativesResponse(
        creatives=creatives,
        query_summary=QuerySummary(total_matching=3, returned=3, filters_applied=[]),
        pagination=Pagination(limit=50, offset=0, has_more=False),
    )

    result = response.model_dump()

    # Verify internal fields excluded from all creatives
    for i, creative_data in enumerate(result["creatives"]):
        assert "principal_id" not in creative_data, f"Creative {i}: principal_id should be excluded"
        assert "created_at" not in creative_data, f"Creative {i}: legacy alias created_at should be excluded"
        assert "updated_at" not in creative_data, f"Creative {i}: legacy alias updated_at should be excluded"

        # Verify spec fields present
        assert creative_data["creative_id"] == f"creative_{i}"
        assert "status" in creative_data, f"Creative {i}: status is a spec field"
        assert "created_date" in creative_data, f"Creative {i}: created_date is a spec field"
        assert "updated_date" in creative_data, f"Creative {i}: updated_date is a spec field"


def test_list_creatives_response_with_optional_fields():
    """Test that optional AdCP fields (tags) are included when present."""
    creative = Creative(
        creative_id="test_with_optional",
        name="Test Creative",
        format={"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
        assets={"banner": {"asset_type": "image", "url": "https://example.com/banner.jpg"}},
        tags=["sports", "premium"],  # Optional AdCP field
        # Internal fields
        principal_id="principal_123",
        status="approved",
    )

    response = ListCreativesResponse(
        creatives=[creative],
        query_summary=QuerySummary(total_matching=1, returned=1, filters_applied=[]),
        pagination=Pagination(limit=50, offset=0, has_more=False),
    )

    result = response.model_dump()
    creative_data = result["creatives"][0]

    # Optional AdCP fields should be included
    assert "tags" in creative_data
    assert creative_data["tags"] == ["sports", "premium"]

    # Internal fields still excluded
    assert "principal_id" not in creative_data

    # Spec fields should be present
    assert "status" in creative_data, "Status is a spec field, should be present"
