"""Tests for spec compliance after context management improvements."""

import pytest

from src.core.async_patterns import (
    AsyncTask,
    TaskState,
    TaskStatus,
    is_async_operation,
)
from src.core.schemas import (
    CreateMediaBuySuccess,
    FormatId,
    GetProductsResponse,
    ListCreativeFormatsResponse,
)

DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"


def make_format_id(format_id: str) -> FormatId:
    """Helper to create FormatId objects."""
    return FormatId(agent_url=DEFAULT_AGENT_URL, id=format_id)


class TestResponseSchemas:
    """Test that response schemas are spec-compliant."""

    def test_create_media_buy_response_no_protocol_fields(self):
        """Verify CreateMediaBuyResponse has only domain fields (no protocol fields)."""
        response = CreateMediaBuySuccess(media_buy_id="buy_123", buyer_ref="ref_456", packages=[])

        # Verify protocol fields are not in the schema (moved to ProtocolEnvelope)
        assert not hasattr(response, "context_id")
        assert not hasattr(response, "status")
        assert not hasattr(response, "task_id")
        assert not hasattr(response, "message")

        # Verify domain fields are present
        assert response.buyer_ref == "ref_456"
        assert response.media_buy_id == "buy_123"

    def test_get_products_response_no_context_id(self):
        """Verify GetProductsResponse doesn't have context_id."""
        response = GetProductsResponse(products=[])

        # Verify context_id is not in the schema
        assert not hasattr(response, "context_id")

        # Verify AdCP-compliant fields are present
        assert response.products == []

        # Verify message is provided via __str__() not as schema field
        assert not hasattr(response, "message")
        assert str(response) == "No products matched your requirements."

    def test_list_creative_formats_response_no_context_id(self):
        """Verify ListCreativeFormatsResponse doesn't have context_id."""
        from src.core.schemas import Format

        test_formats = [
            Format(format_id=make_format_id("display_300x250"), name="Medium Rectangle", type="display"),
            Format(format_id=make_format_id("video_16x9"), name="16:9 Video", type="video"),
        ]
        response = ListCreativeFormatsResponse(formats=test_formats)

        # Verify context_id is not in the schema
        assert not hasattr(response, "context_id")

        # Verify AdCP-compliant fields
        assert len(response.formats) == 2
        assert response.formats[0].format_id.id == "display_300x250"
        assert response.formats[1].format_id.id == "video_16x9"

        # Verify message is provided via __str__() not as schema field
        assert not hasattr(response, "message")
        assert str(response) == "Found 2 creative formats."

    def test_error_reporting_in_responses(self):
        """Verify error reporting is AdCP-compliant (domain data only)."""
        from src.core.schemas import CreateMediaBuyError

        response = CreateMediaBuyError(
            errors=[{"code": "validation_error", "message": "Validation error", "details": {"budget": -100}}],
        )

        # Verify domain fields
        assert response.errors is not None
        assert len(response.errors) == 1
        assert response.errors[0].code == "validation_error"

        # Verify no protocol fields
        assert not hasattr(response, "status")


class TestAsyncPatterns:
    """Test async operation patterns."""

    def test_task_state_enum(self):
        """Verify TaskState enum has all A2A states."""
        expected_states = {
            "submitted",
            "working",
            "input_required",
            "completed",
            "canceled",
            "failed",
            "rejected",
            "auth_required",
            "unknown",
        }

        actual_states = {state.value for state in TaskState}

        # All A2A states should be present
        assert expected_states.issubset(actual_states)

        # We added pending_approval as custom state
        assert TaskState.PENDING_APPROVAL.value == "pending_approval"

    def test_async_task_model(self):
        """Test AsyncTask model functionality."""
        task = AsyncTask(
            task_id="task_123", task_type="media_buy_creation", status=TaskStatus(state=TaskState.WORKING), result=None
        )

        assert task.task_id == "task_123"
        assert not task.is_complete()
        assert not task.is_success()
        assert not task.needs_input()

        # Update to completed
        task.status.state = TaskState.COMPLETED
        assert task.is_complete()
        assert task.is_success()

        # Update to pending approval
        task.status.state = TaskState.PENDING_APPROVAL
        assert not task.is_complete()
        assert task.needs_input()

    def test_operation_classification(self):
        """Test classification of operations as sync vs async."""
        # Async operations
        assert is_async_operation("create_media_buy") is True
        assert is_async_operation("update_media_buy") is True
        assert is_async_operation("bulk_upload_creatives") is True

        # Sync operations
        assert is_async_operation("get_products") is False
        assert is_async_operation("list_creative_formats") is False
        assert is_async_operation("check_media_buy_status") is False

        # Default behavior - create/update/delete are async
        assert is_async_operation("create_campaign") is True
        assert is_async_operation("update_settings") is True
        assert is_async_operation("delete_creative") is True
        assert is_async_operation("fetch_data") is False


class TestProtocolCompliance:
    """Test protocol compliance - domain responses only."""

    def test_create_media_buy_response_domain_fields(self):
        """Test that create_media_buy response contains only domain fields."""
        # Response with media_buy_id (success case)
        response = CreateMediaBuySuccess(
            media_buy_id="pending_123",
            buyer_ref="ref_123",
            packages=[],
        )

        # Domain fields present
        assert response.media_buy_id == "pending_123"
        assert response.buyer_ref == "ref_123"

        # Protocol fields NOT present (moved to ProtocolEnvelope)
        assert not hasattr(response, "status")
        assert not hasattr(response, "task_id")

        # Error case
        from src.core.schemas import CreateMediaBuyError

        error_response = CreateMediaBuyError(
            errors=[{"code": "invalid_budget", "message": "Invalid budget"}],
        )

        assert error_response.errors is not None
        assert len(error_response.errors) == 1

        # Success case with packages
        response = CreateMediaBuySuccess(
            media_buy_id="buy_456",
            buyer_ref="ref_789",
            packages=[{"buyer_ref": "ref_789", "package_id": "pkg_1"}],
        )

        assert response.media_buy_id == "buy_456"
        assert response.buyer_ref == "ref_789"
        assert len(response.packages) == 1
        assert not hasattr(response, "status")  # Protocol field removed


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
