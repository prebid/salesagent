"""Unit tests for Creative status enum serialization at boundaries.

Tests that Creative.status enum is properly converted to string when serialized
with model_dump_internal() — in adcp 3.6.0, status is an internal-only field
excluded from the public model_dump() but included in model_dump_internal().
"""

from datetime import UTC, datetime

from src.core.schemas import Creative, CreativeStatus, FormatId


def test_creative_status_serialized_as_string_at_boundary():
    """Test that Creative.model_dump_internal(mode='json') serializes status as string.

    In adcp 3.6.0, status is an internal field excluded from the public API
    (model_dump). It is accessible via model_dump_internal() for DB storage
    and internal processing.
    """
    creative = Creative(
        creative_id="test_creative_1",
        variants=[],
        name="Test Creative",
        format_id=FormatId(id="display_300x250", agent_url="https://creative.adcontextprotocol.org"),
        status=CreativeStatus.approved,
        created_date=datetime.now(UTC),
        updated_date=datetime.now(UTC),
    )

    # Status is internal — use model_dump_internal to access it
    data = creative.model_dump_internal(mode="json")

    assert isinstance(data["status"], str), f"Expected str, got {type(data['status'])}"
    assert data["status"] == "approved"


def test_creative_model_dump_internal_includes_principal_id():
    """Test that model_dump_internal includes excluded internal fields."""
    creative = Creative(
        creative_id="test_creative_2",
        variants=[],
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
    # model_dump_internal() serializes status to string value (not enum object)
    assert data["status"] == CreativeStatus.pending_review.value


def test_creative_all_status_values_at_boundary():
    """Test that all CreativeStatus enum values serialize to strings at boundaries.

    In adcp 3.6.0, status is internal-only, accessed via model_dump_internal().
    """
    statuses = [
        CreativeStatus.approved,
        CreativeStatus.rejected,
        CreativeStatus.pending_review,
        CreativeStatus.processing,
    ]

    for status_enum in statuses:
        creative = Creative(
            creative_id=f"test_{status_enum.value}",
            variants=[],
            name=f"Test {status_enum.value}",
            format_id=FormatId(id="display_300x250", agent_url="https://creative.adcontextprotocol.org"),
            status=status_enum,
            created_date=datetime.now(UTC),
            updated_date=datetime.now(UTC),
        )

        # Status is internal — use model_dump_internal for DB storage
        data = creative.model_dump_internal(mode="json")

        assert isinstance(data["status"], str), f"Status should be str for {status_enum}"
        assert data["status"] == status_enum.value


def test_creative_status_string_passthrough():
    """Test that passing status as string round-trips through enum and back."""
    creative = Creative(
        creative_id="test_creative_string",
        variants=[],
        name="Test Creative String",
        format_id=FormatId(id="display_300x250", agent_url="https://creative.adcontextprotocol.org"),
        status="approved",  # String → Pydantic coerces to enum
        created_date=datetime.now(UTC),
        updated_date=datetime.now(UTC),
    )

    # Verify Pydantic coerced string to enum internally
    assert creative.status == CreativeStatus.approved

    # Status is internal in adcp 3.6.0 — use model_dump_internal at boundaries
    data = creative.model_dump_internal(mode="json")
    assert isinstance(data["status"], str)
    assert data["status"] == "approved"
