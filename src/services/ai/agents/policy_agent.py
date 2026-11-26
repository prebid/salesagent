"""Pydantic AI agent for advertising brief policy compliance checking."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from pydantic_ai import Agent

    from src.core.schemas.advertising_policy import AdvertisingPolicy


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
    from pydantic_ai import Agent

    return Agent(
        model=model,
        output_type=PolicyAnalysis,
        system_prompt=POLICY_SYSTEM_PROMPT,
    )


def build_policy_prompt(text: str, tenant_policy: AdvertisingPolicy | None = None) -> str:
    """Build the user prompt for policy analysis.

    Args:
        text: The advertising brief text to analyze
        tenant_policy: The advertising policy the seller saved, whose prohibitions the
            prompt names

    Returns:
        Formatted prompt string
    """
    prompt_parts = [f"Analyze this advertising brief:\n\n{text}"]

    if tenant_policy is not None:
        rules_text = []

        # The default prohibitions are enforced alongside the seller's own.
        all_prohibited_categories = tenant_policy.default_prohibited_categories + tenant_policy.prohibited_categories
        all_prohibited_tactics = tenant_policy.default_prohibited_tactics + tenant_policy.prohibited_tactics

        if tenant_policy.prohibited_advertisers:
            rules_text.append(f"Prohibited advertisers/domains: {', '.join(tenant_policy.prohibited_advertisers)}")

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
    tenant_policy: AdvertisingPolicy | None = None,
) -> PolicyAnalysis:
    """Run policy compliance check using the agent.

    Args:
        agent: The policy check agent
        text: Text to analyze (brief + brand info)
        tenant_policy: The advertising policy the seller saved

    Returns:
        PolicyAnalysis with compliance result
    """
    from src.services.ai.factory import run_agent

    prompt = build_policy_prompt(text, tenant_policy)
    # A provider failure raises AIProviderError; the caller (get_products) fails open on it.
    result = await run_agent(agent, prompt)
    # pydantic-ai 1.x uses .output for structured data
    return result.output
