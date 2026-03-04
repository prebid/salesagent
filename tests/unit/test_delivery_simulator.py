"""Unit tests for delivery simulator service."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.services.delivery_simulator import DeliverySimulator


class TestDeliverySimulator:
    """Test cases for DeliverySimulator."""

    @pytest.fixture
    def simulator(self):
        """Create a fresh simulator instance."""
        return DeliverySimulator()

    @pytest.fixture
    def mock_webhook_service(self):
        """Mock webhook delivery service."""
        with patch("src.services.delivery_simulator.webhook_delivery_service") as mock:
            mock.send_delivery_webhook = MagicMock(return_value=True)
            mock.reset_sequence = MagicMock()
            yield mock

    def test_simulator_initialization(self, simulator):
        """Test simulator initializes correctly."""
        assert simulator._active_simulations == {}
        assert simulator._stop_signals == {}

    def test_start_simulation_creates_thread(self, simulator, mock_webhook_service):
        """Test that starting simulation creates a background thread."""
        media_buy_id = "buy_test_123"
        start_time = datetime.now(UTC)
        end_time = start_time + timedelta(hours=1)

        simulator.start_simulation(
            media_buy_id=media_buy_id,
            tenant_id="tenant_1",
            principal_id="principal_1",
            start_time=start_time,
            end_time=end_time,
            total_budget=1000.0,
            time_acceleration=3600,
            update_interval_seconds=0.1,  # Fast for testing
        )

        # Check thread was created
        assert media_buy_id in simulator._active_simulations
        assert media_buy_id in simulator._stop_signals
        assert simulator._active_simulations[media_buy_id].is_alive()

        # Cleanup - stop and wait for thread to finish
        simulator.stop_simulation(media_buy_id)
        simulator._active_simulations.get(media_buy_id, MagicMock()).join(timeout=5)

    def test_stop_simulation(self, simulator, mock_webhook_service):
        """Test that stopping simulation sets stop signal."""
        media_buy_id = "buy_test_456"
        start_time = datetime.now(UTC)
        end_time = start_time + timedelta(hours=1)

        simulator.start_simulation(
            media_buy_id=media_buy_id,
            tenant_id="tenant_1",
            principal_id="principal_1",
            start_time=start_time,
            end_time=end_time,
            total_budget=1000.0,
            time_acceleration=3600,
            update_interval_seconds=0.1,
        )

        # Capture thread reference before stopping
        thread = simulator._active_simulations[media_buy_id]

        # Stop simulation
        simulator.stop_simulation(media_buy_id)

        # Check stop signal was set
        assert simulator._stop_signals[media_buy_id].is_set()

        # Wait for thread to finish cleanup
        thread.join(timeout=5)

        # Thread should have cleaned up
        assert media_buy_id not in simulator._active_simulations

    def test_duplicate_simulation_prevented(self, simulator, mock_webhook_service):
        """Test that duplicate simulations for same media buy are prevented."""
        media_buy_id = "buy_test_789"
        start_time = datetime.now(UTC)
        end_time = start_time + timedelta(hours=1)

        # Start first simulation
        simulator.start_simulation(
            media_buy_id=media_buy_id,
            tenant_id="tenant_1",
            principal_id="principal_1",
            start_time=start_time,
            end_time=end_time,
            total_budget=1000.0,
            time_acceleration=3600,
            update_interval_seconds=0.1,
        )

        # Try to start duplicate
        simulator.start_simulation(
            media_buy_id=media_buy_id,
            tenant_id="tenant_1",
            principal_id="principal_1",
            start_time=start_time,
            end_time=end_time,
            total_budget=1000.0,
            time_acceleration=3600,
            update_interval_seconds=0.1,
        )

        # Should only have one thread
        assert media_buy_id in simulator._active_simulations
        active_count = sum(1 for t in simulator._active_simulations.values() if t.is_alive())
        assert active_count == 1

        # Cleanup
        simulator.stop_simulation(media_buy_id)
        simulator._active_simulations.get(media_buy_id, MagicMock()).join(timeout=5)

    def test_webhook_payload_structure(self, simulator, mock_webhook_service):
        """Test that webhook delivery service is called correctly."""
        media_buy_id = "buy_test_webhook"
        start_time = datetime.now(UTC)
        end_time = start_time + timedelta(hours=2)

        simulator.start_simulation(
            media_buy_id=media_buy_id,
            tenant_id="tenant_1",
            principal_id="principal_1",
            start_time=start_time,
            end_time=end_time,
            total_budget=1000.0,
            time_acceleration=7200,  # 1 sec = 2 hours (complete in 1 second)
            update_interval_seconds=0.5,  # Should fire 2-3 webhooks
        )

        # Wait for simulation to complete (thread finishes when progress >= 1.0)
        thread = simulator._active_simulations.get(media_buy_id)
        if thread:
            thread.join(timeout=5)

        # Check webhooks were sent via webhook_delivery_service
        assert mock_webhook_service.send_delivery_webhook.called

        # Get first webhook call
        first_call = mock_webhook_service.send_delivery_webhook.call_args_list[0]
        kwargs = first_call[1]

        # Verify parameters passed to webhook service
        assert kwargs["tenant_id"] == "tenant_1"
        assert kwargs["principal_id"] == "principal_1"
        assert kwargs["media_buy_id"] == media_buy_id
        assert "reporting_period_start" in kwargs
        assert "reporting_period_end" in kwargs
        assert "impressions" in kwargs
        assert "spend" in kwargs
        assert "is_final" in kwargs

        # Verify reset_sequence was called after completion
        assert mock_webhook_service.reset_sequence.called

    def test_time_acceleration_calculation(self, simulator, mock_webhook_service):
        """Test that time acceleration works correctly."""
        media_buy_id = "buy_test_acceleration"
        start_time = datetime.now(UTC)
        end_time = start_time + timedelta(hours=24)  # 24-hour campaign

        # With acceleration of 86400 (1 sec = 1 day), should complete in 1 second
        simulator.start_simulation(
            media_buy_id=media_buy_id,
            tenant_id="tenant_1",
            principal_id="principal_1",
            start_time=start_time,
            end_time=end_time,
            total_budget=1000.0,
            time_acceleration=86400,  # 1 sec = 1 day
            update_interval_seconds=0.5,
        )

        # Wait for simulation to complete
        thread = simulator._active_simulations.get(media_buy_id)
        if thread:
            thread.join(timeout=5)

        # Should have completed
        assert media_buy_id not in simulator._active_simulations

        # Check final webhook had is_final=True
        last_call = mock_webhook_service.send_delivery_webhook.call_args_list[-1]
        kwargs = last_call[1]
        assert kwargs["is_final"] is True

    def test_delivery_metrics_progression(self, simulator, mock_webhook_service):
        """Test that delivery metrics progress realistically."""
        media_buy_id = "buy_test_metrics"
        start_time = datetime.now(UTC)
        end_time = start_time + timedelta(hours=10)
        total_budget = 5000.0

        simulator.start_simulation(
            media_buy_id=media_buy_id,
            tenant_id="tenant_1",
            principal_id="principal_1",
            start_time=start_time,
            end_time=end_time,
            total_budget=total_budget,
            time_acceleration=36000,  # 1 sec = 10 hours (complete in 1 second)
            update_interval_seconds=0.25,  # 4 updates
        )

        # Wait for simulation to complete
        thread = simulator._active_simulations.get(media_buy_id)
        if thread:
            thread.join(timeout=5)

        # Analyze webhook progression
        calls = mock_webhook_service.send_delivery_webhook.call_args_list

        # Should have multiple calls
        assert len(calls) >= 2

        # Check first and last
        first_kwargs = calls[0][1]
        last_kwargs = calls[-1][1]

        # First should have 0 spend/impressions
        assert first_kwargs["spend"] == 0.0
        assert first_kwargs["impressions"] == 0
        assert first_kwargs["is_final"] is False

        # Last should have full spend/impressions
        assert last_kwargs["spend"] > 0
        assert last_kwargs["impressions"] > 0
        assert last_kwargs["is_final"] is True

        # Spend should not exceed budget significantly
        assert last_kwargs["spend"] <= total_budget * 1.1  # Allow 10% variance

    def test_cleanup_after_completion(self, simulator, mock_webhook_service):
        """Test that simulator cleans up after completion."""
        media_buy_id = "buy_test_cleanup"
        start_time = datetime.now(UTC)
        end_time = start_time + timedelta(hours=1)

        simulator.start_simulation(
            media_buy_id=media_buy_id,
            tenant_id="tenant_1",
            principal_id="principal_1",
            start_time=start_time,
            end_time=end_time,
            total_budget=1000.0,
            time_acceleration=3600,  # 1 sec = 1 hour
            update_interval_seconds=0.5,
        )

        # Wait for completion
        thread = simulator._active_simulations.get(media_buy_id)
        if thread:
            thread.join(timeout=5)

        # Should have cleaned up
        assert media_buy_id not in simulator._active_simulations
        assert media_buy_id not in simulator._stop_signals
