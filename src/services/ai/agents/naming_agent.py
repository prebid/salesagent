"""Pydantic AI agent for generating order names."""

import logging

from pydantic import BaseModel, Field
from pydantic_ai import Agent

logger = logging.getLogger(__name__)


class OrderName(BaseModel):
    """Structured output for order name generation."""

    name: str = Field(..., description="Concise, professional order name")


NAMING_SYSTEM_PROMPT = """You are an advertising campaign naming specialist.
Generate concise, professional order names for advertising campaigns.

Guidelines:
- Keep names professional and scannable
- Capture the essence of the campaign
- Include the buyer reference when provided
- Be concise but descriptive

Return ONLY the order name, nothing else."""


def create_naming_agent(model: str, max_length: int = 150) -> Agent[None, OrderName]:
    """Create a naming agent with the specified model.

    Args:
        model: Pydantic AI model string (e.g., "google-gla:gemini-2.0-flash")
        max_length: Maximum length for generated names

    Returns:
        Configured Agent instance
    """
    # Include max_length in the system prompt
    system_prompt = f"""{NAMING_SYSTEM_PROMPT}

Maximum name length: {max_length} characters."""

    return Agent(
        model=model,
        output_type=OrderName,
        system_prompt=system_prompt,
    )


def build_naming_prompt(
    buyer_ref: str,
    campaign_name: str | None,
    brand_name: str | None,
    budget_info: str | None,
    date_range: str,
    products: list[str],
    objectives: list[str] | None = None,
    max_length: int = 150,
) -> str:
    """Build the user prompt for name generation.

    Args:
        buyer_ref: Buyer's reference ID
        campaign_name: Optional campaign name
        brand_name: Brand name from manifest
        budget_info: Budget string (e.g., "$10,000.00 USD")
        date_range: Formatted date range
        products: List of product IDs
        objectives: Optional campaign objectives
        max_length: Maximum name length

    Returns:
        Formatted prompt string
    """
    context_parts = [
        f"Buyer Reference: {buyer_ref}",
        f"Campaign: {campaign_name or 'N/A'}",
        f"Brand: {brand_name or 'N/A'}",
    ]

    if budget_info:
        context_parts.append(f"Budget: {budget_info}")

    context_parts.append(f"Duration: {date_range}")
    context_parts.append(f"Products: {', '.join(products)}")

    if objectives:
        context_parts.append(f"Objectives: {', '.join(objectives[:2])}")

    context = "\n".join(context_parts)

    return f"""Generate a concise, professional order name for this advertising campaign.

Requirements:
- Maximum {max_length} characters
- Include buyer reference "{buyer_ref}" somewhere in the name
- Professional and scannable
- Captures the essence of the campaign

Campaign Details:
{context}"""


async def generate_name_async(
    agent: Agent[None, OrderName],
    buyer_ref: str,
    campaign_name: str | None,
    brand_name: str | None,
    budget_info: str | None,
    date_range: str,
    products: list[str],
    objectives: list[str] | None = None,
    max_length: int = 150,
) -> str:
    """Generate an order name using the agent.

    Args:
        agent: The naming agent
        buyer_ref: Buyer's reference ID
        campaign_name: Optional campaign name
        brand_name: Brand name from manifest
        budget_info: Budget string
        date_range: Formatted date range
        products: List of product IDs
        objectives: Optional campaign objectives
        max_length: Maximum name length

    Returns:
        Generated order name
    """
    prompt = build_naming_prompt(
        buyer_ref=buyer_ref,
        campaign_name=campaign_name,
        brand_name=brand_name,
        budget_info=budget_info,
        date_range=date_range,
        products=products,
        objectives=objectives,
        max_length=max_length,
    )

    try:
        result = await agent.run(prompt)
        # pydantic-ai 1.x uses .output for structured data
        generated_name = result.output.name.strip().strip('"').strip("'")

        # Validate length
        if len(generated_name) > max_length:
            logger.warning(f"Generated name too long ({len(generated_name)} > {max_length}), truncating")
            generated_name = generated_name[:max_length].rsplit(" ", 1)[0] + "..."

        logger.info(f"Generated auto_name: {generated_name}")
        return generated_name

    except Exception as e:
        logger.error(f"Naming agent failed: {e}")
        raise
