#!/usr/bin/env python3
"""
Strategy management and time simulation system for AdCP.

Provides unified strategy system that works for both:
1. Production strategies (pacing, bidding, optimization)
2. Simulation strategies (testing, time progression)
"""

from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from sqlalchemy import delete, select

from src.core.database.database_session import get_db_session
from src.core.database.models import Strategy as StrategyModel
from src.core.database.models import StrategyState


class StrategyError(Exception):
    """Base exception for strategy-related errors."""

    pass


class SimulationError(StrategyError):
    """Errors related to simulation control."""

    pass


class JumpEvent(str, Enum):
    """Predefined events for simulation time jumping."""

    # Campaign lifecycle
    CAMPAIGN_CREATED = "campaign-created"
    CAMPAIGN_PENDING_APPROVAL = "campaign-pending-approval"
    CAMPAIGN_APPROVED = "campaign-approved"
    CAMPAIGN_START = "campaign-start"
    CAMPAIGN_25_PERCENT = "campaign-25-percent"
    CAMPAIGN_50_PERCENT = "campaign-50-percent"
    CAMPAIGN_75_PERCENT = "campaign-75-percent"
    CAMPAIGN_END = "campaign-end"
    CAMPAIGN_COMPLETED = "campaign-completed"

    # Creative lifecycle
    CREATIVE_SUBMITTED = "creative-submitted"
    CREATIVE_SCANNING = "creative-scanning"
    CREATIVE_PENDING_REVIEW = "creative-pending-review"
    CREATIVE_APPROVED = "creative-approved"
    CREATIVE_REJECTED_POLICY = "creative-rejected-policy"
    CREATIVE_REJECTED_QUALITY = "creative-rejected-quality"
    CREATIVE_PAUSED = "creative-paused"
    CREATIVE_EXPIRED = "creative-expired"

    # Error scenarios
    ERROR_BUDGET_EXCEEDED = "error-budget-exceeded"
    ERROR_POLICY_VIOLATION = "error-policy-violation"
    ERROR_TARGETING_INVALID = "error-targeting-invalid"
    ERROR_INVENTORY_UNAVAILABLE = "error-inventory-unavailable"
    ERROR_COMPETITIVE_CONFLICT = "error-competitive-conflict"
    ERROR_FREQUENCY_CAP_HIT = "error-frequency-cap-hit"
    ERROR_PACING_ALERT = "error-pacing-alert"
    ERROR_CREATIVE_404 = "error-creative-404"
    ERROR_BILLING_FAILED = "error-billing-failed"

    # Performance milestones
    MILESTONE_FIRST_IMPRESSION = "milestone-first-impression"
    MILESTONE_FIRST_CLICK = "milestone-first-click"
    MILESTONE_FIRST_CONVERSION = "milestone-first-conversion"
    MILESTONE_OPTIMIZATION_TRIGGERED = "milestone-optimization-triggered"
    MILESTONE_BUDGET_50_PERCENT = "milestone-budget-50-percent"
    MILESTONE_BUDGET_90_PERCENT = "milestone-budget-90-percent"


class StrategyManager:
    """Manages strategies and simulation control."""

    def __init__(self, tenant_id: str | None = None, principal_id: str | None = None):
        self.tenant_id = tenant_id
        self.principal_id = principal_id
        self._simulation_contexts: dict[str, SimulationContext] = {}

    def get_or_create_strategy(self, strategy_id: str, create_if_missing: bool = True) -> "StrategyContext":
        """Get existing strategy or create new one."""
        with get_db_session() as session:
            stmt = select(StrategyModel).filter_by(strategy_id=strategy_id)
            strategy = session.scalars(stmt).first()

            if not strategy and create_if_missing:
                strategy = self._create_default_strategy(strategy_id)
                session.add(strategy)
                session.commit()
                session.refresh(strategy)

            if not strategy:
                raise StrategyError(f"Strategy not found: {strategy_id}")

            return StrategyContext(strategy)

    def _create_default_strategy(self, strategy_id: str) -> StrategyModel:
        """Create a default strategy based on strategy_id."""
        is_simulation = strategy_id.startswith("sim_")

        if is_simulation:
            return self._create_simulation_strategy(strategy_id)
        else:
            return self._create_production_strategy(strategy_id)

    def _create_production_strategy(self, strategy_id: str) -> StrategyModel:
        """Create a production strategy."""
        strategy_configs = {
            "conservative_pacing": {
                "name": "Conservative Pacing",
                "description": "Slow, steady delivery to ensure full flight completion",
                "config": {
                    "pacing_rate": 0.8,
                    "bid_adjustment": 0.9,
                    "optimization_threshold": 0.15,
                    "error_handling": "pause_and_alert",
                },
            },
            "aggressive_scaling": {
                "name": "Aggressive Scaling",
                "description": "Fast delivery to maximize reach quickly",
                "config": {
                    "pacing_rate": 1.3,
                    "bid_adjustment": 1.2,
                    "optimization_threshold": 0.25,
                    "error_handling": "auto_recover",
                },
            },
            "premium_guaranteed": {
                "name": "Premium Guaranteed Strategy",
                "description": "Ensure premium placement delivery",
                "config": {
                    "require_viewability": 0.7,
                    "placement_priority": "premium_only",
                    "pacing_rate": 1.0,
                    "error_handling": "escalate_immediately",
                },
            },
        }

        config = strategy_configs.get(
            strategy_id,
            {
                "name": strategy_id.replace("_", " ").title(),
                "description": f"Custom strategy: {strategy_id}",
                "config": {},
            },
        )

        return StrategyModel(
            strategy_id=strategy_id,
            tenant_id=self.tenant_id,
            principal_id=self.principal_id,
            name=config["name"],
            description=config["description"],
            config=config["config"],
            is_simulation=False,
        )

    def _create_simulation_strategy(self, strategy_id: str) -> StrategyModel:
        """Create a simulation strategy."""
        # Extract scenario from strategy_id if present
        parts = strategy_id.split("_")
        scenario = parts[-1] if len(parts) > 2 else "default"

        simulation_configs = {
            "sim_happy_path": {
                "name": "Happy Path Simulation",
                "description": "Everything works perfectly",
                "config": {
                    "mode": "simulation",
                    "scenario": "everything_works",
                    "force_success": True,
                    "time_progression": "accelerated",
                },
            },
            "sim_creative_rejection": {
                "name": "Creative Rejection Simulation",
                "description": "Simulate creative policy violations",
                "config": {
                    "mode": "simulation",
                    "force_creative_rejection": True,
                    "rejection_reason": "policy_violation",
                    "rejection_stage": "review",
                },
            },
            "sim_budget_exceeded": {
                "name": "Budget Exceeded Simulation",
                "description": "Simulate budget overspend scenarios",
                "config": {"mode": "simulation", "force_budget_exceeded": True, "overspend_percentage": 0.15},
            },
        }

        # Handle custom simulation strategies
        if strategy_id not in simulation_configs:
            config = {
                "name": f"Custom Simulation ({scenario})",
                "description": f"Custom simulation strategy: {strategy_id}",
                "config": {"mode": "simulation", "scenario": scenario, "time_progression": "controlled"},
            }
        else:
            config = simulation_configs[strategy_id]

        return StrategyModel(
            strategy_id=strategy_id,
            tenant_id=self.tenant_id,
            principal_id=self.principal_id,
            name=config["name"],
            description=config["description"],
            config=config["config"],
            is_simulation=True,
        )

    def control_simulation(self, strategy_id: str, action: str, parameters: dict[str, Any]) -> dict[str, Any]:
        """Control simulation time progression and events."""
        if not strategy_id.startswith("sim_"):
            raise SimulationError("Only simulation strategies can be controlled")

        strategy = self.get_or_create_strategy(strategy_id)

        if not strategy.is_simulation:
            raise SimulationError(f"Strategy {strategy_id} is not a simulation strategy")

        sim_context = self._get_simulation_context(strategy_id, strategy)

        if action == "jump_to":
            # Support both event and target_date parameters for flexibility
            target = parameters.get("event") or parameters.get("target_date")
            if not target:
                raise SimulationError("jump_to requires either 'event' or 'target_date' parameter")
            return sim_context.jump_to_event(target)
        elif action == "reset":
            return sim_context.reset()
        elif action == "set_scenario":
            return sim_context.set_scenario(parameters.get("scenario"))
        else:
            raise SimulationError(f"Unknown simulation action: {action}")

    def _get_simulation_context(self, strategy_id: str, strategy: "StrategyContext") -> "SimulationContext":
        """Get or create simulation context."""
        if strategy_id not in self._simulation_contexts:
            self._simulation_contexts[strategy_id] = SimulationContext(strategy)
        return self._simulation_contexts[strategy_id]


class StrategyContext:
    """Context for working with a strategy."""

    def __init__(self, strategy_model: StrategyModel):
        self.strategy = strategy_model
        self.strategy_id = strategy_model.strategy_id
        self.is_simulation = strategy_model.is_simulation

    def get_config_value(self, key: str, default: Any = None) -> Any:
        """Get a configuration value."""
        return self.strategy.config.get(key, default) if self.strategy.config else default

    def should_force_error(self, error_type: str) -> bool:
        """Check if this strategy should force a specific error."""
        if not self.is_simulation:
            return False
        return self.get_config_value(f"force_{error_type}", False)

    def get_pacing_multiplier(self) -> float:
        """Get pacing rate multiplier."""
        return self.get_config_value("pacing_rate", 1.0)

    def get_bid_adjustment(self) -> float:
        """Get bid adjustment multiplier."""
        return self.get_config_value("bid_adjustment", 1.0)


class SimulationContext:
    """Manages simulation state and time progression."""

    def __init__(self, strategy: StrategyContext):
        self.strategy = strategy
        self.strategy_id = strategy.strategy_id
        self.current_time = datetime.now(UTC)
        self.events_triggered: list[dict[str, Any]] = []
        self.media_buy_states: dict[str, dict[str, Any]] = {}
        self._load_state()

    def _load_state(self):
        """Load persistent simulation state."""
        with get_db_session() as session:
            stmt = select(StrategyState).filter_by(strategy_id=self.strategy_id)
            states = session.scalars(stmt).all()

            for state in states:
                if state.state_key == "current_time":
                    self.current_time = datetime.fromisoformat(state.state_value["time"])
                elif state.state_key == "events_triggered":
                    self.events_triggered = state.state_value["events"]
                elif state.state_key == "media_buy_states":
                    self.media_buy_states = state.state_value["states"]

    def _save_state(self):
        """Save simulation state to database."""
        with get_db_session() as session:
            # Save current time
            self._upsert_state(session, "current_time", {"time": self.current_time.isoformat()})

            # Save triggered events
            self._upsert_state(session, "events_triggered", {"events": self.events_triggered})

            # Save media buy states
            self._upsert_state(session, "media_buy_states", {"states": self.media_buy_states})

            session.commit()

    def _upsert_state(self, session, key: str, value: dict[str, Any]):
        """Insert or update strategy state."""
        stmt = select(StrategyState).filter_by(strategy_id=self.strategy_id, state_key=key)
        existing = session.scalars(stmt).first()

        if existing:
            existing.state_value = value
            existing.updated_at = datetime.now(UTC)
        else:
            state = StrategyState(strategy_id=self.strategy_id, state_key=key, state_value=value)
            session.add(state)

    def jump_to_event(self, event: str) -> dict[str, Any]:
        """Jump simulation to a specific event or time."""
        if event.startswith("+"):
            # Relative time jump: "+1d", "+6h", etc.
            return self._advance_time(event[1:])
        elif event in [e.value for e in JumpEvent]:
            # Jump to predefined event
            return self._trigger_event(event)
        else:
            # Try to parse as an absolute date (e.g., "2025-09-15")
            try:
                from datetime import datetime

                target_date = datetime.strptime(event, "%Y-%m-%d")
                old_time = self.current_time
                self.current_time = target_date
                self.events_triggered.append(
                    {
                        "event": "time_jumped",
                        "old_time": old_time.isoformat(),
                        "new_time": self.current_time.isoformat(),
                        "target": event,
                        "triggered_at": datetime.now(UTC).isoformat(),
                    }
                )
                self._save_state()
                return {
                    "status": "ok",
                    "message": f"Jumped to {event}",
                    "current_time": self.current_time.isoformat(),
                }
            except ValueError:
                raise SimulationError(f"Unknown jump event: {event}")

    def _advance_time(self, duration_str: str) -> dict[str, Any]:
        """Advance simulation time by duration."""
        # Parse duration: "1d", "6h", "30m", "45s"
        duration = self._parse_duration(duration_str)
        old_time = self.current_time
        self.current_time += duration

        self.events_triggered.append(
            {
                "event": "time_advanced",
                "old_time": old_time.isoformat(),
                "new_time": self.current_time.isoformat(),
                "duration": duration_str,
                "triggered_at": datetime.now(UTC).isoformat(),
            }
        )

        self._save_state()

        return {
            "status": "ok",
            "message": f"Advanced time by {duration_str}",
            "current_state": self.get_current_state(),
            "simulation_time": self.current_time.isoformat(),
        }

    def _trigger_event(self, event: str) -> dict[str, Any]:
        """Trigger a specific simulation event."""
        self.events_triggered.append(
            {"event": event, "triggered_at": self.current_time.isoformat(), "real_time": datetime.now(UTC).isoformat()}
        )

        # Apply event-specific state changes
        self._apply_event_effects(event)
        self._save_state()

        return {
            "status": "ok",
            "message": f"Triggered event: {event}",
            "current_state": self.get_current_state(),
            "simulation_time": self.current_time.isoformat(),
        }

    def _apply_event_effects(self, event: str):
        """Apply side effects of triggering an event."""
        # This will be expanded based on specific event requirements
        if event == JumpEvent.CAMPAIGN_START:
            # Activate all media buys in this simulation
            for media_buy_id in self.media_buy_states:
                self.media_buy_states[media_buy_id]["status"] = "active"
        elif event == JumpEvent.ERROR_BUDGET_EXCEEDED:
            # Pause campaigns due to budget exceeded
            for media_buy_id in self.media_buy_states:
                self.media_buy_states[media_buy_id]["status"] = "paused"
                self.media_buy_states[media_buy_id]["pause_reason"] = "budget_exceeded"

    def reset(self) -> dict[str, Any]:
        """Reset simulation to initial state."""
        self.current_time = datetime.now(UTC)
        self.events_triggered = []
        self.media_buy_states = {}

        # Clear persistent state
        with get_db_session() as session:
            stmt = delete(StrategyState).where(StrategyState.strategy_id == self.strategy_id)
            session.execute(stmt)
            session.commit()

        return {
            "status": "ok",
            "message": "Simulation reset to initial state",
            "current_state": self.get_current_state(),
            "simulation_time": self.current_time.isoformat(),
        }

    def set_scenario(self, scenario: str) -> dict[str, Any]:
        """Change simulation scenario."""
        # Update strategy config with new scenario
        with get_db_session() as session:
            stmt = select(StrategyModel).filter_by(strategy_id=self.strategy_id)
            strategy = session.scalars(stmt).first()
            if strategy:
                strategy.config["scenario"] = scenario
                session.commit()

        return {
            "status": "ok",
            "message": f"Scenario changed to: {scenario}",
            "current_state": self.get_current_state(),
            "simulation_time": self.current_time.isoformat(),
        }

    def get_current_state(self) -> dict[str, Any]:
        """Get current simulation state."""
        return {
            "strategy_id": self.strategy_id,
            "current_time": self.current_time.isoformat(),
            "events_triggered": len(self.events_triggered),
            "latest_events": self.events_triggered[-5:] if self.events_triggered else [],
            "media_buys": len(self.media_buy_states),
            "active_media_buys": len([mb for mb in self.media_buy_states.values() if mb.get("status") == "active"]),
        }

    def _parse_duration(self, duration_str: str) -> timedelta:
        """Parse duration string into timedelta."""
        if duration_str.endswith("d"):
            return timedelta(days=int(duration_str[:-1]))
        elif duration_str.endswith("h"):
            return timedelta(hours=int(duration_str[:-1]))
        elif duration_str.endswith("m"):
            return timedelta(minutes=int(duration_str[:-1]))
        elif duration_str.endswith("s"):
            return timedelta(seconds=int(duration_str[:-1]))
        else:
            raise SimulationError(f"Invalid duration format: {duration_str}")

    def register_media_buy(self, media_buy_id: str, initial_state: dict[str, Any]):
        """Register a media buy in this simulation."""
        self.media_buy_states[media_buy_id] = {**initial_state, "created_at": self.current_time.isoformat()}
        self._save_state()

    def update_media_buy_state(self, media_buy_id: str, updates: dict[str, Any]):
        """Update media buy state in simulation."""
        if media_buy_id in self.media_buy_states:
            self.media_buy_states[media_buy_id].update(updates)
            self._save_state()
