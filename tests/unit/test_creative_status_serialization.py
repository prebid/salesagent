"""Unit tests for Creative status enum serialization.

Tests that Creative.status enum is properly converted to string in model_dump().
This is critical for AdCP compliance - status must be a string in responses.
"""

from datetime import UTC, datetime

from src.core.schemas import Creative, CreativeStatus, FormatId


def test_creative_status_serialized_as_string():
    """Test that Creative.status enum is converted to string in model_dump()."""
    # Create a Creative with status enum
    creative = Creative(
        creative_id="test_creative_1",
        name="Test Creative",
        format_id=FormatId(id="display_300x250", agent_url="https://creative.adcontextprotocol.org"),
        status=CreativeStatus.approved,  # Enum value
        created_date=datetime.now(UTC),
        updated_date=datetime.now(UTC),
    )

    # Serialize to dict
    data = creative.model_dump()

    # Status should be a string, not an enum
    assert isinstance(data["status"], str), f"Expected str, got {type(data['status'])}"
    assert data["status"] == "approved", f"Expected 'approved', got {data['status']}"


def test_creative_status_serialized_in_model_dump_internal():
    """Test that status enum is also converted in model_dump_internal()."""
    creative = Creative(
        creative_id="test_creative_2",
        name="Test Creative 2",
        format_id=FormatId(id="display_728x90", agent_url="https://creative.adcontextprotocol.org"),
        status=CreativeStatus.pending_review,  # Enum value
        created_date=datetime.now(UTC),
        updated_date=datetime.now(UTC),
        principal_id="test_principal",  # Internal field
    )

    # Serialize with internal fields
    data = creative.model_dump_internal()

    # Status should be a string
    assert isinstance(data["status"], str)
    assert data["status"] == "pending_review"

    # Principal ID should be included (internal field)
    assert data["principal_id"] == "test_principal"


def test_creative_all_status_values():
    """Test that all CreativeStatus enum values serialize correctly."""
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

        data = creative.model_dump()

        # Verify serialization
        assert isinstance(data["status"], str), f"Status should be str for {status_enum}"
        assert data["status"] == status_enum.value, f"Expected {status_enum.value}, got {data['status']}"


def test_creative_status_string_passthrough():
    """Test that passing status as string also works (backward compatibility)."""
    # Create Creative with status as string (Pydantic will convert to enum internally)
    creative = Creative(
        creative_id="test_creative_string",
        name="Test Creative String",
        format_id=FormatId(id="display_300x250", agent_url="https://creative.adcontextprotocol.org"),
        status="approved",  # String value (Pydantic converts to enum)
        created_date=datetime.now(UTC),
        updated_date=datetime.now(UTC),
    )

    # Serialize to dict
    data = creative.model_dump()

    # Should still output as string
    assert isinstance(data["status"], str)
    assert data["status"] == "approved"
