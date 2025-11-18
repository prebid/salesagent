"""AI-powered product catalog provider using Gemini for intelligent matching."""

import json
import os
from typing import Any

import google.generativeai as genai

from src.core.database.database_session import get_db_session
from src.core.database.models import Product as ProductModel
from src.core.database.product_pricing import get_product_pricing_options
from src.core.schemas import PricingOption, Product

from .base import ProductCatalogProvider


class AIProductCatalog(ProductCatalogProvider):
    """
    AI-powered product catalog that uses Gemini to intelligently match
    products to briefs, simulating a RAG-like system.

    This provider:
    1. Fetches all available products from the database
    2. Uses Gemini to analyze the brief and rank/filter products
    3. Returns the most relevant products based on the AI's analysis

    Configuration:
        model: Gemini model to use (default: "gemini-flash-latest")
        max_products: Maximum number of products to return (default: 5)
        temperature: Model temperature for creativity (default: 0.3)
        include_reasoning: Include AI reasoning in response (default: false)
    """

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.model_name = config.get("model", "gemini-flash-latest")
        self.max_products = config.get("max_products", 5)
        self.temperature = config.get("temperature", 0.3)
        self.include_reasoning = config.get("include_reasoning", False)

        # Initialize Gemini
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable is required for AI product catalog")

        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(self.model_name)

    async def get_products(
        self,
        brief: str,
        tenant_id: str,
        principal_id: str | None = None,
        context: dict[str, Any] | None = None,
        principal_data: dict[str, Any] | None = None,
    ) -> list[Product]:
        """
        Get products that match the given brief using AI analysis.

        Args:
            brief: Natural language description of what the advertiser is looking for
            tenant_id: The tenant ID to fetch products for
            principal_id: Optional principal ID for personalization
            context: Additional context for the analysis
            principal_data: Principal/advertiser specific data for personalization
        """
        # Get all available products
        all_products = await self._get_all_products(tenant_id)

        if not all_products:
            return []

        if not brief or brief.strip() == "":
            # Return all products if no brief provided
            return all_products[: self.max_products]

        # Use AI to analyze and rank products
        try:
            relevant_products = await self._analyze_products_with_ai(brief, all_products, context, principal_data)
            return relevant_products[: self.max_products]
        except Exception as e:
            print(f"AI analysis failed: {e}")
            # Fallback to returning all products if AI fails
            return all_products[: self.max_products]

    async def _get_all_products(self, tenant_id: str) -> list[Product]:
        """Fetch all products from the database."""
        from sqlalchemy import select

        with get_db_session() as session:
            stmt = select(ProductModel).filter_by(tenant_id=tenant_id)
            product_models = session.scalars(stmt).all()

            products = []
            for product_model in product_models:
                # Get pricing from pricing_options (preferred) or legacy fields (fallback)
                pricing_options = get_product_pricing_options(product_model)
                first_pricing = pricing_options[0] if pricing_options else {}

                # Convert model to Product schema (only include AdCP-compliant fields)
                # Build product_data with proper types
                # Note: PostgreSQL with JSONType returns formats as Python objects (list of dicts)
                formats_raw = product_model.format_ids

                # Convert database format objects to FormatId objects
                # Database stores formats as list of dicts with 'agent_url' and 'id' keys
                from src.core.schemas import FormatId

                formats: list[FormatId] = []
                if formats_raw:
                    for fmt in formats_raw:
                        if isinstance(fmt, dict):
                            # Database format: {"agent_url": "...", "id": "..."}
                            formats.append(FormatId(agent_url=fmt.get("agent_url", ""), id=fmt.get("id", "")))  # type: ignore[arg-type]
                        elif isinstance(fmt, str):
                            # Legacy string format (backwards compatibility)
                            formats.append(FormatId(agent_url="", id=fmt))

                product_data: dict[str, Any] = {
                    "product_id": product_model.product_id,
                    "name": product_model.name,
                    "description": product_model.description or f"Advertising product: {product_model.name}",
                    "formats": formats,
                    "delivery_type": product_model.delivery_type or "non_guaranteed",
                    "is_custom": product_model.is_custom if product_model.is_custom is not None else False,
                    "pricing_options": pricing_options,  # Use pricing_options structure
                }

                # Create Product instance
                product = Product(**product_data)
                products.append(product)

            return products

    async def _analyze_products_with_ai(
        self,
        brief: str,
        products: list[Product],
        context: dict[str, Any] | None,
        principal_data: dict[str, Any] | None,
    ) -> list[Product]:
        """Use AI to analyze and rank products based on the brief."""

        # Prepare products data for AI analysis
        products_data = []
        for product in products:
            # Product schema objects already have pricing_options structure
            # Extract pricing info from first option for AI analysis
            # Type ignore: discriminated union types are compatible with PricingOption at runtime
            first_pricing: PricingOption | None = product.pricing_options[0] if product.pricing_options else None  # type: ignore[assignment]
            product_info = {
                "product_id": product.product_id,
                "name": product.name,
                "description": product.description,
                "formats": product.format_ids,
                "delivery_type": product.delivery_type,
                "is_custom": product.is_custom,
                "pricing": {
                    "model": first_pricing.pricing_model if first_pricing else None,
                    "rate": first_pricing.rate if first_pricing else None,
                    "is_fixed": first_pricing.is_fixed if first_pricing else None,
                    "min_spend": first_pricing.min_spend_per_package if first_pricing else None,
                },
            }
            products_data.append(product_info)

        # Build context string
        context_str = ""
        if context:
            context_str = f"Additional context: {json.dumps(context, indent=2)}\n"

        if principal_data:
            context_str += f"Advertiser data: {json.dumps(principal_data, indent=2)}\n"

        # Create the AI prompt
        prompt = f"""
You are an expert media buyer analyzing products for a programmatic advertising campaign.

{context_str}

Campaign Brief: {brief}

Available Products:
{json.dumps(products_data, indent=2)}

Your task:
1. Analyze each product's relevance to the campaign brief
2. Consider targeting capabilities, format compatibility, and pricing
3. Rank products from most to least relevant
4. Return the top {self.max_products} products

Response format (JSON only):
{{
  "products": [
    {{
      "product_id": "product_id_here",
      "relevance_score": 0.95,
      "reasoning": "Why this product is relevant"
    }}
  ]
}}

Focus on:
- Targeting alignment with brief requirements
- Format suitability for campaign goals
- Pricing compatibility with budget
- Geographic targeting match
- Delivery type appropriateness

Return ONLY the JSON response, no additional text.
"""

        try:
            # Generate AI response
            response = self.model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(temperature=self.temperature, max_output_tokens=2048),
            )

            # Parse AI response
            response_text = response.text.strip()

            # Remove markdown code blocks if present
            if response_text.startswith("```json"):
                response_text = response_text.replace("```json", "").replace("```", "")
            elif response_text.startswith("```"):
                response_text = response_text.replace("```", "")

            response_text = response_text.strip()

            ai_result = json.loads(response_text)

            # Extract ranked product IDs
            ranked_product_ids = []
            for product_analysis in ai_result.get("products", []):
                ranked_product_ids.append(product_analysis["product_id"])

            # Reorder products based on AI ranking
            ranked_products = []
            for product_id in ranked_product_ids:
                for product in products:
                    if product.product_id == product_id:
                        # Note: ai_reasoning and ai_score are internal fields
                        # They're not part of Product schema (AdCP spec)
                        # We just add the product as-is for now
                        ranked_products.append(product)
                        break

            return ranked_products

        except Exception as e:
            print(f"Error in AI analysis: {e}")
            # Return original products if AI analysis fails
            return products
