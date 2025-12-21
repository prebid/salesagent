"""Pydantic AI agent for advertising brief policy compliance checking."""

import logging
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent

logger = logging.getLogger(__name__)


class PolicyAnalysis(BaseModel):
    """Structured output from AI policy analysis."""

    status: Literal["allowed", "restricted", "blocked"]
    reason: str | None = None
    restrictions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


POLICY_SYSTEM_PROMPT = """You are a policy compliance checker for advertising content.
Analyze the provided advertising brief and determine if it violates any advertising policies.

You must check for:
1. Targeting of vulnerable populations (children, elderly, disabled)
2. Discriminatory content based on protected characteristics
3. Illegal or heavily regulated products/services
4. Misleading or deceptive claims
5. Harmful content that could exploit users
6. Content that violates platform brand safety guidelines

Respond with:
- status: "allowed" if no issues, "restricted" if some concerns, "blocked" if severe violations
- reason: explanation if blocked
- restrictions: list of restrictions if status is restricted
- warnings: list of policy warnings even if allowed

Be strict in your analysis. When in doubt, mark as restricted rather than allowed."""


def create_policy_agent(model: str) -> Agent[None, PolicyAnalysis]:
    """Create a policy check agent with the specified model.

    Args:
        model: Pydantic AI model string (e.g., "google-gla:gemini-2.0-flash")

    Returns:
        Configured Agent instance
    """
    return Agent(
        model=model,
        output_type=PolicyAnalysis,
        system_prompt=POLICY_SYSTEM_PROMPT,
    )


def build_policy_prompt(text: str, tenant_policies: dict | None = None) -> str:
    """Build the user prompt for policy analysis.

    Args:
        text: The advertising brief text to analyze
        tenant_policies: Optional tenant-specific policy configuration

    Returns:
        Formatted prompt string
    """
    prompt_parts = [f"Analyze this advertising brief:\n\n{text}"]

    if tenant_policies:
        rules_text = []

        # Default prohibited categories and tactics are enforced for all
        default_categories = tenant_policies.get("default_prohibited_categories", [])
        default_tactics = tenant_policies.get("default_prohibited_tactics", [])

        # Combine default and custom policies
        all_prohibited_advertisers = tenant_policies.get("prohibited_advertisers", [])
        all_prohibited_categories = default_categories + tenant_policies.get("prohibited_categories", [])
        all_prohibited_tactics = default_tactics + tenant_policies.get("prohibited_tactics", [])

        if all_prohibited_advertisers:
            rules_text.append(f"Prohibited advertisers/domains: {', '.join(all_prohibited_advertisers)}")

        if all_prohibited_categories:
            rules_text.append(f"Prohibited content categories: {', '.join(all_prohibited_categories)}")

        if all_prohibited_tactics:
            rules_text.append(f"Prohibited advertising tactics: {', '.join(all_prohibited_tactics)}")

        if rules_text:
            prompt_parts.append("\n\nAdditional policy rules to enforce:\n" + "\n".join(rules_text))

    return "".join(prompt_parts)


async def check_policy_compliance(
    agent: Agent[None, PolicyAnalysis],
    text: str,
    tenant_policies: dict | None = None,
) -> PolicyAnalysis:
    """Run policy compliance check using the agent.

    Args:
        agent: The policy check agent
        text: Text to analyze (brief + brand info)
        tenant_policies: Optional tenant-specific policies

    Returns:
        PolicyAnalysis with compliance result
    """
    prompt = build_policy_prompt(text, tenant_policies)

    try:
        result = await agent.run(prompt)
        # pydantic-ai 1.x uses .output for structured data
        return result.output
    except Exception as e:
        logger.error(f"Policy agent failed: {e}")
        # Return allowed with warning on failure
        return PolicyAnalysis(
            status="allowed",
            warnings=[f"AI policy check unavailable: {str(e)}"],
        )
