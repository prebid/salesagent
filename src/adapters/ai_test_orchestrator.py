"""AI-powered test orchestrator for mock adapter.

Uses Gemini to interpret natural language test instructions and control
the mock adapter's behavior for comprehensive testing scenarios.
"""

import json
import os
from dataclasses import dataclass

import google.generativeai as genai


@dataclass
class TestScenario:
    """Parsed test scenario from AI interpretation."""

    # Timing control
    delay_seconds: int | None = None
    use_async: bool = False

    # Response control
    should_accept: bool = True
    should_reject: bool = False
    rejection_reason: str | None = None
    should_ask_question: bool = False
    question_text: str | None = None

    # Human-in-the-loop
    simulate_hitl: bool = False
    hitl_delay_minutes: int | None = None
    hitl_outcome: str | None = None  # "approve", "reject"

    # Creative-specific
    creative_actions: list[dict] = None  # [{creative_index: 0, action: "approve"}, ...]

    # Delivery-specific
    delivery_profile: str | None = None
    simulate_outage: bool = False
    delivery_percentage: float | None = None

    # General
    error_message: str | None = None

    def __post_init__(self):
        if self.creative_actions is None:
            self.creative_actions = []


class AITestOrchestrator:
    """AI-powered test orchestrator using Gemini."""

    def __init__(self, api_key: str | None = None):
        """Initialize the orchestrator.

        Args:
            api_key: Gemini API key (defaults to GEMINI_API_KEY env var)
        """
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY not found in environment")

        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel("gemini-2.5-flash-lite")

    def interpret_message(self, message: str, operation: str) -> TestScenario:
        """Interpret natural language test instructions.

        Args:
            message: Natural language test instructions from buyer
            operation: Operation type ("create_media_buy", "sync_creatives", "get_delivery", etc.)

        Returns:
            TestScenario with parsed instructions
        """
        if not message or not message.strip():
            # No test instructions - return default scenario
            return TestScenario()

        prompt = self._build_prompt(message, operation)

        try:
            response = self.model.generate_content(prompt)
            scenario_json = self._extract_json(response.text)
            return self._parse_scenario(scenario_json)
        except Exception as e:
            # If AI fails, log and return default scenario
            print(f"Warning: AI orchestrator failed to parse message: {e}")
            return TestScenario()

    def _build_prompt(self, message: str, operation: str) -> str:
        """Build the prompt for Gemini."""
        return f"""You are a test orchestrator for an advertising protocol mock server.

A buyer has sent test instructions for the operation: {operation}

Their message: "{message}"

IMPORTANT: Only interpret this message as test instructions if it contains explicit test directives.
If the message is just a normal business description (like "Test Product", "Summer Campaign 2024", "Q1 Brand Awareness"),
return a default success scenario: {{"should_accept": true}}

Your job is to interpret their test instructions and return a JSON object describing what the mock server should do.

Available test behaviors:

**Timing Control:**
- delay_seconds: Integer (delay before responding)
- use_async: Boolean (return pending status, require polling)

**Response Control:**
- should_accept: Boolean (default true)
- should_reject: Boolean (reject the operation)
- rejection_reason: String (why it was rejected)
- should_ask_question: Boolean (respond with a question)
- question_text: String (the question to ask)

**Human-in-the-Loop Simulation:**
- simulate_hitl: Boolean (create workflow step requiring human approval)
- hitl_delay_minutes: Integer (how long to wait before auto-resolving)
- hitl_outcome: "approve" or "reject" (what the simulated human decides)

**Creative Actions (for sync_creatives only):**
- creative_actions: Array with ONE object like [{{"action": "approve"}}] or [{{"action": "reject", "reason": "Missing URL"}}]
  - Actions: "approve", "reject", "request_changes", "ask_for_field"
  - Include "reason" field for rejections/requests
  - Note: Each creative is processed individually, so return action for THIS creative only

**Delivery Simulation (for get_delivery only):**
- delivery_profile: "slow", "fast", "uneven", "normal"
- simulate_outage: Boolean (raise platform error)
- delivery_percentage: Float 0-100 (override with specific percentage)

**Error Simulation:**
- error_message: String (raise exception with this message)
  - ONLY use this if the message explicitly asks for an error to be raised
  - DO NOT use this for normal business descriptions

Return ONLY valid JSON matching this structure. No markdown, no explanations.

Example for "Wait 10 seconds then reject":
{{"delay_seconds": 10, "should_reject": true, "rejection_reason": "Test rejection"}}

Example for creative named "reject this for missing URL":
{{"creative_actions": [{{"action": "reject", "reason": "Missing click URL"}}]}}

Example for creative named "ask for click tracker":
{{"creative_actions": [{{"action": "ask_for_field", "reason": "Need click tracking URL"}}]}}

Example for normal campaign name "Test Product":
{{"should_accept": true}}

Now interpret the buyer's message and return JSON:"""

    def _extract_json(self, response_text: str) -> dict:
        """Extract JSON from AI response (may have markdown formatting)."""
        # Remove markdown code blocks if present
        text = response_text.strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]

        text = text.strip()
        return json.loads(text)

    def _parse_scenario(self, scenario_json: dict) -> TestScenario:
        """Parse JSON into TestScenario object."""
        return TestScenario(
            delay_seconds=scenario_json.get("delay_seconds"),
            use_async=scenario_json.get("use_async", False),
            should_accept=scenario_json.get("should_accept", True),
            should_reject=scenario_json.get("should_reject", False),
            rejection_reason=scenario_json.get("rejection_reason"),
            should_ask_question=scenario_json.get("should_ask_question", False),
            question_text=scenario_json.get("question_text"),
            simulate_hitl=scenario_json.get("simulate_hitl", False),
            hitl_delay_minutes=scenario_json.get("hitl_delay_minutes"),
            hitl_outcome=scenario_json.get("hitl_outcome"),
            creative_actions=scenario_json.get("creative_actions", []),
            delivery_profile=scenario_json.get("delivery_profile"),
            simulate_outage=scenario_json.get("simulate_outage", False),
            delivery_percentage=scenario_json.get("delivery_percentage"),
            error_message=scenario_json.get("error_message"),
        )
