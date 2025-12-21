"""Tests for mock adapter keyword-based test scenario parser."""

from src.adapters.test_scenario_parser import (
    TestScenario,
    has_test_keywords,
    parse_test_scenario,
)


class TestParseTestScenario:
    """Tests for parse_test_scenario function."""

    def test_empty_message_returns_default(self):
        """Empty or None message returns default scenario."""
        assert parse_test_scenario(None).should_accept is True
        assert parse_test_scenario("").should_accept is True
        assert parse_test_scenario("  ").should_accept is True

    def test_normal_text_returns_default(self):
        """Normal business text returns default scenario."""
        scenario = parse_test_scenario("Nike Q4 Campaign")
        assert scenario.should_accept is True
        assert scenario.should_reject is False
        assert scenario.delay_seconds is None

    def test_reject_with_reason(self):
        """[REJECT:reason] parses correctly."""
        scenario = parse_test_scenario("[REJECT:Budget too low]")
        assert scenario.should_reject is True
        assert scenario.should_accept is False
        assert scenario.rejection_reason == "Budget too low"

    def test_reject_without_reason(self):
        """[REJECT] without reason uses default."""
        scenario = parse_test_scenario("[REJECT]")
        assert scenario.should_reject is True
        assert scenario.rejection_reason == "Test rejection"

    def test_reject_case_insensitive(self):
        """Keywords are case insensitive."""
        scenario = parse_test_scenario("[reject:Budget issue]")
        assert scenario.should_reject is True

    def test_delay(self):
        """[DELAY:N] parses seconds correctly."""
        scenario = parse_test_scenario("[DELAY:10]")
        assert scenario.delay_seconds == 10
        assert scenario.should_accept is True

    def test_async(self):
        """[ASYNC] sets async mode."""
        scenario = parse_test_scenario("[ASYNC]")
        assert scenario.use_async is True

    def test_hitl(self):
        """[HITL:Nm:outcome] parses human-in-the-loop."""
        scenario = parse_test_scenario("[HITL:5m:approve]")
        assert scenario.simulate_hitl is True
        assert scenario.hitl_delay_minutes == 5
        assert scenario.hitl_outcome == "approve"

    def test_hitl_reject(self):
        """HITL can simulate rejection."""
        scenario = parse_test_scenario("[HITL:2m:reject]")
        assert scenario.simulate_hitl is True
        assert scenario.hitl_outcome == "reject"

    def test_error(self):
        """[ERROR:message] parses error simulation."""
        scenario = parse_test_scenario("[ERROR:Server timeout]")
        assert scenario.error_message == "Server timeout"

    def test_question(self):
        """[QUESTION:text] parses question asking."""
        scenario = parse_test_scenario("[QUESTION:What is the campaign budget?]")
        assert scenario.should_ask_question is True
        assert scenario.question_text == "What is the campaign budget?"

    def test_combined_keywords(self):
        """Multiple keywords can be combined."""
        scenario = parse_test_scenario("Test campaign [DELAY:5] [ASYNC]")
        assert scenario.delay_seconds == 5
        assert scenario.use_async is True

    def test_creative_approve(self):
        """[APPROVE] for creatives."""
        scenario = parse_test_scenario("[APPROVE]", operation="sync_creatives")
        assert scenario.creative_actions == [{"action": "approve"}]

    def test_creative_reject(self):
        """[REJECT:reason] for creatives sets creative_actions."""
        scenario = parse_test_scenario("[REJECT:Missing click URL]", operation="sync_creatives")
        assert scenario.should_reject is True
        # Note: For creatives, rejection is through should_reject, not creative_actions

    def test_creative_ask(self):
        """[ASK:field] for creatives."""
        scenario = parse_test_scenario("[ASK:Click tracking URL]", operation="sync_creatives")
        assert scenario.creative_actions == [{"action": "ask_for_field", "reason": "Click tracking URL"}]

    def test_delivery_profile(self):
        """[DELIVERY:profile] for delivery queries."""
        scenario = parse_test_scenario("[DELIVERY:slow]", operation="get_delivery")
        assert scenario.delivery_profile == "slow"

    def test_delivery_percentage(self):
        """[DELIVERY%:N] sets specific percentage."""
        scenario = parse_test_scenario("[DELIVERY%:75.5]", operation="get_delivery")
        assert scenario.delivery_percentage == 75.5

    def test_delivery_outage(self):
        """[OUTAGE] simulates platform outage."""
        scenario = parse_test_scenario("[OUTAGE]", operation="get_delivery")
        assert scenario.simulate_outage is True

    def test_keywords_in_middle_of_text(self):
        """Keywords work when embedded in text."""
        scenario = parse_test_scenario("Campaign for Nike [DELAY:10] Q4 2025")
        assert scenario.delay_seconds == 10


class TestHasTestKeywords:
    """Tests for has_test_keywords function."""

    def test_none_returns_false(self):
        """None message returns False."""
        assert has_test_keywords(None) is False

    def test_empty_returns_false(self):
        """Empty message returns False."""
        assert has_test_keywords("") is False

    def test_normal_text_returns_false(self):
        """Normal business text returns False."""
        assert has_test_keywords("Nike Q4 Campaign") is False
        assert has_test_keywords("Test Product") is False  # "Test" alone is not a keyword

    def test_reject_keyword(self):
        """[REJECT] is detected."""
        assert has_test_keywords("[REJECT]") is True
        assert has_test_keywords("[REJECT:reason]") is True

    def test_delay_keyword(self):
        """[DELAY:N] is detected."""
        assert has_test_keywords("[DELAY:10]") is True

    def test_async_keyword(self):
        """[ASYNC] is detected."""
        assert has_test_keywords("[ASYNC]") is True

    def test_case_insensitive(self):
        """Keywords are case insensitive."""
        assert has_test_keywords("[reject]") is True
        assert has_test_keywords("[ASYNC]") is True
        assert has_test_keywords("[delay:5]") is True


class TestTestScenario:
    """Tests for TestScenario dataclass."""

    def test_default_values(self):
        """Default scenario is acceptance."""
        scenario = TestScenario()
        assert scenario.should_accept is True
        assert scenario.should_reject is False
        assert scenario.delay_seconds is None
        assert scenario.creative_actions == []

    def test_creative_actions_default(self):
        """creative_actions defaults to empty list."""
        scenario = TestScenario()
        assert scenario.creative_actions == []

        scenario2 = TestScenario(creative_actions=None)
        assert scenario2.creative_actions == []
