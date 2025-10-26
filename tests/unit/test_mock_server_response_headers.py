"""
Test suite for mock server AdCP response headers implementation.

Tests the mock server's response header functionality added to comply with
the AdCP testing specification.
"""

from datetime import datetime

from src.core.schemas import PricingOption
from src.core.testing_hooks import (
    AdCPTestContext,
    CampaignEvent,
    NextEventCalculator,
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

        response_data = {"total_spend": 7500.0, "active_count": 1}

        result = apply_testing_hooks(response_data, testing_ctx, "test_op", campaign_info)

        # Check that response headers were added
        assert "response_headers" in result
        headers = result["response_headers"]

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
        response_data1 = {"total_spend": 2500.0}
        result1 = apply_testing_hooks(response_data1, testing_ctx, "request_1")

        # Second request with more spending
        response_data2 = {"total_spend": 5000.0}
        result2 = apply_testing_hooks(response_data2, testing_ctx, "request_2")

        # Check that spend is tracked
        current_spend = session_manager.get_session_spend(session_id)
        assert current_spend == 5000.0

        # Check response headers include spend
        if "response_headers" in result2:
            headers = result2["response_headers"]
            assert headers.get("X-Simulated-Spend") == "5000.00"

        # Cleanup
        session_manager.cleanup_session(session_id)
        assert session_manager.get_session_spend(session_id) == 0.0

    def test_response_headers_without_campaign_info(self):
        """Test response headers when no campaign info is available."""
        from src.core.schemas import Product

        testing_ctx = AdCPTestContext(dry_run=True, test_session_id="test_no_campaign")

        # Use real Product object instead of mock dictionary
        test_product = Product(
            product_id="test",
            name="Test Product",
            description="Real product object for testing",
            formats=["display_300x250"],  # Internal field name
            delivery_type="non_guaranteed",
            is_custom=False,
            property_tags=["all_inventory"],  # Required per AdCP spec
            pricing_options=[
                PricingOption(
                    pricing_option_id="cpm_usd_auction",
                    pricing_model="cpm",
                    currency="USD",
                    is_fixed=False,
                    price_guidance={"floor": 1.0, "suggested_rate": 5.0},
                )
            ],
        )

        response_data = {"products": [test_product.model_dump_internal()]}

        result = apply_testing_hooks(response_data, testing_ctx, "get_products")

        # Should still have test markers
        assert result["is_test"] is True
        assert result["test_session_id"] == "test_no_campaign"

        # Response headers might be empty or minimal without campaign info
        headers = result.get("response_headers", {})
        # Should not have event-related headers without campaign info
        assert "X-Next-Event" not in headers
        assert "X-Next-Event-Time" not in headers

        # CRITICAL: Test roundtrip conversion - this would catch the "formats field required" bug
        modified_products = [Product(**p) for p in result["products"]]

        # Verify the roundtrip worked correctly
        assert len(modified_products) == 1
        reconstructed_product = modified_products[0]
        assert reconstructed_product.product_id == "test"
        assert reconstructed_product.name == "Test Product"
        assert reconstructed_product.formats == ["display_300x250"]  # Internal field preserved
        assert reconstructed_product.format_ids == ["display_300x250"]  # AdCP property works

    def test_response_headers_in_debug_mode(self):
        """Test that debug mode includes response header information."""
        testing_ctx = AdCPTestContext(dry_run=True, debug_mode=True, mock_time=datetime(2025, 1, 15), auto_advance=True)

        campaign_info = {"start_date": datetime(2025, 1, 1), "end_date": datetime(2025, 1, 31), "total_budget": 10000.0}

        response_data = {"total_spend": 5000.0}

        result = apply_testing_hooks(response_data, testing_ctx, "debug_test", campaign_info)

        # Debug info should be present
        assert "debug_info" in result
        debug_info = result["debug_info"]

        # Should include response headers in debug info
        assert "response_headers" in debug_info
        assert "campaign_info" in debug_info
        assert debug_info["operation"] == "debug_test"

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

        response_data = {"total_spend": 10000.0, "total_impressions": 1000000, "active_count": 1}

        result = apply_testing_hooks(response_data, testing_ctx, "multi_test", campaign_info)

        # Should have all test markers
        assert result["is_test"] is True
        assert result["dry_run"] is True
        assert result["test_session_id"] == "multi_header_test"

        # Should have response headers
        headers = result.get("response_headers", {})
        assert "X-Next-Event" in headers
        assert "X-Next-Event-Time" in headers
        assert "X-Simulated-Spend" in headers

        # Should have debug info
        assert "debug_info" in result

        # Verify the headers make sense
        assert headers["X-Next-Event"] == "campaign-75-percent"  # Next after midpoint
        assert float(headers["X-Simulated-Spend"]) == 10000.0
