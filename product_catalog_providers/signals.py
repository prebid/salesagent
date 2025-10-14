"""Signals-based product catalog provider for upstream signals discovery integration."""

import asyncio
import logging
from typing import Any

from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

from src.core.database.database_session import get_db_session
from src.core.database.models import Product as ModelProduct
from src.core.database.product_pricing import get_product_pricing_options
from src.core.schemas import Product

from .base import ProductCatalogProvider

logger = logging.getLogger(__name__)


class SignalsDiscoveryProvider(ProductCatalogProvider):
    """
    Product catalog provider that integrates with an upstream AdCP signals discovery agent.

    This provider:
    1. Calls upstream signals agent to get relevant signals for a brief
    2. Transforms signals into custom products with appropriate targeting
    3. Falls back to database products if signals agent is unavailable
    4. Only forwards requests when a brief is provided (optimization per issue #106)

    Configuration:
        enabled: Whether signals discovery is enabled (default: False)
        upstream_url: URL of the upstream signals discovery agent
        upstream_token: Authentication token for the signals agent
        auth_header: Header name for authentication (default: "x-adcp-auth")
        forward_promoted_offering: Include promoted_offering in signals request (default: True)
        timeout: Request timeout in seconds (default: 30)
        fallback_to_database: Use database products if signals unavailable (default: True)
        max_signal_products: Maximum number of products to create from signals (default: 10)
    """

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.enabled = config.get("enabled", False)
        self.upstream_url = config.get("upstream_url", "")
        self.upstream_token = config.get("upstream_token", "")
        self.auth_header = config.get("auth_header", "x-adcp-auth")
        self.forward_promoted_offering = config.get("forward_promoted_offering", True)
        self.timeout = config.get("timeout", 30)
        self.fallback_to_database = config.get("fallback_to_database", True)
        self.max_signal_products = config.get("max_signal_products", 10)
        self.client = None

    async def initialize(self) -> None:
        """Initialize the MCP client connection if enabled."""
        if not self.enabled or not self.upstream_url:
            logger.info("Signals discovery disabled or no upstream URL configured")
            return

        try:
            headers = {}
            if self.upstream_token:
                headers[self.auth_header] = self.upstream_token

            transport = StreamableHttpTransport(url=self.upstream_url, headers=headers)
            self.client = Client(transport=transport)
            await self.client.__aenter__()
            logger.info(f"Initialized signals discovery connection to {self.upstream_url}")
        except Exception as e:
            logger.error(f"Failed to initialize signals discovery client: {e}")
            self.client = None

    async def shutdown(self) -> None:
        """Clean up the MCP client connection."""
        if self.client:
            try:
                await self.client.__aexit__(None, None, None)
                logger.info("Shut down signals discovery connection")
            except Exception as e:
                logger.error(f"Error shutting down signals client: {e}")

    async def get_products(
        self,
        brief: str,
        tenant_id: str,
        principal_id: str | None = None,
        context: dict[str, Any] | None = None,
        principal_data: dict[str, Any] | None = None,
    ) -> list[Product]:
        """
        Get products enhanced with signals from upstream discovery agent.

        Implementation follows the requirements from issue #106:
        - Only forward to signals agent if brief is provided
        - Include promoted_offering when configured
        - Transform signals into custom products
        - Fall back to database products on error
        """
        products = []

        # If signals discovery is disabled, fall back to database immediately
        if not self.enabled:
            logger.debug("Signals discovery disabled, falling back to database")
            return await self._get_database_products(brief, tenant_id, principal_id)

        # Optimization per issue #106: "if there is no brief don't forward"
        if not brief or not brief.strip():
            logger.debug("No brief provided, skipping signals discovery")
            return await self._get_database_products(brief, tenant_id, principal_id)

        # Try to get signals from upstream agent
        try:
            signals = await self._get_signals_from_upstream(brief, tenant_id, principal_id, context, principal_data)
            if signals:
                logger.info(f"Retrieved {len(signals)} signals from upstream agent")
                products = await self._transform_signals_to_products(signals, brief, tenant_id)
        except Exception as e:
            logger.error(f"Error calling signals discovery agent: {e}")

        # If no products from signals or fallback enabled, include database products
        if not products or self.fallback_to_database:
            database_products = await self._get_database_products(brief, tenant_id, principal_id)
            if not products:
                logger.info("Using database products as primary source")
                products = database_products
            else:
                logger.info(
                    f"Combining {len(products)} signal products with {len(database_products)} database products"
                )
                products.extend(database_products)

        return products

    async def _get_signals_from_upstream(
        self,
        brief: str,
        tenant_id: str,
        principal_id: str | None,
        context: dict[str, Any] | None,
        principal_data: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Call upstream signals discovery agent to get relevant signals."""
        if not self.client:
            await self.initialize()

        if not self.client:
            raise Exception("Signals discovery client not available")

        # Prepare request for signals discovery
        request_data = {
            "brief": brief,
            "tenant_id": tenant_id,
        }

        if principal_id:
            request_data["principal_id"] = principal_id

        if principal_data:
            request_data["principal_data"] = principal_data

        if context:
            request_data["context"] = context

        # Include promoted_offering if configured and available
        if self.forward_promoted_offering and context and "promoted_offering" in context:
            request_data["promoted_offering"] = context["promoted_offering"]

        # Call the upstream signals discovery tool
        try:
            result = await asyncio.wait_for(self.client.call_tool("get_signals", request_data), timeout=self.timeout)

            # Return raw signal data (AdCP protocol format)
            return result.get("signals", [])

        except TimeoutError as err:
            raise Exception(f"Signals discovery timeout after {self.timeout} seconds") from err

    async def _transform_signals_to_products(
        self, signals: list[dict[str, Any]], brief: str, tenant_id: str
    ) -> list[Product]:
        """Transform signals into custom products with appropriate targeting and pricing."""
        products = []

        # Group signals by category for better organization
        signals_by_category = {}
        for signal in signals:
            category = signal.get("category") or "general"
            if category not in signals_by_category:
                signals_by_category[category] = []
            signals_by_category[category].append(signal)

        product_count = 0
        for category, category_signals in signals_by_category.items():
            if product_count >= self.max_signal_products:
                break

            # Create a product for this category of signals
            product = await self._create_product_from_signals(category_signals, category, brief, tenant_id)
            if product:
                products.append(product)
                product_count += 1

        logger.info(f"Created {len(products)} products from {len(signals)} signals")
        return products

    async def _create_product_from_signals(
        self, signals: list[dict[str, Any]], category: str, brief: str, tenant_id: str
    ) -> Product | None:
        """Create a single product from a group of related signals."""
        if not signals:
            return None

        # Calculate average CPM and coverage
        cpm_values = [s["pricing"]["cpm"] for s in signals if s.get("pricing") and s["pricing"].get("cpm") is not None]
        coverage_percentages = [s["coverage_percentage"] for s in signals if s.get("coverage_percentage") is not None]

        avg_cpm = sum(cpm_values) / len(cpm_values) if cpm_values else 5.0
        total_coverage = sum(coverage_percentages) if coverage_percentages else 0

        # Create targeting overlay with signal IDs
        signal_ids = [s["signal_agent_segment_id"] for s in signals]
        targeting_overlay = {
            "signals": signal_ids,
            "signal_category": category,
            "signal_types": list({s["signal_type"] for s in signals}),
        }

        # Create product name and description
        signal_names = [s["name"] for s in signals[:3]]  # Use first 3 for name
        product_name = f"Signal-Enhanced {category.title()}: {', '.join(signal_names)}"
        if len(signals) > 3:
            product_name += f" (+{len(signals) - 3} more)"

        product_description = f"Custom product targeting based on signals discovery for brief: '{brief[:100]}...'"
        product_description += f"\n\nTargeted signals: {', '.join([s['name'] for s in signals])}"

        # Base price calculation (could be enhanced with more sophisticated logic)
        base_price = 5.00  # Base CPM
        adjusted_price = avg_cpm if avg_cpm > 0 else base_price

        # Generate unique product ID
        import hashlib

        product_id_hash = hashlib.md5(f"signals_{tenant_id}_{category}_{len(signals)}".encode()).hexdigest()[:12]
        product_id = f"signal_{product_id_hash}"

        # Create AdCP-compliant Product (without internal fields like tenant_id)
        return Product(
            product_id=product_id,
            name=product_name,
            description=product_description,
            formats=["display_300x250", "display_728x90", "video_pre_roll"],  # Standard format IDs
            delivery_type="non_guaranteed",  # Signals products are typically programmatic
            is_fixed_price=False,  # Signals products are typically programmatic
            cpm=adjusted_price,  # Use cpm field instead of base_price
            min_spend=100.0,
            is_custom=True,  # These are custom products created from signals
            brief_relevance=f"Generated from {len(signals)} signals in {category} category for: {brief[:100]}...",
            property_tags=["all_inventory"],  # Required per AdCP spec
        )

    async def _get_database_products(self, brief: str, tenant_id: str, principal_id: str | None) -> list[Product]:
        """Fallback method to get products from database."""
        products = []

        try:
            with get_db_session() as db_session:
                query = db_session.query(ModelProduct).filter_by(tenant_id=tenant_id, is_active=True)

                # Simple brief matching (could be enhanced with better search)
                if brief and brief.strip():
                    brief_lower = brief.lower()
                    query = query.filter(
                        ModelProduct.name.ilike(f"%{brief_lower}%") | ModelProduct.description.ilike(f"%{brief_lower}%")
                    )

                db_products = query.limit(20).all()

                for db_product in db_products:
                    # Convert database model to AdCP-compliant Product schema
                    # (Similar to database.py approach - only include AdCP spec fields)
                    # Get pricing from pricing_options (preferred) or legacy fields (fallback)
                    pricing_options = get_product_pricing_options(db_product)
                    first_pricing = pricing_options[0] if pricing_options else {}

                    product_data = {
                        "product_id": db_product.product_id,
                        "name": db_product.name,
                        "description": db_product.description or f"Advertising product: {db_product.name}",
                        "formats": db_product.formats or [],
                        "delivery_type": "guaranteed" if first_pricing.get("is_fixed") else "non_guaranteed",
                        "is_fixed_price": first_pricing.get("is_fixed", False),
                        "cpm": first_pricing.get("rate"),
                        "min_spend": float(db_product.min_spend) if db_product.min_spend else None,
                        "is_custom": getattr(db_product, "is_custom", False),
                        "property_tags": getattr(
                            db_product, "property_tags", ["all_inventory"]
                        ),  # Required per AdCP spec
                    }

                    # Handle JSON fields (might be strings in SQLite)
                    if isinstance(product_data["formats"], str):
                        import json

                        try:
                            product_data["formats"] = json.loads(product_data["formats"])
                        except json.JSONDecodeError:
                            product_data["formats"] = []

                    # Extract format IDs if formats are objects
                    if product_data["formats"]:
                        format_ids = []
                        for fmt in product_data["formats"]:
                            if isinstance(fmt, dict) and "format_id" in fmt:
                                format_ids.append(fmt["format_id"])
                            elif isinstance(fmt, str):
                                format_ids.append(fmt)
                        product_data["formats"] = format_ids

                    product = Product(**product_data)
                    products.append(product)

        except Exception as e:
            logger.error(f"Error fetching database products: {e}")

        return products
