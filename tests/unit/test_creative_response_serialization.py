"""Test that all Creative-related response models properly exclude internal fields.

adcp 3.6.0: Many Creative fields moved to internal (exclude=True):
- name, assets, tags, status, created_date, updated_date are now INTERNAL
- model_dump() only returns: creative_id, format_id, variants

This test suite covers:
- CreateCreativeResponse
- GetCreativesResponse

Internal fields are accessible via model_dump_internal() for DB storage.
"""

from datetime import UTC, datetime

from src.core.schemas import CreateCreativeResponse, Creative, CreativeApprovalStatus, GetCreativesResponse


def test_create_creative_response_excludes_internal_fields():
    """Test that CreateCreativeResponse excludes Creative internal fields."""
    # Create Creative with internal fields
    creative = Creative(
        creative_id="test_123",
        variants=[],
        name="Test Banner",
        format={"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
        assets={"banner": {"asset_type": "image", "url": "https://example.com/banner.jpg"}},
        # Internal fields - should be excluded
        principal_id="principal_456",
        created_date=datetime.now(UTC),
        updated_date=datetime.now(UTC),
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

    # Listing Creative: model_dump() returns public listing fields
    assert creative_data["creative_id"] == "test_123"
    assert "format_id" in creative_data, "Spec field 'format_id' should be present"
    assert "name" in creative_data, "Listing Creative: name is a public field"
    assert "status" in creative_data, "Listing Creative: status is a public field"

    # Delivery-only fields should NOT be present
    assert "variants" not in creative_data, "Delivery field 'variants' should not be in listing response"


def test_get_creatives_response_excludes_internal_fields():
    """Test that GetCreativesResponse excludes Creative internal fields from all creatives."""
    # Create multiple creatives with internal fields
    creatives = [
        Creative(
            creative_id=f"creative_{i}",
            variants=[],
            name=f"Test Creative {i}",
            format={"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
            assets={"banner": {"asset_type": "image", "url": f"https://example.com/banner{i}.jpg"}},
            # Internal fields
            principal_id=f"principal_{i}",
            created_date=datetime.now(UTC),
            updated_date=datetime.now(UTC),
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

        # Listing Creative: public fields in model_dump()
        assert creative_data["creative_id"] == f"creative_{i}"
        assert "format_id" in creative_data, f"Creative {i}: format_id should be present"
        assert "name" in creative_data, f"Creative {i}: name is a public listing field"
        assert "status" in creative_data, f"Creative {i}: status is a public listing field"


def test_creative_optional_fields_still_included():
    """Test model_dump_internal() returns internal fields when present."""
    creative = Creative(
        creative_id="test_with_optional",
        variants=[],
        name="Test Creative",
        format={"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
        assets={"banner": {"asset_type": "image", "url": "https://example.com/banner.jpg"}},
        tags=["sports", "premium"],  # Internal field in adcp 3.6.0
        # Internal fields
        principal_id="principal_123",
        status="approved",
    )

    response = GetCreativesResponse(creatives=[creative])
    result = response.model_dump()
    creative_data = result["creatives"][0]

    # Listing Creative: tags is a public optional field; present when set
    assert "tags" in creative_data, "Listing Creative: tags is a public field"

    # Internal fields still excluded
    assert "principal_id" not in creative_data, "Internal field principal_id should be excluded"

    # Internal fields accessible via model_dump_internal()
    internal_data = creative.model_dump_internal()
    assert "principal_id" in internal_data
    assert internal_data["principal_id"] == "principal_123"
