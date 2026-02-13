"""
Test suite for mock server AdCP response headers implementation.

Tests the mock server's response header functionality added to comply with
the AdCP testing specification.
"""

from datetime import datetime

from src.core.testing_hooks import (
    AdCPTestContext,
    CampaignEvent,
    NextEventCalculator,
    TestingHooksResult,
    apply_testing_hooks,
    get_session_manager,
)


class TestMockServerResponseHeaders:
    """Test suite for mock server AdCP testing response headers."""

    def test_next_event_calculator_lifecycle_progression(self):
        """Test that NextEventCalculator correctly calculates lifecycle progression."""
        testing_ctx = AdCPTestContext(dry_run=True)

        # Test normal lifecycle progression
        test_cases = [
            (CampaignEvent.CAMPAIGN_CREATION, 0.0, CampaignEvent.CAMPAIGN_PENDING),
            (CampaignEvent.CAMPAIGN_PENDING, 0.0, CampaignEvent.CAMPAIGN_APPROVED),
            (CampaignEvent.CAMPAIGN_APPROVED, 0.0, CampaignEvent.CAMPAIGN_START),
            (CampaignEvent.CAMPAIGN_START, 0.1, CampaignEvent.CAMPAIGN_MIDPOINT),
            (CampaignEvent.CAMPAIGN_MIDPOINT, 0.5, CampaignEvent.CAMPAIGN_75_PERCENT),
            (CampaignEvent.CAMPAIGN_75_PERCENT, 0.75, CampaignEvent.CAMPAIGN_COMPLETE),
            (CampaignEvent.CAMPAIGN_COMPLETE, 1.0, None),  # No next event after completion
        ]

        for current_event, progress, expected_next in test_cases:
            next_event = NextEventCalculator.get_next_event(current_event, progress, testing_ctx)
            assert next_event == expected_next, f"Expected {expected_next} after {current_event}, got {next_event}"

    def test_next_event_calculator_with_jump_to_event(self):
        """Test NextEventCalculator when jumping to specific events."""
        testing_ctx = AdCPTestContext(dry_run=True, jump_to_event=CampaignEvent.CAMPAIGN_MIDPOINT)

        # When jumping to midpoint, next should be 75%
        next_event = NextEventCalculator.get_next_event(None, 0.3, testing_ctx)
        assert next_event == CampaignEvent.CAMPAIGN_75_PERCENT

    def test_next_event_time_calculation(self):
        """Test calculation of next event timing."""
        start_date = datetime(2025, 1, 1)
        end_date = datetime(2025, 1, 31)
        current_time = datetime(2025, 1, 10)

        # Test midpoint timing
        midpoint_time = NextEventCalculator.calculate_next_event_time(
            CampaignEvent.CAMPAIGN_MIDPOINT, start_date, end_date, current_time
        )

        # Midpoint should be around January 16 (middle of campaign)
        expected_midpoint = start_date + (end_date - start_date) * 0.5
        assert abs((midpoint_time - expected_midpoint).total_seconds()) < 3600  # Within 1 hour

    def test_response_headers_with_campaign_info(self):
        """Test that response headers are correctly generated with campaign info."""
        testing_ctx = AdCPTestContext(
            dry_run=True, auto_advance=True, mock_time=datetime(2025, 1, 10), test_session_id="test_response_headers"
        )

        campaign_info = {"start_date": datetime(2025, 1, 1), "end_date": datetime(2025, 1, 31), "total_budget": 15000.0}

        result = apply_testing_hooks(testing_ctx, "test_op", campaign_info, spend_amount=7500.0)

        # Result is now a TestingHooksResult dataclass
        assert isinstance(result, TestingHooksResult)
        assert result.is_test is True

        headers = result.response_headers

        # Should have next event header
        assert "X-Next-Event" in headers
        assert headers["X-Next-Event"] == "campaign-midpoint"

        # Should have next event time header
        assert "X-Next-Event-Time" in headers
        assert headers["X-Next-Event-Time"].endswith("Z")  # ISO format with Z

        # Should have simulated spend header
        assert "X-Simulated-Spend" in headers
        assert headers["X-Simulated-Spend"] == "7500.00"

    def test_simulated_spend_tracking(self):
        """Test simulated spend tracking across sessions."""
        session_manager = get_session_manager()
        session_id = "test_spend_tracking"

        # Clean up any existing session
        session_manager.cleanup_session(session_id)

        testing_ctx = AdCPTestContext(dry_run=True, test_session_id=session_id, simulated_spend=True)

        # First request with spending
        result1 = apply_testing_hooks(testing_ctx, "request_1", spend_amount=2500.0)

        # Second request with more spending
        result2 = apply_testing_hooks(testing_ctx, "request_2", spend_amount=5000.0)

        # Check that spend is tracked
        current_spend = session_manager.get_session_spend(session_id)
        assert current_spend == 5000.0

        # Check response headers include spend
        assert result2.response_headers.get("X-Simulated-Spend") == "5000.00"

        # Cleanup
        session_manager.cleanup_session(session_id)
        assert session_manager.get_session_spend(session_id) == 0.0

    def test_result_without_campaign_info(self):
        """Test result when no campaign info is available."""
        testing_ctx = AdCPTestContext(dry_run=True, test_session_id="test_no_campaign")

        result = apply_testing_hooks(testing_ctx, "get_products")

        # Should be marked as test
        assert result.is_test is True

        # Should not have event-related headers without campaign info
        assert "X-Next-Event" not in result.response_headers
        assert "X-Next-Event-Time" not in result.response_headers

    def test_response_headers_in_debug_mode(self):
        """Test that debug mode includes response header information."""
        testing_ctx = AdCPTestContext(dry_run=True, debug_mode=True, mock_time=datetime(2025, 1, 15), auto_advance=True)

        campaign_info = {"start_date": datetime(2025, 1, 1), "end_date": datetime(2025, 1, 31), "total_budget": 10000.0}

        result = apply_testing_hooks(testing_ctx, "debug_test", campaign_info, spend_amount=5000.0)

        # Debug info should be present
        assert result.debug_info is not None
        assert "response_headers" in result.debug_info
        assert "campaign_info" in result.debug_info
        assert result.debug_info["operation"] == "debug_test"

    def test_error_event_next_event_calculation(self):
        """Test next event calculation for error scenarios."""
        testing_ctx = AdCPTestContext(dry_run=True, jump_to_event=CampaignEvent.BUDGET_EXCEEDED)

        # After budget exceeded (error), next event should depend on progress
        next_event = NextEventCalculator.get_next_event(CampaignEvent.BUDGET_EXCEEDED, 0.9, testing_ctx)

        # At 90% progress after budget error, should go to completion
        assert next_event == CampaignEvent.CAMPAIGN_COMPLETE

    def test_multiple_testing_headers_integration(self):
        """Test integration with multiple testing headers simultaneously."""
        testing_ctx = AdCPTestContext(
            dry_run=True,
            mock_time=datetime(2025, 1, 15),
            jump_to_event=CampaignEvent.CAMPAIGN_MIDPOINT,
            auto_advance=True,
            test_session_id="multi_header_test",
            simulated_spend=True,
            debug_mode=True,
        )

        campaign_info = {"start_date": datetime(2025, 1, 1), "end_date": datetime(2025, 1, 31), "total_budget": 20000.0}

        result = apply_testing_hooks(testing_ctx, "multi_test", campaign_info, spend_amount=10000.0)

        # Should be marked as test
        assert result.is_test is True

        # Should have response headers
        headers = result.response_headers
        assert "X-Next-Event" in headers
        assert "X-Next-Event-Time" in headers
        assert "X-Simulated-Spend" in headers

        # Should have debug info
        assert result.debug_info is not None

        # Verify the headers make sense
        assert headers["X-Next-Event"] == "campaign-75-percent"  # Next after midpoint
        assert float(headers["X-Simulated-Spend"]) == 10000.0

    def test_media_buy_id_override_in_dry_run(self):
        """Test that dry-run adds test_ prefix to media_buy_id."""
        testing_ctx = AdCPTestContext(dry_run=True)

        result = apply_testing_hooks(testing_ctx, "create_media_buy", media_buy_id="mb_123")
        assert result.media_buy_id_override == "test_mb_123"

    def test_no_media_buy_id_override_without_dry_run(self):
        """Test that non-dry-run doesn't add test_ prefix."""
        testing_ctx = AdCPTestContext(test_session_id="test")

        result = apply_testing_hooks(testing_ctx, "create_media_buy", media_buy_id="mb_123")
        assert result.media_buy_id_override is None

    def test_no_double_test_prefix(self):
        """Test that already-prefixed media_buy_id isn't double-prefixed."""
        testing_ctx = AdCPTestContext(dry_run=True)

        result = apply_testing_hooks(testing_ctx, "create_media_buy", media_buy_id="test_mb_123")
        assert result.media_buy_id_override is None
