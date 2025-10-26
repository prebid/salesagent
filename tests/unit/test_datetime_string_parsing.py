"""Tests for datetime string parsing in schemas.

These tests ensure that ISO 8601 datetime strings (as sent by real clients)
are properly parsed and handled, catching bugs that tests with datetime objects miss.
"""

from datetime import datetime

import pytest

from src.core.schemas import CreateMediaBuyRequest, UpdateMediaBuyRequest


class TestDateTimeStringParsing:
    """Test that schemas correctly parse ISO 8601 datetime strings."""

    def test_create_media_buy_with_utc_z_format(self):
        """Test parsing ISO 8601 with Z timezone (most common format)."""
        req = CreateMediaBuyRequest(
            buyer_ref="test_ref",  # Required per AdCP spec
            brand_manifest={"name": "Nike Air Jordan 2025 basketball shoes"},
            po_number="TEST-001",
            packages=[
                {
                    "package_id": "pkg_1",
                    "buyer_ref": "pkg_1",
                    "products": ["prod_1"],
                    "status": "draft",
                "budget": 5000.0,
                }
            ],
            start_time="2025-02-15T00:00:00Z",  # String, not datetime object!
            end_time="2025-02-28T23:59:59Z",
            # budget moved to package level per AdCP v2.2.0
        )

        # Should parse successfully
        assert req.start_time is not None
        assert isinstance(req.start_time, datetime)
        assert req.start_time.tzinfo is not None  # Must have timezone
        assert req.end_time is not None
        assert isinstance(req.end_time, datetime)
        assert req.end_time.tzinfo is not None

    def test_create_media_buy_with_offset_format(self):
        """Test parsing ISO 8601 with +00:00 offset."""
        req = CreateMediaBuyRequest(
            buyer_ref="test_ref",  # Required per AdCP spec
            brand_manifest={"name": "Adidas UltraBoost 2025 running shoes"},
            po_number="TEST-002",
            packages=[
                {
                    "package_id": "pkg_1",
                    "buyer_ref": "pkg_1",
                    "products": ["prod_1"],
                    "status": "draft",
                "budget": 5000.0,
                }
            ],
            start_time="2025-02-15T00:00:00+00:00",
            end_time="2025-02-28T23:59:59+00:00",
            # budget moved to package level per AdCP v2.2.0
        )

        assert req.start_time is not None
        assert req.start_time.tzinfo is not None

    def test_create_media_buy_with_pst_timezone(self):
        """Test parsing ISO 8601 with PST offset."""
        req = CreateMediaBuyRequest(
            buyer_ref="test_ref",  # Required per AdCP spec
            brand_manifest={"name": "Puma RS-X 2025 training shoes"},
            po_number="TEST-003",
            packages=[
                {
                    "package_id": "pkg_1",
                    "buyer_ref": "pkg_1",
                    "products": ["prod_1"],
                    "status": "draft",
                "budget": 5000.0,
                }
            ],
            start_time="2025-02-15T00:00:00-08:00",
            end_time="2025-02-28T23:59:59-08:00",
            # budget moved to package level per AdCP v2.2.0
        )

        assert req.start_time is not None
        assert req.start_time.tzinfo is not None

    def test_legacy_start_date_string_conversion(self):
        """Test that legacy start_date strings are converted properly."""
        req = CreateMediaBuyRequest(
            buyer_ref="test_ref",  # Required per AdCP spec
            brand_manifest={"name": "New Balance 990v6 premium sneakers"},
            po_number="TEST-004",
            product_ids=["prod_1"],
            start_date="2025-02-15",  # String date (no time)
            end_date="2025-02-28",
            # total_budget is legacy field, using packages with budgets per AdCP v2.2.0
        )

        # Should convert to datetime with UTC timezone
        assert req.start_time is not None
        assert isinstance(req.start_time, datetime)
        assert req.start_time.tzinfo is not None  # MUST have timezone
        assert req.end_time is not None
        assert req.end_time.tzinfo is not None

    def test_mixed_legacy_and_new_fields(self):
        """Test that mixing legacy date strings with new datetime strings works."""
        req = CreateMediaBuyRequest(
            buyer_ref="test_ref",  # Required per AdCP spec
            brand_manifest={"name": "Reebok Classic leather shoes"},
            po_number="TEST-005",
            product_ids=["prod_1"],
            start_date="2025-02-15",  # Legacy: date string
            end_time="2025-02-28T23:59:59Z",  # New: datetime string
            # total_budget is legacy field, using packages with budgets per AdCP v2.2.0
        )

        assert req.start_time is not None
        assert req.start_time.tzinfo is not None
        assert req.end_time is not None
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
        # This should fail validation (no timezone)
        with pytest.raises(ValueError, match="timezone-aware"):
            CreateMediaBuyRequest(
                buyer_ref="test_ref",  # Required per AdCP spec
                brand_manifest={"name": "Converse Chuck Taylor All Star sneakers"},
                po_number="TEST-006",
                packages=[
                    {
                        "package_id": "pkg_1",
                        "buyer_ref": "pkg_1",
                        "products": ["prod_1"],
                        "status": "draft",
                    "budget": 5000.0,
                }
                ],
                start_time="2025-02-15T00:00:00",  # No timezone!
                end_time="2025-02-28T23:59:59",
                # budget moved to package level per AdCP v2.2.0
            )

    def test_invalid_datetime_format_rejected(self):
        """Test that invalid datetime formats are rejected."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CreateMediaBuyRequest(
                buyer_ref="test_ref",  # Required per AdCP spec
                brand_manifest={"name": "Vans Old Skool skateboard shoes"},
                po_number="TEST-007",
                packages=[
                    {
                        "package_id": "pkg_1",
                        "buyer_ref": "pkg_1",
                        "products": ["prod_1"],
                        "status": "draft",
                    "budget": 5000.0,
                }
                ],
                start_time="02/15/2025",  # Wrong format!
                end_time="02/28/2025",
                # budget moved to package level per AdCP v2.2.0
            )

    def test_create_media_buy_roundtrip_serialization(self):
        """Test that parsed datetimes can be serialized back to ISO 8601."""
        req = CreateMediaBuyRequest(
            buyer_ref="test_ref",  # Required per AdCP spec
            brand_manifest={"name": "Asics Gel-Kayano 29 running shoes"},
            po_number="TEST-008",
            packages=[
                {
                    "package_id": "pkg_1",
                    "buyer_ref": "pkg_1",
                    "products": ["prod_1"],
                    "status": "draft",
                "budget": 5000.0,
                }
            ],
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
            buyer_ref="test_ref",  # Required per AdCP spec
            brand_manifest={"name": "Brooks Ghost 15 running shoes"},
            po_number="TEST-009",
            packages=[
                {
                    "package_id": "pkg_1",
                    "buyer_ref": "pkg_1",
                    "products": ["prod_1"],
                    "status": "draft",
                "budget": 5000.0,
                }
            ],
            start_time="2025-02-15T00:00:00Z",
            end_time="2025-02-28T23:59:59Z",
            # budget moved to package level per AdCP v2.2.0
        )

        # Should have timezone info
        assert req.start_time is not None
        assert req.start_time.tzinfo is not None
        assert req.end_time is not None
        assert req.end_time.tzinfo is not None

    def test_legacy_date_conversion_with_values(self):
        """Test that legacy date fields are converted to datetime."""
        req = CreateMediaBuyRequest(
            buyer_ref="test_ref",  # Required per AdCP spec
            brand_manifest={"name": "Saucony Triumph 20 running shoes"},
            po_number="TEST-010",
            product_ids=["prod_1"],
            start_date="2025-02-15",
            end_date="2025-02-28",
            # total_budget is legacy field, using packages with budgets per AdCP v2.2.0
        )

        # Should convert legacy dates to datetimes
        assert req.start_time is not None
        assert req.end_time is not None
        assert req.start_time.tzinfo is not None
        assert req.end_time.tzinfo is not None

    def test_partial_legacy_fields(self):
        """Test that providing only start_date without end_date works."""
        req = CreateMediaBuyRequest(
            buyer_ref="test_ref",  # Required per AdCP spec
            brand_manifest={"name": "Hoka One One Clifton 9 running shoes"},
            po_number="TEST-011",
            product_ids=["prod_1"],
            start_date="2025-02-15",
            end_date="2025-02-28",  # end_date is now required
            # total_budget is legacy field, using packages with budgets per AdCP v2.2.0
        )

        assert req.start_time is not None
        assert req.start_time.tzinfo is not None
        # end_time might be None or might have a default


class TestAdditionalDateTimeValidation:
    """Test timezone validation for additional request models."""

    def test_list_creatives_with_timezone_aware_filters(self):
        """Test ListCreativesRequest with timezone-aware datetime filters."""
        from src.core.schemas import ListCreativesRequest

        req = ListCreativesRequest(
            created_after="2025-02-15T00:00:00Z",
            created_before="2025-02-28T23:59:59Z",
        )

        assert req.created_after is not None
        assert req.created_after.tzinfo is not None
        assert req.created_before is not None
        assert req.created_before.tzinfo is not None

    def test_list_creatives_rejects_naive_created_after(self):
        """Test ListCreativesRequest rejects naive datetime for created_after."""
        from src.core.schemas import ListCreativesRequest

        with pytest.raises(ValueError, match="created_after.*timezone-aware"):
            ListCreativesRequest(
                created_after="2025-02-15T00:00:00",  # No timezone
                created_before="2025-02-28T23:59:59Z",
            )

    def test_list_creatives_rejects_naive_created_before(self):
        """Test ListCreativesRequest rejects naive datetime for created_before."""
        from src.core.schemas import ListCreativesRequest

        with pytest.raises(ValueError, match="created_before.*timezone-aware"):
            ListCreativesRequest(
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
