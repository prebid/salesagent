"""Unit tests for AI test orchestrator.

Tests the AI's ability to interpret natural language test instructions.
"""

from unittest.mock import patch

import pytest

from src.adapters.ai_test_orchestrator import AITestOrchestrator


@pytest.fixture
def mock_genai():
    """Mock the Gemini AI to return controlled responses."""
    with patch("src.adapters.ai_test_orchestrator.genai") as mock:
        yield mock


class TestAIOrchestrator:
    """Test AI orchestrator initialization and basic operation."""

    def test_init_with_api_key(self):
        """Test initialization with provided API key."""
        orchestrator = AITestOrchestrator(api_key="test_key")
        assert orchestrator.api_key == "test_key"

    def test_init_from_env(self, monkeypatch):
        """Test initialization from environment variable."""
        monkeypatch.setenv("GEMINI_API_KEY", "env_key")
        orchestrator = AITestOrchestrator()
        assert orchestrator.api_key == "env_key"

    def test_init_no_key_raises(self, monkeypatch):
        """Test that missing API key raises error."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="GEMINI_API_KEY not found"):
            AITestOrchestrator()

    def test_empty_message_returns_default(self):
        """Test that empty message returns default scenario."""
        orchestrator = AITestOrchestrator(api_key="test")
        scenario = orchestrator.interpret_message("", "create_media_buy")
        assert scenario.should_accept is True
        assert scenario.should_reject is False

    def test_none_message_returns_default(self):
        """Test that None message returns default scenario."""
        orchestrator = AITestOrchestrator(api_key="test")
        scenario = orchestrator.interpret_message(None, "create_media_buy")
        assert scenario.should_accept is True


class TestJSONExtraction:
    """Test JSON extraction from AI responses."""

    def test_extract_plain_json(self):
        """Test extracting plain JSON."""
        orchestrator = AITestOrchestrator(api_key="test")
        response = '{"delay_seconds": 10}'
        result = orchestrator._extract_json(response)
        assert result == {"delay_seconds": 10}

    def test_extract_json_with_markdown(self):
        """Test extracting JSON wrapped in markdown."""
        orchestrator = AITestOrchestrator(api_key="test")
        response = '```json\n{"delay_seconds": 10}\n```'
        result = orchestrator._extract_json(response)
        assert result == {"delay_seconds": 10}

    def test_extract_json_with_plain_markdown(self):
        """Test extracting JSON with plain markdown markers."""
        orchestrator = AITestOrchestrator(api_key="test")
        response = '```\n{"delay_seconds": 10}\n```'
        result = orchestrator._extract_json(response)
        assert result == {"delay_seconds": 10}


class TestScenarioParsing:
    """Test parsing JSON into TestScenario objects."""

    def test_parse_delay_scenario(self):
        """Test parsing delay scenario."""
        orchestrator = AITestOrchestrator(api_key="test")
        scenario_json = {"delay_seconds": 10}
        scenario = orchestrator._parse_scenario(scenario_json)

        assert scenario.delay_seconds == 10
        assert scenario.should_accept is True

    def test_parse_rejection_scenario(self):
        """Test parsing rejection scenario."""
        orchestrator = AITestOrchestrator(api_key="test")
        scenario_json = {"should_reject": True, "rejection_reason": "Test rejection"}
        scenario = orchestrator._parse_scenario(scenario_json)

        assert scenario.should_reject is True
        assert scenario.rejection_reason == "Test rejection"

    def test_parse_hitl_scenario(self):
        """Test parsing human-in-the-loop scenario."""
        orchestrator = AITestOrchestrator(api_key="test")
        scenario_json = {
            "simulate_hitl": True,
            "hitl_delay_minutes": 5,
            "hitl_outcome": "approve",
        }
        scenario = orchestrator._parse_scenario(scenario_json)

        assert scenario.simulate_hitl is True
        assert scenario.hitl_delay_minutes == 5
        assert scenario.hitl_outcome == "approve"

    def test_parse_creative_actions(self):
        """Test parsing creative-specific actions."""
        orchestrator = AITestOrchestrator(api_key="test")
        scenario_json = {
            "creative_actions": [
                {"creative_index": 0, "action": "approve"},
                {"creative_index": 1, "action": "reject", "reason": "Missing URL"},
            ]
        }
        scenario = orchestrator._parse_scenario(scenario_json)

        assert len(scenario.creative_actions) == 2
        assert scenario.creative_actions[0]["action"] == "approve"
        assert scenario.creative_actions[1]["reason"] == "Missing URL"

    def test_parse_delivery_profile(self):
        """Test parsing delivery profile scenario."""
        orchestrator = AITestOrchestrator(api_key="test")
        scenario_json = {
            "delivery_profile": "slow",
            "delivery_percentage": 30.5,
        }
        scenario = orchestrator._parse_scenario(scenario_json)

        assert scenario.delivery_profile == "slow"
        assert scenario.delivery_percentage == 30.5

    def test_parse_question_scenario(self):
        """Test parsing question-asking scenario."""
        orchestrator = AITestOrchestrator(api_key="test")
        scenario_json = {
            "should_ask_question": True,
            "question_text": "What is your target audience?",
        }
        scenario = orchestrator._parse_scenario(scenario_json)

        assert scenario.should_ask_question is True
        assert scenario.question_text == "What is your target audience?"


class TestPromptBuilding:
    """Test prompt construction for different operations."""

    def test_prompt_includes_message(self):
        """Test that prompt includes the buyer's message."""
        orchestrator = AITestOrchestrator(api_key="test")
        prompt = orchestrator._build_prompt("Wait 10 seconds", "create_media_buy")

        assert "Wait 10 seconds" in prompt
        assert "create_media_buy" in prompt

    def test_prompt_includes_operation_type(self):
        """Test that prompt specifies operation type."""
        orchestrator = AITestOrchestrator(api_key="test")
        prompt = orchestrator._build_prompt("test", "sync_creatives")

        assert "sync_creatives" in prompt

    def test_prompt_includes_examples(self):
        """Test that prompt includes example scenarios."""
        orchestrator = AITestOrchestrator(api_key="test")
        prompt = orchestrator._build_prompt("test", "create_media_buy")

        assert "delay_seconds" in prompt
        assert "Example" in prompt


class TestAIIntegration:
    """Integration tests with mocked Gemini API."""

    def test_interpret_simple_delay(self, mock_gemini_test_scenarios):
        """Test interpreting simple delay instruction."""
        orchestrator = AITestOrchestrator(api_key="test_key")
        scenario = orchestrator.interpret_message("Wait 10 seconds before responding", "create_media_buy")

        assert scenario.delay_seconds == 10

    def test_interpret_rejection(self, mock_gemini_test_scenarios):
        """Test interpreting rejection instruction."""
        orchestrator = AITestOrchestrator(api_key="test_key")
        scenario = orchestrator.interpret_message(
            "Reject this media buy with reason 'Budget too high'", "create_media_buy"
        )

        assert scenario.should_reject is True
        assert "budget" in scenario.rejection_reason.lower() or "high" in scenario.rejection_reason.lower()

    def test_interpret_hitl(self, mock_gemini_test_scenarios):
        """Test interpreting human-in-the-loop instruction."""
        orchestrator = AITestOrchestrator(api_key="test_key")
        scenario = orchestrator.interpret_message(
            "Simulate human in the loop approval after 2 minutes", "create_media_buy"
        )

        assert scenario.simulate_hitl is True
        assert scenario.hitl_delay_minutes == 2

    def test_interpret_creative_reject(self, mock_gemini_test_scenarios):
        """Test interpreting creative rejection."""
        orchestrator = AITestOrchestrator(api_key="test_key")
        scenario = orchestrator.interpret_message("reject this for missing click URL", "sync_creatives")

        assert len(scenario.creative_actions) >= 1
        assert scenario.creative_actions[0]["action"] == "reject"
        assert "url" in scenario.creative_actions[0].get("reason", "").lower()

    def test_interpret_creative_approve(self, mock_gemini_test_scenarios):
        """Test interpreting creative approval."""
        orchestrator = AITestOrchestrator(api_key="test_key")
        scenario = orchestrator.interpret_message("approve this creative", "sync_creatives")

        # AI should approve (or return empty actions which defaults to approve)
        if scenario.creative_actions:
            assert scenario.creative_actions[0]["action"] == "approve"

    def test_interpret_creative_ask_for_field(self, mock_gemini_test_scenarios):
        """Test interpreting creative field request."""
        orchestrator = AITestOrchestrator(api_key="test_key")
        scenario = orchestrator.interpret_message("ask for click tracker", "sync_creatives")

        assert len(scenario.creative_actions) >= 1
        assert scenario.creative_actions[0]["action"] in ["ask_for_field", "request_changes"]
