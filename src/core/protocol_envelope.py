"""Protocol envelope wrapper for AdCP responses per AdCP v2.4 spec.

This module implements the protocol envelope pattern defined in:
https://adcontextprotocol.org/schemas/v1/core/protocol-envelope.json

The envelope separates protocol-level concerns (status, task_id, context_id, message)
from domain response data (payload). This allows the same domain response models to work
across different transport layers (MCP, A2A, REST) without embedding protocol fields
in business logic.

Architecture:
    Protocol Envelope (added by transport layer)
    ├── status: Task execution state
    ├── message: Human-readable summary
    ├── task_id: Async operation tracking
    ├── context_id: Session/conversation tracking
    ├── timestamp: Response generation time
    ├── push_notification_config: Webhook configuration (optional)
    └── payload: Domain-specific response data (from schemas.py models)

Usage:
    # In MCP tool:
    domain_response = CreateMediaBuyResponse(buyer_ref="...", packages=[...])
    envelope = ProtocolEnvelope.wrap(
        payload=domain_response,
        status="completed",
        message="Media buy created successfully"
    )

    # In A2A handler:
    envelope = ProtocolEnvelope.wrap(
        payload=get_products_response,
        status="completed",
        context_id=conversation_id
    )
"""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.core.schemas import AdCPBaseModel

# Task status values per AdCP spec
TaskStatus = Literal[
    "submitted",  # Task queued for async processing
    "working",  # Task in progress (< 120s, supports streaming)
    "completed",  # Task finished successfully
    "failed",  # Task failed with errors
    "input-required",  # Task needs user input to proceed
    "canceled",  # Task canceled by user
    "rejected",  # Task rejected before starting
    "auth-required",  # Task needs authentication/authorization
]


class ProtocolEnvelope(BaseModel):
    """Protocol envelope for AdCP task responses.

    This envelope is added by the protocol layer (MCP, A2A, REST) and wraps
    task-specific response payloads. Task response schemas should NOT include
    these fields - they are protocol-level concerns.

    Per AdCP v2.4 spec: /schemas/v1/core/protocol-envelope.json
    """

    # Required fields
    status: TaskStatus = Field(
        ...,
        description="Current task execution state. Indicates whether the task is completed, "
        "in progress (working), submitted for async processing, failed, or requires user input.",
    )

    payload: dict[str, Any] = Field(
        ...,
        description="The actual task-specific response data. Contains only domain-specific "
        "data without protocol-level fields.",
    )

    # Optional fields
    context_id: str | None = Field(
        None,
        description="Session/conversation identifier for tracking related operations across "
        "multiple task invocations. Managed by the protocol layer.",
    )

    task_id: str | None = Field(
        None,
        description="Unique identifier for tracking asynchronous operations. Present when a task "
        "requires extended processing time. Used to query task status and retrieve results.",
    )

    message: str | None = Field(
        None,
        description="Human-readable summary of the task result. Provides natural language "
        "explanation suitable for display to end users or for AI agent comprehension.",
    )

    timestamp: datetime | None = Field(
        None,
        description="ISO 8601 timestamp when the response was generated. Useful for debugging, "
        "logging, cache validation, and tracking async operation progress.",
    )

    push_notification_config: dict[str, Any] | None = Field(
        None,
        description="Push notification configuration for async task updates (A2A and REST protocols). "
        "Echoed from the request to confirm webhook settings.",
    )

    @classmethod
    def wrap(
        cls,
        payload: AdCPBaseModel | dict[str, Any],
        status: TaskStatus,
        message: str | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
        push_notification_config: dict[str, Any] | None = None,
        add_timestamp: bool = True,
    ) -> "ProtocolEnvelope":
        """Wrap a domain response in a protocol envelope.

        Args:
            payload: Domain-specific response (Pydantic model or dict)
            status: Task execution status
            message: Human-readable result summary (optional, generated from payload if not provided)
            task_id: Async operation tracking ID (optional)
            context_id: Session/conversation ID (optional)
            push_notification_config: Webhook configuration (optional)
            add_timestamp: Whether to add current timestamp (default: True)

        Returns:
            ProtocolEnvelope wrapping the payload

        Example:
            >>> response = CreateMediaBuyResponse(buyer_ref="ref123", packages=[...])
            >>> envelope = ProtocolEnvelope.wrap(
            ...     payload=response,
            ...     status="completed",
            ...     message="Media buy created successfully"
            ... )
        """
        # Convert Pydantic model to dict at serialization boundary
        if hasattr(payload, "model_dump"):
            payload_dict = payload.model_dump(mode="json")
            # Generate message from __str__ if not provided
            if message is None and hasattr(payload, "__str__"):
                message = str(payload)
        else:
            payload_dict = payload

        # Add timestamp
        timestamp = datetime.now(UTC) if add_timestamp else None

        return cls(
            status=status,
            payload=payload_dict,
            message=message,
            task_id=task_id,
            context_id=context_id,
            timestamp=timestamp,
            push_notification_config=push_notification_config,
        )

    def model_dump(self, **kwargs) -> dict[str, Any]:
        """Dump envelope to dict, excluding None values by default."""
        # Exclude None values for cleaner JSON output
        if "exclude_none" not in kwargs:
            kwargs["exclude_none"] = True
        return super().model_dump(**kwargs)
