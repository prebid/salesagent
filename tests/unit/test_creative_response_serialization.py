"""Test that all Creative-related response models properly exclude internal fields.

adcp 3.6.0: Many Creative fields moved to internal (exclude=True):
- name, assets, tags, status, created_date, updated_date are now INTERNAL
- model_dump() only returns: creative_id, format_id, variants

This test suite covers:
- CreateCreativeResponse
- GetCreativesResponse
- SyncCreativeResult
- SyncCreativesResponse
- ListCreativeFormatsResponse
- ListCreativesResponse
"""

from datetime import UTC, datetime

from adcp.types.generated_poc.enums.creative_action import CreativeAction

from src.core.schemas import (
    CreateCreativeResponse,
    Creative,
    CreativeApprovalStatus,
    GetCreativesResponse,
    ListCreativeFormatsResponse,
    ListCreativesResponse,
    Pagination,
    QuerySummary,
    SyncCreativeResult,
    SyncCreativesResponse,
)


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


# ── SyncCreativeResult serialization ─────────────────────────────────────


def test_sync_creative_result_excludes_internal_fields():
    """model_dump() excludes status and review_feedback (internal-only)."""
    result = SyncCreativeResult(
        creative_id="c_1",
        action=CreativeAction.created,
        status="pending_review",
        review_feedback="Looks good",
    )
    dumped = result.model_dump()
    assert dumped["creative_id"] == "c_1"
    assert "status" not in dumped
    assert "review_feedback" not in dumped


def test_sync_creative_result_excludes_empty_lists():
    """model_dump() omits changes, errors, warnings when empty."""
    result = SyncCreativeResult(
        creative_id="c_2",
        action=CreativeAction.updated,
        changes=[],
        errors=[],
        warnings=[],
    )
    dumped = result.model_dump()
    assert "changes" not in dumped
    assert "errors" not in dumped
    assert "warnings" not in dumped


def test_sync_creative_result_keeps_populated_lists():
    """model_dump() keeps changes/errors/warnings when non-empty."""
    result = SyncCreativeResult(
        creative_id="c_3",
        action=CreativeAction.updated,
        changes=["name"],
        warnings=["provenance missing"],
    )
    dumped = result.model_dump()
    assert dumped["changes"] == ["name"]
    assert dumped["warnings"] == ["provenance missing"]
    assert "errors" not in dumped  # still empty → omitted


def test_sync_creative_result_model_dump_internal():
    """model_dump_internal() drops user-passed exclude but Field(exclude=True) still applies.

    The method ensures no additional user excludes are applied. Internal fields
    with exclude=True on the Field are still excluded by Pydantic.
    """
    result = SyncCreativeResult(
        creative_id="c_4",
        action=CreativeAction.created,
        changes=["name"],
        status="approved",
        review_feedback="Auto-approved",
    )
    internal = result.model_dump_internal()
    # model_dump_internal drops user exclude param, but Field(exclude=True) persists
    assert internal["creative_id"] == "c_4"
    # changes/errors/warnings are NOT excluded by Field definition, so they appear
    assert internal["changes"] == ["name"]


# ── SyncCreativesResponse __str__ ────────────────────────────────────────


def test_sync_creatives_response_str_created():
    """__str__ summarizes created/updated/failed counts."""
    response = SyncCreativesResponse(  # type: ignore[call-arg]
        creatives=[
            SyncCreativeResult(creative_id="c_1", action=CreativeAction.created),
            SyncCreativeResult(creative_id="c_2", action=CreativeAction.created),
            SyncCreativeResult(creative_id="c_3", action=CreativeAction.updated),
        ],
    )
    msg = str(response)
    assert "2 created" in msg
    assert "1 updated" in msg


def test_sync_creatives_response_str_no_changes():
    """__str__ reports 'no changes' when no creatives."""
    response = SyncCreativesResponse(creatives=[])  # type: ignore[call-arg]
    assert "no changes" in str(response)


def test_sync_creatives_response_str_with_deleted():
    """__str__ includes deleted count."""
    response = SyncCreativesResponse(  # type: ignore[call-arg]
        creatives=[
            SyncCreativeResult(creative_id="c_1", action=CreativeAction.deleted),
        ],
    )
    assert "1 deleted" in str(response)


def test_sync_creatives_response_str_failed():
    """__str__ includes failed count."""
    response = SyncCreativesResponse(  # type: ignore[call-arg]
        creatives=[
            SyncCreativeResult(creative_id="c_1", action=CreativeAction.failed),
        ],
    )
    assert "1 failed" in str(response)


def test_sync_creatives_response_str_dry_run():
    """__str__ appends '(dry run)' when dry_run=True."""
    response = SyncCreativesResponse(  # type: ignore[call-arg]
        creatives=[
            SyncCreativeResult(creative_id="c_1", action=CreativeAction.created),
        ],
        dry_run=True,
    )
    msg = str(response)
    assert "(dry run)" in msg


def test_sync_creatives_response_properties_success():
    """Success variant: creatives, dry_run accessible; errors is None."""
    response = SyncCreativesResponse(  # type: ignore[call-arg]
        creatives=[
            SyncCreativeResult(creative_id="c_1", action=CreativeAction.created),
        ],
        dry_run=False,
    )
    assert len(response.creatives) == 1
    assert response.dry_run is False
    # adcp 3.9: SyncCreativesResponse subclasses success variant only;
    # error variant is a separate type, no .errors attr on success
    assert not hasattr(response, "errors")
    assert response.context is None


# ── ListCreativeFormatsResponse __str__ ──────────────────────────────────


def test_list_creative_formats_response_str_zero():
    """__str__ with no formats."""
    response = ListCreativeFormatsResponse(formats=[])
    assert str(response) == "No creative formats are currently supported."


def test_list_creative_formats_response_str_one():
    """__str__ with exactly one format."""
    from src.core.schemas import Format, FormatId

    fmt = Format(
        format_id=FormatId(agent_url="https://example.com", id="f1"),
        name="Banner",
        is_standard=True,
    )
    response = ListCreativeFormatsResponse(formats=[fmt])
    assert str(response) == "Found 1 creative format."


def test_list_creative_formats_response_str_many():
    """__str__ with multiple formats."""
    from src.core.schemas import Format, FormatId

    fmts = [
        Format(format_id=FormatId(agent_url="https://example.com", id=f"f{i}"), name=f"F{i}", is_standard=True)
        for i in range(3)
    ]
    response = ListCreativeFormatsResponse(formats=fmts)
    assert str(response) == "Found 3 creative formats."


# ── ListCreativesResponse __str__ ────────────────────────────────────────


def test_list_creatives_response_str_all_shown():
    """__str__ when all results shown (returned == total_matching)."""
    response = ListCreativesResponse(
        creatives=[],
        query_summary=QuerySummary(returned=5, total_matching=5),
        pagination=Pagination(has_more=False, total_count=5),
    )
    assert str(response) == "Found 5 creatives."


def test_list_creatives_response_str_paginated():
    """__str__ when showing a page (returned < total_matching)."""
    response = ListCreativesResponse(
        creatives=[],
        query_summary=QuerySummary(returned=10, total_matching=50),
        pagination=Pagination(has_more=True, total_count=50),
    )
    assert str(response) == "Showing 10 of 50 creatives."


def test_list_creatives_response_str_singular():
    """__str__ handles singular correctly."""
    response = ListCreativesResponse(
        creatives=[],
        query_summary=QuerySummary(returned=1, total_matching=1),
        pagination=Pagination(has_more=False, total_count=1),
    )
    assert str(response) == "Found 1 creative."


# ── CreateCreativeResponse __str__ and nested serialization ──────────────


def test_create_creative_response_str():
    """__str__ returns human-readable message."""
    creative = Creative(
        creative_id="test_str",
        variants=[],
        name="Test",
        format={"agent_url": "https://example.com", "id": "f1"},
        assets={},
    )
    response = CreateCreativeResponse(
        creative=creative,
        status=CreativeApprovalStatus(creative_id="test_str", status="approved", detail="OK"),
        suggested_adaptations=[],
    )
    msg = str(response)
    assert "test_str" in msg
    assert "approved" in msg
