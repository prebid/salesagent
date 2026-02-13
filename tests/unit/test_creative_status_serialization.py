"""Unit tests for Creative status enum serialization at boundaries.

Tests that Creative.status enum is properly converted to string when serialized
with mode='json' — the mode used at all serialization boundaries (A2A, MCP, DB).
"""

from datetime import UTC, datetime

from src.core.schemas import Creative, CreativeStatus, FormatId


def test_creative_status_serialized_as_string_at_boundary():
    """Test that Creative.model_dump(mode='json') serializes status as string.

    Verifies that our custom model_dump() correctly propagates mode='json' to
    super(), so Pydantic's built-in enum serialization produces strings at
    serialization boundaries (A2A, MCP, DB).
    """
    creative = Creative(
        creative_id="test_creative_1",
        name="Test Creative",
        format_id=FormatId(id="display_300x250", agent_url="https://creative.adcontextprotocol.org"),
        status=CreativeStatus.approved,
        created_date=datetime.now(UTC),
        updated_date=datetime.now(UTC),
    )

    data = creative.model_dump(mode="json")

    assert isinstance(data["status"], str), f"Expected str, got {type(data['status'])}"
    assert data["status"] == "approved"


def test_creative_model_dump_internal_includes_principal_id():
    """Test that model_dump_internal includes excluded internal fields."""
    creative = Creative(
        creative_id="test_creative_2",
        name="Test Creative 2",
        format_id=FormatId(id="display_728x90", agent_url="https://creative.adcontextprotocol.org"),
        status=CreativeStatus.pending_review,
        created_date=datetime.now(UTC),
        updated_date=datetime.now(UTC),
        principal_id="test_principal",
    )

    data = creative.model_dump_internal()

    # model_dump_internal exists specifically to include excluded fields
    assert data["principal_id"] == "test_principal"
    # Status is a typed Python object in Python mode — engine json_serializer
    # handles conversion to string when this data reaches the DB JSONB column
    assert data["status"] == CreativeStatus.pending_review


def test_creative_all_status_values_at_boundary():
    """Test that all CreativeStatus enum values serialize to strings at boundaries."""
    statuses = [
        CreativeStatus.approved,
        CreativeStatus.rejected,
        CreativeStatus.pending_review,
        CreativeStatus.processing,
    ]

    for status_enum in statuses:
        creative = Creative(
            creative_id=f"test_{status_enum.value}",
            name=f"Test {status_enum.value}",
            format_id=FormatId(id="display_300x250", agent_url="https://creative.adcontextprotocol.org"),
            status=status_enum,
            created_date=datetime.now(UTC),
            updated_date=datetime.now(UTC),
        )

        data = creative.model_dump(mode="json")

        assert isinstance(data["status"], str), f"Status should be str for {status_enum}"
        assert data["status"] == status_enum.value


def test_creative_status_string_passthrough():
    """Test that passing status as string round-trips through enum and back."""
    creative = Creative(
        creative_id="test_creative_string",
        name="Test Creative String",
        format_id=FormatId(id="display_300x250", agent_url="https://creative.adcontextprotocol.org"),
        status="approved",  # String → Pydantic coerces to enum
        created_date=datetime.now(UTC),
        updated_date=datetime.now(UTC),
    )

    # Verify Pydantic coerced string to enum internally
    assert creative.status == CreativeStatus.approved

    # At boundaries, mode='json' converts enum back to string
    data = creative.model_dump(mode="json")
    assert isinstance(data["status"], str)
    assert data["status"] == "approved"
