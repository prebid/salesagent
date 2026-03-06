"""Test that ListCreativesResponse properly excludes internal fields from nested Creative objects.

Creative extends the listing Creative (list_creatives_response.Creative):
- Public fields: creative_id, format_id, name, status, created_date, updated_date, assets, tags
- Internal fields (exclude=True): principal_id

Related:
- Original bug: SyncCreativesResponse (f5bd7b8a)
- Systematic fix: ListCreativesResponse nested serialization
- Pattern: All response models with nested Pydantic models need explicit serialization
"""

from datetime import UTC, datetime

from src.core.schemas import Creative, ListCreativesResponse, Pagination, QuerySummary


def test_list_creatives_response_excludes_internal_fields_from_nested_creatives():
    """Test that ListCreativesResponse excludes Creative internal fields.

    adcp 3.6.0: name, assets, tags, status, created_date, updated_date are internal.
    model_dump() only returns: creative_id, format_id, variants.
    principal_id is always excluded (internal advertiser tracking).
    """
    # Create Creative with internal fields populated
    creative = Creative(
        creative_id="test_123",
        variants=[],
        name="Test Banner",
        format={"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
        assets={"banner": {"asset_type": "image", "url": "https://example.com/banner.jpg"}},
        # Internal fields - should be excluded from response
        principal_id="principal_456",
        created_date=datetime.now(UTC),
        updated_date=datetime.now(UTC),
        status="approved",
    )

    # Create response with the creative
    response = ListCreativesResponse(
        creatives=[creative],
        query_summary=QuerySummary(total_matching=1, returned=1, filters_applied=["format: display_300x250"]),
        pagination=Pagination(has_more=False),
    )

    # Dump to dict (what clients receive)
    result = response.model_dump()

    # Verify internal fields are excluded from nested creative
    creative_in_response = result["creatives"][0]

    assert "principal_id" not in creative_in_response, "Internal field 'principal_id' should be excluded"

    # Listing Creative: public fields in model_dump()
    assert "creative_id" in creative_in_response
    assert creative_in_response["creative_id"] == "test_123"
    assert "format_id" in creative_in_response, "Spec field format_id should be present"
    assert "name" in creative_in_response, "Listing Creative: name is a public field"
    assert "status" in creative_in_response, "Listing Creative: status is a public field"
    assert "created_date" in creative_in_response, "Listing Creative: created_date is a public field"
    assert "updated_date" in creative_in_response, "Listing Creative: updated_date is a public field"

    # Delivery-only fields should NOT be present
    assert "variants" not in creative_in_response, "Delivery field 'variants' should not be in listing response"


def test_list_creatives_response_with_multiple_creatives():
    """Test that internal fields are excluded from all creatives in the list."""
    # Create multiple creatives with internal fields
    creatives = [
        Creative(
            creative_id=f"creative_{i}",
            variants=[],
            name=f"Test Creative {i}",
            format={"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
            assets={"banner": {"asset_type": "image", "url": f"https://example.com/banner{i}.jpg"}},
            principal_id=f"principal_{i}",
            created_date=datetime.now(UTC),
            updated_date=datetime.now(UTC),
            status="approved" if i % 2 == 0 else "pending_review",
        )
        for i in range(3)
    ]

    response = ListCreativesResponse(
        creatives=creatives,
        query_summary=QuerySummary(total_matching=3, returned=3, filters_applied=[]),
        pagination=Pagination(has_more=False),
    )

    result = response.model_dump()

    # Verify internal fields excluded from all creatives
    for i, creative_data in enumerate(result["creatives"]):
        assert "principal_id" not in creative_data, f"Creative {i}: principal_id should be excluded"

        # Listing Creative: public fields in model_dump()
        assert creative_data["creative_id"] == f"creative_{i}"
        assert "format_id" in creative_data, f"Creative {i}: format_id should be present"
        assert "name" in creative_data, f"Creative {i}: name is a public listing field"
        assert "status" in creative_data, f"Creative {i}: status is a public listing field"
        assert "created_date" in creative_data, f"Creative {i}: created_date is a public listing field"
        assert "updated_date" in creative_data, f"Creative {i}: updated_date is a public listing field"


def test_list_creatives_response_with_optional_fields():
    """Test that internal fields are accessible via model_dump_internal()."""
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

    response = ListCreativesResponse(
        creatives=[creative],
        query_summary=QuerySummary(total_matching=1, returned=1, filters_applied=[]),
        pagination=Pagination(has_more=False),
    )

    result = response.model_dump()
    creative_data = result["creatives"][0]

    # Listing Creative: tags is a public optional field; present when set
    assert "tags" in creative_data, "Listing Creative: tags is a public field"

    # Internal fields always excluded from model_dump()
    assert "principal_id" not in creative_data

    # Spec fields in model_dump()
    assert "creative_id" in creative_data
    assert "format_id" in creative_data

    # Internal fields accessible via model_dump_internal()
    internal_data = creative.model_dump_internal()
    assert "principal_id" in internal_data
    assert internal_data["principal_id"] == "principal_123"


def test_query_summary_sort_applied_serializes_enum_values():
    """Test that sort_applied serializes enum values, not enum repr strings.

    Bug: Using str() on enums produces "SortDirection.desc" instead of "desc".
    The sort_applied dict must contain enum VALUES for valid JSON schema compliance.

    The client validates against a schema expecting:
        "direction": "desc"  (valid)
    Not:
        "direction": "SortDirection.desc"  (invalid - fails schema validation)
    """
    # The sort_applied dict that gets built in _list_creatives_impl
    # must use .value, not str()
    from adcp.types.generated_poc.enums.creative_sort_field import CreativeSortField
    from adcp.types.generated_poc.enums.sort_direction import SortDirection

    # Simulate what the implementation should do
    field_enum = CreativeSortField.created_date
    direction_enum = SortDirection.desc

    # CORRECT: Use .value to get the string value
    correct_sort_applied = {"field": field_enum.value, "direction": direction_enum.value}

    # WRONG: Using str() produces the enum repr
    wrong_sort_applied = {"field": str(field_enum), "direction": str(direction_enum)}

    # Verify correct serialization produces simple strings
    assert correct_sort_applied["field"] == "created_date"
    assert correct_sort_applied["direction"] == "desc"

    # Verify wrong serialization produces enum repr (this is the bug we fixed)
    assert wrong_sort_applied["field"] == "CreativeSortField.created_date"
    assert wrong_sort_applied["direction"] == "SortDirection.desc"

    # Create a QuerySummary with the correct sort_applied
    query_summary = QuerySummary(
        total_matching=10,
        returned=5,
        filters_applied=[],
        sort_applied=correct_sort_applied,
    )

    result = query_summary.model_dump(mode="json")

    # Verify sort_applied contains valid string values, not enum repr
    assert result["sort_applied"]["field"] == "created_date"
    assert result["sort_applied"]["direction"] == "desc"
    assert "SortDirection" not in str(result["sort_applied"]["direction"])
    assert "CreativeSortField" not in str(result["sort_applied"]["field"])
