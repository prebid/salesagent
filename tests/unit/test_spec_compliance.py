"""Tests for spec compliance after context management improvements."""

import pytest

from src.core.async_patterns import (
    AsyncTask,
    TaskState,
    TaskStatus,
    is_async_operation,
)
from src.core.schemas import (
    CreateMediaBuyResponse,
    Error,
    GetProductsResponse,
    ListCreativeFormatsResponse,
)


class TestResponseSchemas:
    """Test that response schemas are spec-compliant."""

    def test_create_media_buy_response_no_context_id(self):
        """Verify CreateMediaBuyResponse doesn't have context_id."""
        response = CreateMediaBuyResponse(media_buy_id="buy_123", buyer_ref="ref_456", status="completed", packages=[])

        # Verify context_id is not in the schema
        assert not hasattr(response, "context_id")

        # Verify new fields are present
        assert response.status == "completed"
        assert response.buyer_ref == "ref_456"

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
            Format(format_id="display_300x250", name="Medium Rectangle", type="display"),
            Format(format_id="video_16x9", name="16:9 Video", type="video"),
        ]
        response = ListCreativeFormatsResponse(formats=test_formats)

        # Verify context_id is not in the schema
        assert not hasattr(response, "context_id")

        # Verify AdCP-compliant fields
        assert len(response.formats) == 2
        assert response.formats[0].format_id == "display_300x250"
        assert response.formats[1].format_id == "video_16x9"

        # Verify message is provided via __str__() not as schema field
        assert not hasattr(response, "message")
        assert str(response) == "Found 2 creative formats."

    def test_error_reporting_in_responses(self):
        """Verify error reporting is protocol-compliant."""
        response = CreateMediaBuyResponse(
            media_buy_id="",
            buyer_ref="ref_123",
            status="input-required",
            errors=[Error(code="validation_error", message="Validation error", details={"budget": -100})],
        )

        assert response.status == "input-required"
        assert response.errors is not None
        assert len(response.errors) == 1
        assert response.errors[0].code == "validation_error"


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
    """Test protocol compliance."""

    def test_create_media_buy_async_states(self):
        """Test that create_media_buy response handles async states correctly."""
        # Pending approval state (use "submitted" for async operations)
        response = CreateMediaBuyResponse(
            media_buy_id="pending_123",
            buyer_ref="ref_123",
            status="submitted",
            task_id="task_456",
        )

        assert response.status == "submitted"
        assert response.task_id == "task_456"

        # Input required state
        response = CreateMediaBuyResponse(
            media_buy_id="",
            buyer_ref="ref_123",
            status="input-required",
            errors=[Error(code="invalid_budget", message="Invalid budget")],
        )

        assert response.status == "input-required"
        assert response.errors is not None
        assert response.media_buy_id == ""  # Empty on failure

        # Success state
        response = CreateMediaBuyResponse(
            media_buy_id="buy_456",
            buyer_ref="ref_789",
            status="completed",
            packages=[{"package_id": "pkg_1"}],
        )

        assert response.status == "completed"
        assert response.media_buy_id == "buy_456"
        assert len(response.packages) == 1
        assert response.errors is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
