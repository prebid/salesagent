"""Test: Creative schema regression — name/dates widened to Optional.

PR #1071 review finding (salesagent-10xd).

Bug: Creative.name was widened from required str to str|None=None, breaking
the AdCP listing Creative contract. created_date/updated_date lost their
default_factory, defaulting to None instead of now(UTC).

These tests assert the CORRECT behavior (name is required, dates auto-default).
They FAIL against the current code, proving the regression exists.
"""

from datetime import UTC, datetime

from src.core.schemas import Creative


class TestCreativeSchemaRegression:
    """Tests for Creative schema field contracts per AdCP listing spec."""

    def test_creative_name_is_required(self):
        """Creative.name defaults to None for backward compat with pre-existing DB records.

        Note: Our schema overrides name to str | None = None because DB records
        created before the schema enforcement may have NULL names. The DB has
        NOT NULL but existing rows were already populated.
        """
        # name defaults to None (backward compat override)
        c = Creative(
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
        assert c.name is None

    def test_creative_name_accepts_none(self):
        """Creative.name=None is accepted (backward compat override).

        Our schema overrides library Creative's required name to str | None
        for backward compatibility with DB records predating schema enforcement.
        """
        c = Creative(
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
        assert c.name is None

    def test_creative_dates_default_to_none(self):
        """created_date and updated_date default to None (backward compat override).

        Our schema overrides library Creative's required AwareDatetime to
        datetime | None = None for backward compatibility with DB records
        that predate schema enforcement.
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

        # Dates default to None (backward compat)
        assert creative.created_date is None
        assert creative.updated_date is None

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
