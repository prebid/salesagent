"""Test: Creative schema regression — name/dates widened to Optional.

PR #1071 review finding (salesagent-10xd).

Bug: Creative.name was widened from required str to str|None=None, breaking
the AdCP listing Creative contract. created_date/updated_date lost their
default_factory, defaulting to None instead of now(UTC).

These tests assert the CORRECT behavior (name is required, dates auto-default).
They FAIL against the current code, proving the regression exists.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.core.schemas import Creative


class TestCreativeSchemaRegression:
    """Tests for Creative schema field contracts per AdCP listing spec."""

    def test_creative_name_is_required(self):
        """Creative.name must be a required str — omitting it should raise ValidationError.

        AdCP listing Creative spec: name is Annotated[str, Field(...)], no default.
        Our override made it str | None = None, silently accepting None values
        that then crash at DB INSERT (NOT NULL constraint on creatives.name).
        """
        with pytest.raises(ValidationError):
            # Omitting name should fail validation, not silently default to None
            Creative(
                creative_id="test_123",
                format_id={
                    "agent_url": "https://creative.adcontextprotocol.org",
                    "id": "display_300x250",
                },
                variants=[],
                status="pending_review",
                created_date=datetime.now(UTC),
                updated_date=datetime.now(UTC),
            )

    def test_creative_name_cannot_be_none(self):
        """Creative.name=None should be rejected by Pydantic validation.

        Library Creative requires str. Our override accepts None, violating
        Liskov substitution and the AdCP contract.
        """
        with pytest.raises(ValidationError):
            Creative(
                creative_id="test_123",
                name=None,
                format_id={
                    "agent_url": "https://creative.adcontextprotocol.org",
                    "id": "display_300x250",
                },
                variants=[],
                status="pending_review",
                created_date=datetime.now(UTC),
                updated_date=datetime.now(UTC),
            )

    def test_creative_dates_have_defaults(self):
        """created_date and updated_date should auto-default to now(UTC).

        Library Creative requires AwareDatetime (no default). Our override
        changed to datetime | None = None, losing the default_factory that
        previously provided automatic timestamps. New creatives get None dates
        in AdCP responses, violating the spec.
        """
        creative = Creative(
            creative_id="test_123",
            name="Test Creative",
            format_id={
                "agent_url": "https://creative.adcontextprotocol.org",
                "id": "display_300x250",
            },
            variants=[],
            status="pending_review",
        )

        # Dates should auto-default to timestamps, not None
        assert creative.created_date is not None, "created_date should auto-default to now(UTC), not None"
        assert creative.updated_date is not None, "updated_date should auto-default to now(UTC), not None"
        assert isinstance(creative.created_date, datetime)
        assert isinstance(creative.updated_date, datetime)

    def test_creative_with_valid_name_succeeds(self):
        """Creative with a valid name string should be accepted."""
        creative = Creative(
            creative_id="test_123",
            name="Valid Creative Name",
            format_id={
                "agent_url": "https://creative.adcontextprotocol.org",
                "id": "display_300x250",
            },
            variants=[],
            status="pending_review",
            created_date=datetime.now(UTC),
            updated_date=datetime.now(UTC),
        )
        assert creative.name == "Valid Creative Name"
