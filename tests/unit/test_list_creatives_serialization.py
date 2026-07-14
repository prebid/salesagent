"""Test that ListCreativesResponse properly excludes internal fields from nested Creative objects.

Creative extends the listing Creative (list_creatives_response.Creative):
- Public fields: creative_id, format_id, name, status, created_date, updated_date, assets, tags
- Internal fields (exclude=True): principal_id

Related:
- Original bug: SyncCreativesResponse (f5bd7b8a)
- Systematic fix: ListCreativesResponse nested serialization
- Pattern: All response models with nested Pydantic models need explicit serialization
"""

from src.core.schemas import ListCreativesResponse, Pagination, QuerySummary
from tests.helpers.creative_test_helpers import (
    assert_listing_creative_fields,
    make_test_creative,
    make_test_creative_list,
)


def test_list_creatives_response_excludes_internal_fields_from_nested_creatives():
    """Test that ListCreativesResponse excludes Creative internal fields.

    adcp 3.6.0: name, assets, tags, status, created_date, updated_date are internal.
    model_dump() only returns: creative_id, format_id, variants.
    principal_id is always excluded (internal advertiser tracking).
    """
    creative = make_test_creative()

    response = ListCreativesResponse(
        creatives=[creative],
        query_summary=QuerySummary(total_matching=1, returned=1, filters_applied=["format: display_300x250"]),
        pagination=Pagination(has_more=False),
    )

    result = response.model_dump()
    creative_in_response = result["creatives"][0]
    assert_listing_creative_fields(creative_in_response, "test_123")
    assert "created_date" in creative_in_response, "Listing Creative: created_date is a public field"
    assert "updated_date" in creative_in_response, "Listing Creative: updated_date is a public field"

    # Delivery-only fields should NOT be present
    assert "variants" not in creative_in_response, "Delivery field 'variants' should not be in listing response"


def test_list_creatives_response_with_multiple_creatives():
    """Test that internal fields are excluded from all creatives in the list."""
    creatives = make_test_creative_list(3)

    response = ListCreativesResponse(
        creatives=creatives,
        query_summary=QuerySummary(total_matching=3, returned=3, filters_applied=[]),
        pagination=Pagination(has_more=False),
    )

    result = response.model_dump()

    for i, creative_data in enumerate(result["creatives"]):
        assert_listing_creative_fields(creative_data, f"creative_{i}", prefix=f"Creative {i}")
        assert "created_date" in creative_data, f"Creative {i}: created_date is a public listing field"
        assert "updated_date" in creative_data, f"Creative {i}: updated_date is a public listing field"


def test_list_creatives_response_with_optional_fields():
    """Test that internal fields are accessible via model_dump_internal()."""
    creative = make_test_creative(
        creative_id="test_with_optional",
        name="Test Creative",
        principal_id="principal_123",
        tags=["sports", "premium"],
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
    from adcp.types import SortDirection
    from adcp.types.generated_poc.enums.creative_sort_field import (
        CreativeSortField,
    )  # TODO: no stable alias in adcp.types

    # Simulate what the implementation should do
    field_enum = CreativeSortField.created_date
    direction_enum = SortDirection.desc

    # CORRECT: Use .value to get the string value
    correct_sort_applied = {"field": field_enum.value, "direction": direction_enum.value}

    # str() of these enums. adcp 6.6 made them StrEnum, so str() now yields the bare value
    # too — the historical footgun (str(enum) => "CreativeSortField.created_date") is gone.
    str_sort_applied = {"field": str(field_enum), "direction": str(direction_enum)}

    # Verify correct serialization produces simple strings
    assert correct_sort_applied["field"] == "created_date"
    assert correct_sort_applied["direction"] == "desc"

    # StrEnum: str() and .value now agree
    assert str_sort_applied["field"] == "created_date"
    assert str_sort_applied["direction"] == "desc"

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
