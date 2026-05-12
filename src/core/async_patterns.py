"""Async operation patterns for MCP tools following A2A Task model.

This module defines clear patterns for synchronous vs asynchronous operations
in the MCP server, following the A2A protocol's Task-based approach.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

# Generic type for the result of an async operation
T = TypeVar("T", bound=BaseModel)


class TaskState(str, Enum):
    """Task lifecycle states following A2A specification."""

    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input_required"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"
    REJECTED = "rejected"
    AUTH_REQUIRED = "auth_required"
    UNKNOWN = "unknown"
    # Custom states for our use case
    PENDING_APPROVAL = "pending_approval"  # Waiting for human approval


class TaskStatus(BaseModel):
    """Status of a task at a specific point in time."""

    state: TaskState
    message: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    progress: float | None = None  # 0.0 to 1.0


class AsyncTask(BaseModel, Generic[T]):
    """Generic async task following A2A Task pattern.

    This represents an operation that doesn't complete immediately and
    requires tracking through various states until completion.
    """

    task_id: str
    task_type: str  # e.g., "media_buy_creation", "bulk_creative_upload"
    status: TaskStatus
    result: T | None = None  # The actual result when completed
    error: dict[str, Any] | None = None  # Error details if failed
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    def is_complete(self) -> bool:
        """Check if the task has reached a terminal state."""
        return self.status.state in [TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED, TaskState.REJECTED]

    def is_success(self) -> bool:
        """Check if the task completed successfully."""
        return self.status.state == TaskState.COMPLETED

    def needs_input(self) -> bool:
        """Check if the task is waiting for input."""
        return self.status.state in [TaskState.INPUT_REQUIRED, TaskState.PENDING_APPROVAL, TaskState.AUTH_REQUIRED]


class AsyncOperationResponse(BaseModel):
    """Standard response for async operations.

    When an operation is asynchronous, it returns this response immediately
    with a task_id that can be used to track progress.
    """

    task_id: str
    status: TaskStatus
    message: str | None = None
    poll_after_seconds: int | None = Field(default=5, description="Suggested wait time before polling for status")
    estimated_completion_seconds: int | None = None


class SyncOperationResponse(BaseModel, Generic[T]):
    """Standard response for synchronous operations.

    Synchronous operations complete immediately and return the result directly.
    No status tracking is needed.
    """

    result: T
    message: str | None = None
    warnings: list[str] | None = None


# Operation classification helpers


def is_async_operation(operation_name: str) -> bool:
    """Determine if an operation should be async based on its nature.

    Operations that should be async:
    - Create/update operations that require external API calls
    - Operations that may require human approval
    - Bulk operations that process many items
    - Long-running analytical operations

    Operations that should be sync:
    - Read/query operations (get_products, list_formats)
    - Simple status checks
    - Validation operations
    """
    async_operations = {
        "create_media_buy",  # May require approval, external API calls
        "update_media_buy",  # May require re-approval
        "bulk_upload_creatives",  # Processing many items
        "generate_performance_report",  # Long-running analysis
        "submit_for_approval",  # Human-in-the-loop
    }

    sync_operations = {
        "get_products",  # Simple query
        "list_creative_formats",  # Simple list
        "check_media_buy_status",  # Status check (though returns async task info)
        "get_signals",  # Discovery operation
        "validate_targeting",  # Quick validation
    }

    if operation_name in async_operations:
        return True
    elif operation_name in sync_operations:
        return False
    else:
        # Default: if it starts with "create", "update", "delete", it's probably async
        prefixes = ["create", "update", "delete", "submit", "process", "generate"]
        return any(operation_name.startswith(prefix) for prefix in prefixes)


# Specific async task types for our domain


class MediaBuyCreationTask(AsyncTask):
    """Task for tracking media buy creation."""

    task_type: str = "media_buy_creation"
    result: "CreateMediaBuyResult | None" = None


class CreativeUploadTask(AsyncTask):
    """Task for tracking creative upload/approval."""

    task_type: str = "creative_upload"
    result: "CreativeUploadResult | None" = None


# Result types for completed async operations


class CreateMediaBuyResult(BaseModel):
    """Result of a completed media buy creation."""

    media_buy_id: str
    packages: list[dict[str, Any]]
    creative_deadline: datetime | None = None
    total_budget: float
    status: str = "active"  # active, paused, etc.


class CreativeUploadResult(BaseModel):
    """Result of completed creative upload."""

    creative_id: str
    status: str  # approved, rejected
    review_notes: str | None = None
