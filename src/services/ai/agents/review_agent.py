"""Pydantic AI agent for creative review decisions."""

import logging
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent

logger = logging.getLogger(__name__)


class CreativeReviewResult(BaseModel):
    """Structured output from AI creative review."""

    decision: Literal["APPROVE", "REQUIRE HUMAN APPROVAL", "REJECT"]
    reason: str = Field(..., description="Brief explanation of the decision")
    confidence: Literal["high", "medium", "low"] = Field(
        default="medium", description="Confidence level in the decision"
    )


REVIEW_SYSTEM_PROMPT = """You are reviewing a creative asset for approval.

Based on the review criteria provided, determine the appropriate action for this creative.
You MUST respond with one of three decisions:
- APPROVE: Creative clearly meets all criteria
- REQUIRE HUMAN APPROVAL: Unsure or needs human judgment
- REJECT: Creative clearly violates criteria

Be thorough in your analysis but concise in your reasoning."""


def create_review_agent(model: str) -> Agent[None, CreativeReviewResult]:
    """Create a creative review agent with the specified model.

    Args:
        model: Pydantic AI model string (e.g., "google-gla:gemini-2.0-flash")

    Returns:
        Configured Agent instance
    """
    return Agent(
        model=model,
        output_type=CreativeReviewResult,
        system_prompt=REVIEW_SYSTEM_PROMPT,
    )


def build_review_prompt(
    review_criteria: str,
    creative_name: str,
    creative_format: str,
    promoted_offering: str,
    creative_data: dict | None = None,
) -> str:
    """Build the user prompt for creative review.

    Args:
        review_criteria: Tenant's creative review criteria
        creative_name: Name of the creative
        creative_format: Format type of the creative
        promoted_offering: Product/offering being promoted
        creative_data: Additional creative data/metadata

    Returns:
        Formatted prompt string
    """
    import json

    data_str = json.dumps(creative_data, indent=2) if creative_data else "{}"

    return f"""Review this creative asset against the provided criteria.

Review Criteria:
{review_criteria}

Creative Details:
- Name: {creative_name}
- Format: {creative_format}
- Promoted Offering: {promoted_offering}
- Creative Data: {data_str}

Analyze this creative and provide your decision."""


async def review_creative_async(
    agent: Agent[None, CreativeReviewResult],
    review_criteria: str,
    creative_name: str,
    creative_format: str,
    promoted_offering: str,
    creative_data: dict | None = None,
) -> CreativeReviewResult:
    """Review a creative using the agent.

    Args:
        agent: The review agent
        review_criteria: Tenant's creative review criteria
        creative_name: Name of the creative
        creative_format: Format type of the creative
        promoted_offering: Product/offering being promoted
        creative_data: Additional creative data/metadata

    Returns:
        CreativeReviewResult with decision, reason, and confidence
    """
    prompt = build_review_prompt(
        review_criteria=review_criteria,
        creative_name=creative_name,
        creative_format=creative_format,
        promoted_offering=promoted_offering,
        creative_data=creative_data,
    )

    try:
        result = await agent.run(prompt)
        # pydantic-ai 1.x uses .output for structured data
        output = result.output
        logger.info(f"Creative review result: decision={output.decision}, confidence={output.confidence}")
        return output

    except Exception as e:
        logger.error(f"Review agent failed: {e}")
        raise


def parse_confidence_score(confidence: str) -> float:
    """Convert confidence string to numeric score.

    Args:
        confidence: "high", "medium", or "low"

    Returns:
        Float between 0.0 and 1.0
    """
    confidence_map = {"low": 0.3, "medium": 0.6, "high": 0.9}
    return confidence_map.get(confidence.lower(), 0.6)
