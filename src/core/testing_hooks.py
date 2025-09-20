"""
AdCP Testing Hooks Implementation

Implements the full AdCP testing specification for comprehensive test backend support:
https://adcontextprotocol.org/docs/media-buy/testing/

This module handles all testing headers and provides isolated test execution:
- X-Dry-Run: Execute without affecting production
- X-Mock-Time: Control simulated time progression
- X-Jump-To-Event: Jump to specific campaign lifecycle events
- X-Test-Session-ID: Isolate test sessions
- X-Auto-Advance: Automatically advance through events
- X-Simulated-Spend: Track simulated spending without real money
"""

from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from fastmcp.server.context import Context
from fastmcp.server.dependencies import get_http_headers
from pydantic import BaseModel


class CampaignEvent(str, Enum):
    """Campaign lifecycle and error events that can be jumped to."""

    # Lifecycle Events
    CAMPAIGN_CREATION = "campaign-creation"
    CAMPAIGN_PENDING = "campaign-pending"
    CAMPAIGN_APPROVED = "campaign-approved"
    CAMPAIGN_START = "campaign-start"
    CAMPAIGN_MIDPOINT = "campaign-midpoint"
    CAMPAIGN_75_PERCENT = "campaign-75-percent"
    CAMPAIGN_COMPLETE = "campaign-complete"
    CAMPAIGN_PAUSED = "campaign-paused"
    CAMPAIGN_CANCELLED = "campaign-cancelled"

    # Error Events
    CREATIVE_POLICY_VIOLATION = "creative-policy-violation"
    BUDGET_EXCEEDED = "budget-exceeded"
    INVENTORY_UNAVAILABLE = "inventory-unavailable"
    MANUAL_APPROVAL_DELAY = "manual-approval-delay"
    DELIVERY_UNDERPERFORMING = "delivery-underperforming"
    PLATFORM_ERROR = "platform-error"
    TARGETING_CONFLICT = "targeting-conflict"
    CREATIVE_REJECTION = "creative-rejection"


class TestingContext(BaseModel):
    """Context for test execution with all testing hooks."""

    # Session isolation
    test_session_id: str | None = None

    # Dry run mode
    dry_run: bool = False

    # Time simulation
    mock_time: datetime | None = None
    auto_advance: bool = False

    # Event jumping
    jump_to_event: CampaignEvent | None = None

    # Spend tracking
    simulated_spend: bool = False
    simulated_spend_amount: float = 0.0

    # Advanced testing features
    force_error: str | None = None
    slow_mode: bool = False
    debug_mode: bool = False

    @classmethod
    def from_context(cls, context: Context) -> "TestingContext":
        """Extract testing context from FastMCP context headers."""
        if not context:
            return cls()

        # Use modern FastMCP pattern for getting headers
        headers = get_http_headers()

        # Extract all testing headers
        test_session_id = headers.get("X-Test-Session-ID")
        dry_run = headers.get("X-Dry-Run", "").lower() == "true"
        auto_advance = headers.get("X-Auto-Advance", "").lower() == "true"
        simulated_spend = headers.get("X-Simulated-Spend", "").lower() == "true"
        force_error = headers.get("X-Force-Error")
        slow_mode = headers.get("X-Slow-Mode", "").lower() == "true"
        debug_mode = headers.get("X-Debug-Mode", "").lower() == "true"

        # Parse mock time
        mock_time = None
        mock_time_header = headers.get("X-Mock-Time")
        if mock_time_header:
            try:
                # Handle both ISO format and timestamp
                if mock_time_header.isdigit():
                    mock_time = datetime.fromtimestamp(int(mock_time_header))
                else:
                    # Remove 'Z' suffix if present and parse
                    time_str = mock_time_header.rstrip("Z")
                    mock_time = datetime.fromisoformat(time_str)
            except (ValueError, OverflowError):
                # Invalid time format, ignore
                pass

        # Parse jump to event
        jump_to_event = None
        jump_event_header = headers.get("X-Jump-To-Event")
        if jump_event_header:
            try:
                jump_to_event = CampaignEvent(jump_event_header)
            except ValueError:
                # Invalid event, ignore
                pass

        return cls(
            test_session_id=test_session_id,
            dry_run=dry_run,
            mock_time=mock_time,
            auto_advance=auto_advance,
            jump_to_event=jump_to_event,
            simulated_spend=simulated_spend,
            force_error=force_error,
            slow_mode=slow_mode,
            debug_mode=debug_mode,
        )


class NextEventCalculator:
    """Calculates next events and timing for AdCP response headers."""

    @staticmethod
    def get_next_event(
        current_event: CampaignEvent | None, progress: float, testing_ctx: TestingContext
    ) -> CampaignEvent | None:
        """Calculate the next expected event in the campaign lifecycle."""

        # If we're jumping to a specific event, the next event depends on what we jumped to
        if testing_ctx.jump_to_event:
            current_event = testing_ctx.jump_to_event

        # Define the normal campaign progression
        lifecycle_progression = [
            CampaignEvent.CAMPAIGN_CREATION,
            CampaignEvent.CAMPAIGN_PENDING,
            CampaignEvent.CAMPAIGN_APPROVED,
            CampaignEvent.CAMPAIGN_START,
            CampaignEvent.CAMPAIGN_MIDPOINT,
            CampaignEvent.CAMPAIGN_75_PERCENT,
            CampaignEvent.CAMPAIGN_COMPLETE,
        ]

        # If no current event specified, determine based on progress
        if not current_event:
            if progress == 0.0:
                current_event = CampaignEvent.CAMPAIGN_CREATION
            elif progress < 0.1:
                current_event = CampaignEvent.CAMPAIGN_START
            elif progress < 0.5:
                current_event = CampaignEvent.CAMPAIGN_START
            elif progress < 0.75:
                current_event = CampaignEvent.CAMPAIGN_MIDPOINT
            elif progress < 1.0:
                current_event = CampaignEvent.CAMPAIGN_75_PERCENT
            else:
                return None  # Campaign complete

        # Find current event in progression and return next
        try:
            current_index = lifecycle_progression.index(current_event)
            if current_index + 1 < len(lifecycle_progression):
                return lifecycle_progression[current_index + 1]
        except ValueError:
            # Current event not in normal progression (error event)
            # Return appropriate next event based on progress
            if progress < 0.5:
                return CampaignEvent.CAMPAIGN_MIDPOINT
            elif progress < 0.75:
                return CampaignEvent.CAMPAIGN_75_PERCENT
            elif progress < 1.0:
                return CampaignEvent.CAMPAIGN_COMPLETE

        return None

    @staticmethod
    def calculate_next_event_time(
        next_event: CampaignEvent, start_date: datetime, end_date: datetime, current_time: datetime | None = None
    ) -> datetime:
        """Calculate when the next event should occur."""
        if current_time is None:
            current_time = datetime.now()

        # Use existing event timing logic
        next_event_time = TimeSimulator.jump_to_event_time(next_event, start_date, end_date)

        # Ensure next event is in the future relative to current time
        if next_event_time <= current_time:
            # Add a small buffer to make it future
            time_buffer = timedelta(minutes=30)
            next_event_time = current_time + time_buffer

        return next_event_time


class SimulatedSpendTracker:
    """Tracks simulated spending across test sessions."""

    def __init__(self):
        self._session_spend: dict[str, float] = {}

    def update_spend(self, session_id: str, amount: float):
        """Update simulated spend for a session."""
        if session_id:
            self._session_spend[session_id] = amount

    def get_spend(self, session_id: str) -> float:
        """Get current simulated spend for a session."""
        return self._session_spend.get(session_id, 0.0)

    def clear_session(self, session_id: str):
        """Clear spend tracking for a session."""
        if session_id in self._session_spend:
            del self._session_spend[session_id]


class TestSessionManager:
    """Manages isolated test sessions to prevent cross-contamination."""

    def __init__(self):
        self._sessions: dict[str, dict[str, Any]] = {}
        self._spend_tracker = SimulatedSpendTracker()

    def get_session(self, session_id: str) -> dict[str, Any]:
        """Get or create a test session."""
        if session_id not in self._sessions:
            self._sessions[session_id] = {
                "created_at": datetime.now(),
                "media_buys": {},
                "creatives": {},
                "spend": 0.0,
                "events": [],
                "state": "active",
            }
        return self._sessions[session_id]

    def cleanup_session(self, session_id: str):
        """Clean up a test session."""
        if session_id in self._sessions:
            del self._sessions[session_id]
        self._spend_tracker.clear_session(session_id)

    def update_session_spend(self, session_id: str, amount: float):
        """Update simulated spend for a session."""
        self._spend_tracker.update_spend(session_id, amount)

    def get_session_spend(self, session_id: str) -> float:
        """Get current simulated spend for a session."""
        return self._spend_tracker.get_spend(session_id)

    def list_sessions(self) -> dict[str, Any]:
        """List all active test sessions."""
        return {
            session_id: {
                "created_at": session_data["created_at"],
                "state": session_data["state"],
                "media_buys_count": len(session_data["media_buys"]),
                "total_spend": session_data["spend"],
            }
            for session_id, session_data in self._sessions.items()
        }


class TimeSimulator:
    """Handles time simulation for testing campaigns."""

    @staticmethod
    def calculate_campaign_progress(
        start_date: datetime, end_date: datetime, current_time: datetime | None = None
    ) -> float:
        """Calculate campaign progress as percentage (0.0 to 1.0)."""
        if current_time is None:
            current_time = datetime.now()

        if current_time <= start_date:
            return 0.0
        if current_time >= end_date:
            return 1.0

        total_duration = (end_date - start_date).total_seconds()
        elapsed_duration = (current_time - start_date).total_seconds()

        return min(1.0, max(0.0, elapsed_duration / total_duration))

    @staticmethod
    def jump_to_event_time(event: CampaignEvent, start_date: datetime, end_date: datetime) -> datetime:
        """Calculate the datetime for a specific campaign event."""
        duration = end_date - start_date

        event_times = {
            CampaignEvent.CAMPAIGN_CREATION: start_date - timedelta(days=1),
            CampaignEvent.CAMPAIGN_PENDING: start_date - timedelta(hours=1),
            CampaignEvent.CAMPAIGN_APPROVED: start_date - timedelta(minutes=5),
            CampaignEvent.CAMPAIGN_START: start_date,
            CampaignEvent.CAMPAIGN_MIDPOINT: start_date + duration * 0.5,
            CampaignEvent.CAMPAIGN_75_PERCENT: start_date + duration * 0.75,
            CampaignEvent.CAMPAIGN_COMPLETE: end_date,
            CampaignEvent.CAMPAIGN_PAUSED: start_date + duration * 0.3,
            CampaignEvent.CAMPAIGN_CANCELLED: start_date + duration * 0.2,
            # Error events typically occur during campaign
            CampaignEvent.CREATIVE_POLICY_VIOLATION: start_date + timedelta(hours=2),
            CampaignEvent.BUDGET_EXCEEDED: start_date + duration * 0.8,
            CampaignEvent.INVENTORY_UNAVAILABLE: start_date + timedelta(hours=1),
            CampaignEvent.MANUAL_APPROVAL_DELAY: start_date - timedelta(hours=12),
            CampaignEvent.DELIVERY_UNDERPERFORMING: start_date + duration * 0.4,
            CampaignEvent.PLATFORM_ERROR: start_date + duration * 0.1,
            CampaignEvent.TARGETING_CONFLICT: start_date + timedelta(minutes=30),
            CampaignEvent.CREATIVE_REJECTION: start_date + timedelta(hours=6),
        }

        return event_times.get(event, start_date)


class DeliverySimulator:
    """Simulates realistic campaign delivery based on testing context."""

    @staticmethod
    def calculate_simulated_metrics(budget: float, progress: float, testing_ctx: TestingContext) -> dict[str, Any]:
        """Calculate realistic delivery metrics for a campaign."""

        # Base metrics
        impressions_per_dollar = 1000  # Base CPM of $1
        clicks_per_impression = 0.001  # 0.1% CTR
        views_per_impression = 0.7  # 70% viewability

        # Apply event-specific modifications
        if testing_ctx.jump_to_event:
            progress = DeliverySimulator._adjust_progress_for_event(progress, testing_ctx.jump_to_event)

        # Calculate spend based on progress
        spend = budget * progress
        if testing_ctx.simulated_spend:
            spend = min(spend, testing_ctx.simulated_spend_amount or spend)

        # Calculate derived metrics
        impressions = int(spend * impressions_per_dollar)
        clicks = int(impressions * clicks_per_impression)
        video_views = int(impressions * views_per_impression)

        # Error scenarios
        if testing_ctx.force_error:
            return DeliverySimulator._generate_error_scenario(testing_ctx.force_error, spend, impressions)

        # Event-specific adjustments
        status = "active"
        if testing_ctx.jump_to_event:
            status = DeliverySimulator._get_status_for_event(testing_ctx.jump_to_event)

        return {
            "spend": round(spend, 2),
            "impressions": impressions,
            "clicks": clicks,
            "video_views": video_views,
            "ctr": round(clicks / impressions if impressions > 0 else 0, 4),
            "cpm": round(spend / impressions * 1000 if impressions > 0 else 0, 2),
            "viewability_rate": round(views_per_impression, 2),
            "status": status,
            "progress": round(progress, 3),
            "is_simulated": True,
            "test_session_id": testing_ctx.test_session_id,
        }

    @staticmethod
    def _adjust_progress_for_event(progress: float, event: CampaignEvent) -> float:
        """Adjust progress based on jumped-to event."""
        event_progress = {
            CampaignEvent.CAMPAIGN_CREATION: 0.0,
            CampaignEvent.CAMPAIGN_PENDING: 0.0,
            CampaignEvent.CAMPAIGN_APPROVED: 0.0,
            CampaignEvent.CAMPAIGN_START: 0.0,
            CampaignEvent.CAMPAIGN_MIDPOINT: 0.5,
            CampaignEvent.CAMPAIGN_75_PERCENT: 0.75,
            CampaignEvent.CAMPAIGN_COMPLETE: 1.0,
            CampaignEvent.CAMPAIGN_PAUSED: 0.3,
            CampaignEvent.CAMPAIGN_CANCELLED: 0.2,
            CampaignEvent.BUDGET_EXCEEDED: 0.8,
            CampaignEvent.DELIVERY_UNDERPERFORMING: 0.4,
        }
        return event_progress.get(event, progress)

    @staticmethod
    def _get_status_for_event(event: CampaignEvent) -> str:
        """Get campaign status for a specific event."""
        status_map = {
            CampaignEvent.CAMPAIGN_CREATION: "pending",
            CampaignEvent.CAMPAIGN_PENDING: "pending",
            CampaignEvent.CAMPAIGN_APPROVED: "approved",
            CampaignEvent.CAMPAIGN_START: "active",
            CampaignEvent.CAMPAIGN_MIDPOINT: "active",
            CampaignEvent.CAMPAIGN_75_PERCENT: "active",
            CampaignEvent.CAMPAIGN_COMPLETE: "completed",
            CampaignEvent.CAMPAIGN_PAUSED: "paused",
            CampaignEvent.CAMPAIGN_CANCELLED: "cancelled",
            CampaignEvent.BUDGET_EXCEEDED: "paused",
            CampaignEvent.DELIVERY_UNDERPERFORMING: "active",
        }
        return status_map.get(event, "active")

    @staticmethod
    def _generate_error_scenario(error_type: str, spend: float, impressions: int) -> dict[str, Any]:
        """Generate metrics for error scenarios."""
        error_scenarios = {
            "budget_exceeded": {
                "spend": spend * 1.1,  # Over budget
                "impressions": impressions,
                "status": "paused",
                "error": "Budget limit exceeded",
            },
            "low_delivery": {
                "spend": spend * 0.3,  # Under-delivery
                "impressions": int(impressions * 0.3),
                "status": "active",
                "warning": "Delivery below expectations",
            },
            "platform_error": {
                "spend": 0,
                "impressions": 0,
                "status": "error",
                "error": "Platform connectivity issues",
            },
        }

        scenario = error_scenarios.get(error_type, {})
        return {
            "spend": scenario.get("spend", spend),
            "impressions": scenario.get("impressions", impressions),
            "clicks": 0,
            "video_views": 0,
            "ctr": 0,
            "cpm": 0,
            "viewability_rate": 0,
            "status": scenario.get("status", "error"),
            "error": scenario.get("error"),
            "warning": scenario.get("warning"),
            "is_simulated": True,
        }


# Global instances
_session_manager = TestSessionManager()


def get_testing_context(context: Context) -> TestingContext:
    """Get testing context from FastMCP context."""
    return TestingContext.from_context(context)


def get_session_manager() -> TestSessionManager:
    """Get the global test session manager."""
    return _session_manager


def get_next_event_calculator() -> NextEventCalculator:
    """Get the next event calculator."""
    return NextEventCalculator


def is_production_isolated(testing_ctx: TestingContext) -> bool:
    """Verify that testing context is properly isolated from production."""
    return testing_ctx.dry_run or testing_ctx.test_session_id is not None or testing_ctx.simulated_spend


def apply_testing_hooks(
    data: dict[str, Any],
    testing_ctx: TestingContext,
    operation: str = "unknown",
    campaign_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply testing hooks to modify operation data/results."""

    # Always mark data as simulated if any testing hooks are active
    if any(
        [
            testing_ctx.dry_run,
            testing_ctx.test_session_id,
            testing_ctx.mock_time,
            testing_ctx.jump_to_event,
            testing_ctx.simulated_spend,
        ]
    ):
        data["is_test"] = True
        data["test_session_id"] = testing_ctx.test_session_id

    # Add dry-run indicators
    if testing_ctx.dry_run:
        data["dry_run"] = True
        if "media_buy_id" in data and not data["media_buy_id"].startswith("test_"):
            data["media_buy_id"] = f"test_{data['media_buy_id']}"

    # Add AdCP testing response headers
    response_headers = {}

    # Calculate progress and next event information
    if campaign_info and any([testing_ctx.auto_advance, testing_ctx.mock_time, testing_ctx.jump_to_event]):
        start_date = campaign_info.get("start_date")
        end_date = campaign_info.get("end_date")
        current_time = testing_ctx.mock_time or datetime.now()

        if start_date and end_date:
            # Calculate current progress
            progress = TimeSimulator.calculate_campaign_progress(start_date, end_date, current_time)

            # Get next event
            current_event = testing_ctx.jump_to_event
            next_event = NextEventCalculator.get_next_event(current_event, progress, testing_ctx)

            if next_event:
                # Add X-Next-Event header
                response_headers["X-Next-Event"] = next_event.value

                # Add X-Next-Event-Time header
                next_event_time = NextEventCalculator.calculate_next_event_time(
                    next_event, start_date, end_date, current_time
                )
                response_headers["X-Next-Event-Time"] = next_event_time.isoformat() + "Z"

    # Add X-Simulated-Spend header
    if testing_ctx.simulated_spend or testing_ctx.test_session_id or testing_ctx.dry_run:
        # Use spend from current response data
        current_spend = data.get("total_spend", 0) or data.get("spend", 0)

        # If no spend in current data, check session tracker
        if current_spend == 0 and testing_ctx.test_session_id:
            session_manager = get_session_manager()
            current_spend = session_manager.get_session_spend(testing_ctx.test_session_id)

        if current_spend > 0:
            response_headers["X-Simulated-Spend"] = f"{current_spend:.2f}"

    # Update session spend tracking
    if testing_ctx.test_session_id and "total_spend" in data:
        session_manager = get_session_manager()
        session_manager.update_session_spend(testing_ctx.test_session_id, data["total_spend"])
    elif testing_ctx.test_session_id and "spend" in data:
        session_manager = get_session_manager()
        session_manager.update_session_spend(testing_ctx.test_session_id, data["spend"])

    # Add response headers to data
    if response_headers:
        data["response_headers"] = response_headers

    # Add debug information if requested
    if testing_ctx.debug_mode:
        data["debug_info"] = {
            "operation": operation,
            "testing_context": testing_ctx.model_dump(),
            "timestamp": datetime.now().isoformat(),
            "response_headers": response_headers,
            "campaign_info": campaign_info,
        }

    return data
