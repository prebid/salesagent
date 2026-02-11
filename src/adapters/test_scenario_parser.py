"""Simple keyword-based test scenario parser for mock adapter.

Replaces the AI-powered test orchestrator with deterministic keyword parsing.
No API key required, faster, and more predictable for testing.

Keyword Format Examples:
    [REJECT:reason here] - Reject with given reason
    [DELAY:10] - Wait 10 seconds before responding
    [ASYNC] - Return async/pending response
    [HITL:5m:approve] - Simulate human-in-the-loop (5 min, then approve)
    [ERROR:message] - Raise an error with message
    [QUESTION:text] - Ask a question before proceeding

For creatives:
    [APPROVE] - Approve this creative
    [REJECT:reason] - Reject this creative with reason
    [ASK:field needed] - Request additional information
"""

import re
from dataclasses import dataclass


@dataclass
class TestScenario:
    """Parsed test scenario from keyword parsing."""

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
    creative_actions: list[dict] | None = None

    # Delivery-specific
    delivery_profile: str | None = None
    simulate_outage: bool = False
    delivery_percentage: float | None = None

    # General
    error_message: str | None = None

    def __post_init__(self):
        if self.creative_actions is None:
            self.creative_actions = []


def parse_test_scenario(message: str | None, operation: str = "create_media_buy") -> TestScenario:
    """Parse test keywords from message string.

    Args:
        message: Message to parse (e.g., promoted_offering, creative name)
        operation: Operation type for context-specific parsing

    Returns:
        TestScenario with parsed instructions
    """
    if not message or not message.strip():
        return TestScenario()

    scenario = TestScenario()
    text = message.strip()

    # Parse [REJECT:reason] or [REJECT]
    reject_match = re.search(r"\[REJECT(?::([^\]]+))?\]", text, re.IGNORECASE)
    if reject_match:
        scenario.should_reject = True
        scenario.should_accept = False
        scenario.rejection_reason = reject_match.group(1) or "Test rejection"
        return scenario

    # Parse [DELAY:N] where N is seconds
    delay_match = re.search(r"\[DELAY:(\d+)\]", text, re.IGNORECASE)
    if delay_match:
        scenario.delay_seconds = int(delay_match.group(1))

    # Parse [ASYNC]
    if re.search(r"\[ASYNC\]", text, re.IGNORECASE):
        scenario.use_async = True

    # Parse [HITL:Nm:outcome] e.g., [HITL:5m:approve]
    hitl_match = re.search(r"\[HITL:(\d+)m:(\w+)\]", text, re.IGNORECASE)
    if hitl_match:
        scenario.simulate_hitl = True
        scenario.hitl_delay_minutes = int(hitl_match.group(1))
        scenario.hitl_outcome = hitl_match.group(2).lower()

    # Parse [ERROR:message]
    error_match = re.search(r"\[ERROR:([^\]]+)\]", text, re.IGNORECASE)
    if error_match:
        scenario.error_message = error_match.group(1)

    # Parse [QUESTION:text]
    question_match = re.search(r"\[QUESTION:([^\]]+)\]", text, re.IGNORECASE)
    if question_match:
        scenario.should_ask_question = True
        scenario.question_text = question_match.group(1)

    # Creative-specific keywords
    if operation == "sync_creatives":
        # Parse [APPROVE]
        if re.search(r"\[APPROVE\]", text, re.IGNORECASE):
            scenario.creative_actions = [{"action": "approve"}]

        # Parse [ASK:field needed]
        ask_match = re.search(r"\[ASK:([^\]]+)\]", text, re.IGNORECASE)
        if ask_match:
            scenario.creative_actions = [{"action": "ask_for_field", "reason": ask_match.group(1)}]

    # Delivery-specific keywords
    if operation == "get_delivery":
        # Parse [DELIVERY:profile] e.g., [DELIVERY:slow], [DELIVERY:fast]
        delivery_match = re.search(r"\[DELIVERY:(\w+)\]", text, re.IGNORECASE)
        if delivery_match:
            scenario.delivery_profile = delivery_match.group(1).lower()

        # Parse [DELIVERY%:N] for specific percentage
        pct_match = re.search(r"\[DELIVERY%:(\d+(?:\.\d+)?)\]", text, re.IGNORECASE)
        if pct_match:
            scenario.delivery_percentage = float(pct_match.group(1))

        # Parse [OUTAGE]
        if re.search(r"\[OUTAGE\]", text, re.IGNORECASE):
            scenario.simulate_outage = True

    return scenario


def has_test_keywords(message: str | None) -> bool:
    """Check if message contains any test keywords.

    Args:
        message: Message to check

    Returns:
        True if test keywords are present
    """
    if not message:
        return False

    # List of all recognized keywords
    keywords = [
        r"\[REJECT",
        r"\[DELAY:",
        r"\[ASYNC\]",
        r"\[HITL:",
        r"\[ERROR:",
        r"\[QUESTION:",
        r"\[APPROVE\]",
        r"\[ASK:",
        r"\[DELIVERY",
        r"\[OUTAGE\]",
    ]

    return any(re.search(kw, message, re.IGNORECASE) for kw in keywords)
