"""Pydantic AI agent for product ranking based on brief relevance."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from src.core.schemas import Product

if TYPE_CHECKING:
    from pydantic_ai import Agent

logger = logging.getLogger(__name__)


class ProductRanking(BaseModel):
    """Ranking for a single product."""

    product_id: str = Field(..., description="The product ID being ranked")
    relevance_score: float = Field(
        ..., ge=0.0, le=1.0, description="Relevance score from 0 (not relevant) to 1 (highly relevant)"
    )
    reason: str = Field(..., description="Brief explanation of why this product is relevant/not relevant")


class ProductRankingResult(BaseModel):
    """Structured output from AI product ranking."""

    rankings: list[ProductRanking] = Field(..., description="List of products with their relevance rankings")


RANKING_SYSTEM_PROMPT = """You are a product ranking assistant for an advertising platform.

Your job is to rank advertising products based on how well they match a buyer's brief/requirements.

For each product, provide:
- A relevance_score from 0.0 to 1.0 (higher = more relevant)
- A brief reason explaining the relevance

Consider factors like:
- How well the product's name/description matches the brief
- Format suitability for the campaign goals
- Audience targeting alignment
- Any other relevant product attributes

Be objective and consistent in your scoring."""


def create_ranking_agent(model: Any) -> Agent[None, ProductRankingResult]:
    """Create a product ranking agent with the specified model.

    Args:
        model: Pydantic AI model instance or string

    Returns:
        Configured Agent instance
    """
    from pydantic_ai import Agent

    return Agent(
        model=model,
        output_type=ProductRankingResult,
        system_prompt=RANKING_SYSTEM_PROMPT,
    )


def _rankable_formats(product: Product) -> list[str]:
    """How *product*'s formats are described to the ranking model.

    THE CANONICAL ARM FIRST, as the kinds it declares. ``format_kind`` is what a brief can
    actually be matched against -- "video", "native in feed" -- whereas a deprecated
    ``format_id`` is an opaque name whose meaning the model has to guess from its spelling.
    A canonical-only product used to contribute an EMPTY list here, so the model ranked it
    as a product with no formats at all (salesagent-ol033.8, A7).

    The legacy arm answers for a legacy-only product, spelled through
    ``format_resolver.format_display`` -- the one renderer of a format identity. The
    ``str(f)`` this replaced rendered a pydantic model's repr into the prompt
    ("agent_url=Url('https://...') id='display_300x250_image' width=None ..."), which spends
    the model's attention on field names.

    Both arms rather than one: ``core/product.json`` lets a product carry either or both
    during the 3.x window, and a ranking input that drops an arm ranks a legal product on
    less than it declares.
    """
    from src.core.format_resolver import format_display, format_identity_or_none
    from src.core.format_satisfaction import product_declarations

    kinds = [str(declaration.format_kind) for declaration in product_declarations(product.format_options)]
    legacy = [
        format_display(identity)
        for identity in (format_identity_or_none(entry) for entry in product.format_ids or [])
        if identity is not None
    ]
    return kinds or legacy


def build_ranking_prompt(
    custom_prompt: str,
    brief: str,
    products: list[Product],
) -> str:
    """Build the user prompt for product ranking.

    Args:
        custom_prompt: Tenant's custom ranking prompt
        brief: The buyer's brief/requirements
        products: List of Product models to rank

    Returns:
        Formatted prompt string
    """
    simplified_products = []
    for p in products:
        simplified = {
            "product_id": p.product_id,
            "name": p.name,
            "description": p.description,
            "formats": _rankable_formats(p),
            "channels": [c.value for c in (p.channels or [])],
            "delivery_type": p.delivery_type.value,
        }
        simplified_products.append(simplified)

    products_str = json.dumps(simplified_products, indent=2)

    return f"""Rank these products based on relevance to the buyer's brief.

{custom_prompt}

Buyer's Brief:
{brief}

Products to Rank:
{products_str}

Provide a relevance_score (0.0-1.0) and brief reason for each product."""


async def rank_products_async(
    agent: Agent[None, ProductRankingResult],
    custom_prompt: str,
    brief: str,
    products: list[Product],
) -> ProductRankingResult:
    """Rank products using the agent.

    Args:
        agent: The ranking agent
        custom_prompt: Tenant's custom ranking prompt
        brief: The buyer's brief
        products: List of Product models

    Returns:
        ProductRankingResult with rankings for each product
    """
    prompt = build_ranking_prompt(custom_prompt, brief, products)
    result = await agent.run(prompt)
    # pydantic-ai 1.x uses .output for structured data
    return result.output
