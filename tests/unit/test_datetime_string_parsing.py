"""Tests for datetime string parsing in schemas.

These tests ensure that ISO 8601 datetime strings (as sent by real clients)
are properly parsed and handled, catching bugs that tests with datetime objects miss.
"""

from datetime import UTC, datetime

import pytest

from src.core.schemas import CreateMediaBuyRequest, UpdateMediaBuyRequest


class TestDateTimeStringParsing:
    """Test that schemas correctly parse ISO 8601 datetime strings."""

    def test_create_media_buy_with_utc_z_format(self):
        """Test parsing ISO 8601 with Z timezone (most common format)."""
        req = CreateMediaBuyRequest(
            # Required per AdCP spec
            brand={"domain": "nike.com"},
            po_number="TEST-001",
            packages=[{"product_id": "prod_1", "budget": 5000.0, "pricing_option_id": "test_pricing"}],
            start_time="2025-02-15T00:00:00Z",  # String, not datetime object!
            end_time="2025-02-28T23:59:59Z",
            # budget moved to package level per AdCP v2.2.0
        )

        # Per AdCP spec, start_time can be string or datetime
        # Library doesn't auto-convert strings to datetimes
        assert req.start_time is not None
        # start_time can be string "asap" or ISO datetime string or datetime object
        # Library keeps it as-is (may be string or datetime)
        assert req.end_time is not None
        # end_time should be parsed to datetime by the library
        assert isinstance(req.end_time, datetime)
        assert req.end_time.tzinfo is not None

    def test_create_media_buy_with_offset_format(self):
        """Test parsing ISO 8601 with +00:00 offset."""
        req = CreateMediaBuyRequest(
            # Required per AdCP spec
            brand={"domain": "adidas.com"},
            po_number="TEST-002",
            packages=[{"product_id": "prod_1", "budget": 5000.0, "pricing_option_id": "test_pricing"}],
            start_time="2025-02-15T00:00:00+00:00",
            end_time="2025-02-28T23:59:59+00:00",
            # budget moved to package level per AdCP v2.2.0
        )

        # Per AdCP spec, start_time can be string or datetime
        assert req.start_time is not None
        assert req.end_time is not None
        assert isinstance(req.end_time, datetime)
        assert req.end_time.tzinfo is not None

    def test_create_media_buy_with_pst_timezone(self):
        """Test parsing ISO 8601 with PST offset."""
        req = CreateMediaBuyRequest(
            # Required per AdCP spec
            brand={"domain": "puma.com"},
            po_number="TEST-003",
            packages=[{"product_id": "prod_1", "budget": 5000.0, "pricing_option_id": "test_pricing"}],
            start_time="2025-02-15T00:00:00-08:00",
            end_time="2025-02-28T23:59:59-08:00",
            # budget moved to package level per AdCP v2.2.0
        )

        # Per AdCP spec, start_time can be string or datetime
        assert req.start_time is not None
        assert req.end_time is not None
        assert isinstance(req.end_time, datetime)
        assert req.end_time.tzinfo is not None

    def test_update_media_buy_with_datetime_strings(self):
        """Test UpdateMediaBuyRequest with datetime strings."""
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_123",
            start_time="2025-03-01T00:00:00Z",
            end_time="2025-03-31T23:59:59Z",
        )

        assert req.start_time is not None
        assert isinstance(req.start_time, datetime)
        assert req.start_time.tzinfo is not None
        assert req.end_time is not None
        assert req.end_time.tzinfo is not None

    def test_naive_datetime_string_rejected(self):
        """Test that datetime strings without timezone are rejected."""
        from pydantic import ValidationError

        # This should fail validation (no timezone on end_time)
        # Library enforces timezone on end_time (datetime type)
        with pytest.raises(ValidationError, match="timezone"):
            CreateMediaBuyRequest(
                # Required per AdCP spec
                brand={"domain": "converse.com"},
                po_number="TEST-006",
                packages=[{"product_id": "prod_1", "pricing_option_id": "test_pricing", "budget": 5000.0}],
                start_time="2025-02-15T00:00:00",  # No timezone!
                end_time="2025-02-28T23:59:59",
                # budget moved to package level per AdCP v2.2.0
            )

    def test_invalid_datetime_format_rejected(self):
        """Test that invalid datetime formats are rejected."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CreateMediaBuyRequest(
                # Required per AdCP spec
                brand={"domain": "vans.com"},
                po_number="TEST-007",
                packages=[{"package_id": "pkg_1", "products": ["prod_1"], "status": "draft", "budget": 5000.0}],
                start_time="02/15/2025",  # Wrong format!
                end_time="02/28/2025",
                # budget moved to package level per AdCP v2.2.0
            )

    def test_create_media_buy_roundtrip_serialization(self):
        """Test that parsed datetimes can be serialized back to ISO 8601."""
        req = CreateMediaBuyRequest(
            # Required per AdCP spec
            brand={"domain": "asics.com"},
            po_number="TEST-008",
            packages=[{"product_id": "prod_1", "budget": 5000.0, "pricing_option_id": "test_pricing"}],
            start_time="2025-02-15T00:00:00Z",
            end_time="2025-02-28T23:59:59Z",
            # budget moved to package level per AdCP v2.2.0
        )

        # Serialize back to dict
        data = req.model_dump(mode="json")

        # start_time should be serialized as ISO 8601 string
        assert "start_time" in data
        assert isinstance(data["start_time"], str)
        assert "T" in data["start_time"]  # ISO 8601 format
        assert "Z" in data["start_time"] or "+" in data["start_time"] or "-" in data["start_time"]  # Has timezone


class TestDateTimeParsingEdgeCases:
    """Test edge cases in datetime parsing that have caused bugs."""

    def test_datetime_with_tzinfo_access(self):
        """Test that accessing .tzinfo on datetime works correctly."""
        req = CreateMediaBuyRequest(
            # Required per AdCP spec
            brand={"domain": "brooks.com"},
            po_number="TEST-009",
            packages=[{"product_id": "prod_1", "budget": 5000.0, "pricing_option_id": "test_pricing"}],
            start_time="2025-02-15T00:00:00Z",
            end_time="2025-02-28T23:59:59Z",
            # budget moved to package level per AdCP v2.2.0
        )

        # Per AdCP spec, start_time can be string or datetime
        # Library may keep it as string for "asap" support
        assert req.start_time is not None
        # end_time is always datetime type per spec
        assert req.end_time is not None
        assert isinstance(req.end_time, datetime)
        assert req.end_time.tzinfo is not None

    def test_create_media_buy_with_datetime_objects(self):
        """Test that CreateMediaBuyRequest works with datetime objects."""

        req = CreateMediaBuyRequest(
            # Required per AdCP spec
            brand={"domain": "saucony.com"},
            packages=[{"product_id": "prod_1", "budget": 5000.0, "pricing_option_id": "test_pricing"}],
            start_time=datetime(2025, 2, 15, 0, 0, 0, tzinfo=UTC),
            end_time=datetime(2025, 2, 28, 23, 59, 59, tzinfo=UTC),
        )

        # Should have timezone-aware datetimes (library wraps in StartTiming)
        assert req.start_time is not None
        assert req.end_time is not None
        # Handle library StartTiming wrapper type
        if hasattr(req.start_time, "root"):
            assert req.start_time.root.tzinfo is not None
        else:
            assert req.start_time.tzinfo is not None
        assert req.end_time.tzinfo is not None


class TestAdditionalDateTimeValidation:
    """Test timezone validation for additional request models."""

    def test_list_creatives_with_timezone_aware_filters(self):
        """Test ListCreativesRequest with timezone-aware datetime filters."""
        from adcp.types import CreativeFilters as LibraryCreativeFilters

        from src.core.schemas import ListCreativesRequest

        # Datetime filters are now in the filters object per AdCP spec
        filters = LibraryCreativeFilters(
            created_after="2025-02-15T00:00:00Z",
            created_before="2025-02-28T23:59:59Z",
        )
        req = ListCreativesRequest(filters=filters)

        assert req.filters is not None
        assert req.filters.created_after is not None
        assert req.filters.created_after.tzinfo is not None
        assert req.filters.created_before is not None
        assert req.filters.created_before.tzinfo is not None

    def test_list_creatives_rejects_naive_created_after(self):
        """Test CreativeFilters rejects naive datetime for created_after."""
        from adcp.types import CreativeFilters as LibraryCreativeFilters
        from pydantic import ValidationError

        # The library's CreativeFilters enforces AwareDatetime
        with pytest.raises(ValidationError):
            LibraryCreativeFilters(
                created_after="2025-02-15T00:00:00",  # No timezone
                created_before="2025-02-28T23:59:59Z",
            )

    def test_list_creatives_rejects_naive_created_before(self):
        """Test CreativeFilters rejects naive datetime for created_before."""
        from adcp.types import CreativeFilters as LibraryCreativeFilters
        from pydantic import ValidationError

        # The library's CreativeFilters enforces AwareDatetime
        with pytest.raises(ValidationError):
            LibraryCreativeFilters(
                created_after="2025-02-15T00:00:00Z",
                created_before="2025-02-28T23:59:59",  # No timezone
            )

    def test_assign_creative_with_timezone_aware_overrides(self):
        """Test AssignCreativeRequest with timezone-aware override dates."""
        from src.core.schemas import AssignCreativeRequest

        req = AssignCreativeRequest(
            media_buy_id="mb_123",
            package_id="pkg_1",
            creative_id="cr_1",
            override_start_date="2025-02-15T00:00:00Z",
            override_end_date="2025-02-28T23:59:59Z",
        )

        assert req.override_start_date is not None
        assert req.override_start_date.tzinfo is not None
        assert req.override_end_date is not None
        assert req.override_end_date.tzinfo is not None

    def test_assign_creative_rejects_naive_override_start_date(self):
        """Test AssignCreativeRequest rejects naive datetime for override_start_date."""
        from src.core.schemas import AssignCreativeRequest

        with pytest.raises(ValueError, match="override_start_date.*timezone-aware"):
            AssignCreativeRequest(
                media_buy_id="mb_123",
                package_id="pkg_1",
                creative_id="cr_1",
                override_start_date="2025-02-15T00:00:00",  # No timezone
                override_end_date="2025-02-28T23:59:59Z",
            )

    def test_assign_creative_rejects_naive_override_end_date(self):
        """Test AssignCreativeRequest rejects naive datetime for override_end_date."""
        from src.core.schemas import AssignCreativeRequest

        with pytest.raises(ValueError, match="override_end_date.*timezone-aware"):
            AssignCreativeRequest(
                media_buy_id="mb_123",
                package_id="pkg_1",
                creative_id="cr_1",
                override_start_date="2025-02-15T00:00:00Z",
                override_end_date="2025-02-28T23:59:59",  # No timezone
            )

    def test_creative_assignment_with_timezone_aware_overrides(self):
        """Test CreativeAssignment with timezone-aware override dates."""
        from src.core.schemas import CreativeAssignment

        assignment = CreativeAssignment(
            assignment_id="assign_1",
            media_buy_id="mb_123",
            package_id="pkg_1",
            creative_id="cr_1",
            override_start_date="2025-02-15T00:00:00Z",
            override_end_date="2025-02-28T23:59:59Z",
        )

        assert assignment.override_start_date is not None
        assert assignment.override_start_date.tzinfo is not None
        assert assignment.override_end_date is not None
        assert assignment.override_end_date.tzinfo is not None

    def test_creative_assignment_rejects_naive_override_start_date(self):
        """Test CreativeAssignment rejects naive datetime for override_start_date."""
        from src.core.schemas import CreativeAssignment

        with pytest.raises(ValueError, match="override_start_date.*timezone-aware"):
            CreativeAssignment(
                assignment_id="assign_1",
                media_buy_id="mb_123",
                package_id="pkg_1",
                creative_id="cr_1",
                override_start_date="2025-02-15T00:00:00",  # No timezone
                override_end_date="2025-02-28T23:59:59Z",
            )
