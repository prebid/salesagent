"""
AdCP Testing API - Management and Control Interface

Provides endpoints for managing test sessions, inspecting testing state,
and controlling the testing environment.
"""

from datetime import UTC, datetime

from fastmcp.server.context import Context
from pydantic import BaseModel

from src.core.testing_hooks import (
    CampaignEvent,
    get_session_manager,
    get_testing_context,
)


class TestSessionInfo(BaseModel):
    """Information about a test session."""

    session_id: str
    created_at: datetime
    state: str
    media_buys_count: int
    total_spend: float


class TestingControlRequest(BaseModel):
    """Request to control testing features."""

    session_id: str | None = None
    action: str  # "create_session", "cleanup_session", "list_sessions", "get_capabilities"
    parameters: dict | None = None


class TestingControlResponse(BaseModel):
    """Response from testing control operations."""

    success: bool
    message: str
    data: dict | None = None


class TestingCapabilities(BaseModel):
    """Available testing capabilities."""

    supported_headers: list[str]
    lifecycle_events: list[str]
    error_scenarios: list[str]
    time_simulation: bool
    session_isolation: bool
    dry_run_mode: bool


def get_testing_capabilities() -> TestingCapabilities:
    """Get comprehensive testing capabilities."""
    return TestingCapabilities(
        supported_headers=[
            "X-Dry-Run",
            "X-Mock-Time",
            "X-Jump-To-Event",
            "X-Test-Session-ID",
            "X-Auto-Advance",
            "X-Simulated-Spend",
            "X-Force-Error",
            "X-Slow-Mode",
            "X-Debug-Mode",
        ],
        lifecycle_events=[e.value for e in CampaignEvent],
        error_scenarios=[
            "budget_exceeded",
            "low_delivery",
            "platform_error",
            "creative_policy_violation",
            "inventory_unavailable",
            "targeting_conflict",
        ],
        time_simulation=True,
        session_isolation=True,
        dry_run_mode=True,
    )


def handle_testing_control(req: TestingControlRequest, context: Context) -> TestingControlResponse:
    """Handle testing control requests."""
    session_manager = get_session_manager()
    testing_ctx = get_testing_context(context)

    try:
        if req.action == "create_session":
            session_id = req.session_id or f"test_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
            session = session_manager.get_session(session_id)
            return TestingControlResponse(
                success=True,
                message=f"Test session {session_id} created",
                data={"session_id": session_id, "created_at": session["created_at"].isoformat()},
            )

        elif req.action == "cleanup_session":
            if not req.session_id:
                return TestingControlResponse(success=False, message="session_id required for cleanup")
            session_manager.cleanup_session(req.session_id)
            return TestingControlResponse(success=True, message=f"Session {req.session_id} cleaned up")

        elif req.action == "list_sessions":
            sessions = session_manager.list_sessions()
            return TestingControlResponse(
                success=True, message=f"Found {len(sessions)} active sessions", data={"sessions": sessions}
            )

        elif req.action == "get_capabilities":
            capabilities = get_testing_capabilities()
            return TestingControlResponse(
                success=True, message="Testing capabilities retrieved", data=capabilities.model_dump()
            )

        elif req.action == "inspect_context":
            return TestingControlResponse(
                success=True, message="Current testing context", data=testing_ctx.model_dump()
            )

        else:
            return TestingControlResponse(success=False, message=f"Unknown action: {req.action}")

    except Exception as e:
        return TestingControlResponse(success=False, message=f"Error: {str(e)}")
