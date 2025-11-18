"""Test that all Creative-related response models properly exclude internal fields.

This test suite covers:
- CreateCreativeResponse
- GetCreativesResponse

Both models contain nested Creative objects that have internal fields
(principal_id, created_at, updated_at, status) which must be excluded from
client responses.

Related:
- Original bug: SyncCreativesResponse (f5bd7b8a)
- Systematic fix: All response models with nested Pydantic models
- Pattern: Parent models must explicitly call nested model.model_dump()
"""

from datetime import UTC, datetime

from src.core.schemas import CreateCreativeResponse, Creative, CreativeApprovalStatus, GetCreativesResponse


def test_create_creative_response_excludes_internal_fields():
    """Test that CreateCreativeResponse excludes Creative internal fields."""
    # Create Creative with internal fields
    creative = Creative(
        creative_id="test_123",
        name="Test Banner",
        format={"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
        assets={"banner": {"asset_type": "image", "url": "https://example.com/banner.jpg"}},
        # Internal fields - should be excluded
        principal_id="principal_456",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        status="approved",
    )

    # Create response
    response = CreateCreativeResponse(
        creative=creative,
        status=CreativeApprovalStatus(creative_id="test_123", status="pending_review", detail="Under review"),
        suggested_adaptations=[],
    )

    # Dump to dict
    result = response.model_dump()

    # Verify internal fields excluded from nested creative
    creative_data = result["creative"]
    assert "principal_id" not in creative_data, "Internal field 'principal_id' should be excluded"
    assert "created_at" not in creative_data, "Legacy alias 'created_at' should be excluded"
    assert "updated_at" not in creative_data, "Legacy alias 'updated_at' should be excluded"

    # Verify spec fields present (library Creative includes these)
    assert creative_data["creative_id"] == "test_123"
    assert creative_data["name"] == "Test Banner"
    assert "format_id" in creative_data, "Spec field 'format_id' should be present"
    assert "assets" in creative_data, "Spec field 'assets' should be present"
    assert "status" in creative_data, "Spec field 'status' should be present (part of AdCP spec)"
    assert "created_date" in creative_data, "Spec field 'created_date' should be present"
    assert "updated_date" in creative_data, "Spec field 'updated_date' should be present"


def test_get_creatives_response_excludes_internal_fields():
    """Test that GetCreativesResponse excludes Creative internal fields from all creatives."""
    # Create multiple creatives with internal fields
    creatives = [
        Creative(
            creative_id=f"creative_{i}",
            name=f"Test Creative {i}",
            format={"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
            assets={"banner": {"asset_type": "image", "url": f"https://example.com/banner{i}.jpg"}},
            # Internal fields
            principal_id=f"principal_{i}",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            status="approved" if i % 2 == 0 else "pending_review",
        )
        for i in range(3)
    ]

    # Create response
    response = GetCreativesResponse(creatives=creatives, assignments=None)

    # Dump to dict
    result = response.model_dump()

    # Verify internal fields excluded from all creatives
    for i, creative_data in enumerate(result["creatives"]):
        assert "principal_id" not in creative_data, f"Creative {i}: principal_id should be excluded"
        assert "created_at" not in creative_data, f"Creative {i}: legacy alias created_at should be excluded"
        assert "updated_at" not in creative_data, f"Creative {i}: legacy alias updated_at should be excluded"

        # Verify spec fields present (library Creative includes these)
        assert creative_data["creative_id"] == f"creative_{i}"
        assert creative_data["name"] == f"Test Creative {i}"
        assert "status" in creative_data, f"Creative {i}: status is a spec field, should be present"
        assert "created_date" in creative_data, f"Creative {i}: created_date is a spec field"
        assert "updated_date" in creative_data, f"Creative {i}: updated_date is a spec field"


def test_creative_optional_fields_still_included():
    """Test that optional AdCP fields are included when present, only internal fields excluded."""
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

    response = GetCreativesResponse(creatives=[creative])
    result = response.model_dump()
    creative_data = result["creatives"][0]

    # Optional AdCP fields should be included
    assert "tags" in creative_data
    assert creative_data["tags"] == ["sports", "premium"]
    # Note: 'approved' field is not in library Creative, it was removed in our implementation

    # Internal fields still excluded
    assert "principal_id" not in creative_data, "Internal field principal_id should be excluded"

    # Spec fields should be present
    assert "status" in creative_data, "Status is a spec field, should be present"
