#!/usr/bin/env python3
"""Test script to directly call get_products and reproduce the validation issue."""

import asyncio
import logging
import sys
from pathlib import Path

# Add the src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

# Enable debug logging
logging.basicConfig(level=logging.DEBUG)


async def test_direct_get_products():
    """Test the get_products function directly."""
    # Lazy imports to avoid triggering load_config() at module import time
    from src.core.main import get_products
    from src.core.tool_context import ToolContext

    print("Testing direct get_products call...")

    # Create a test context similar to what would come from MCP
    test_context = ToolContext(
        tenant_id="default",
        principal_id="test_principal",
        testing_context={},
        context_id="test-context",
        tool_name="get_products",
        request_timestamp="2025-09-20T18:00:00Z",
    )

    try:
        # Call get_products directly
        result = await get_products(
            brief="Discover available advertising products for testing",
            promoted_offering="gourmet robot food",
            context=test_context,
        )

        print("✅ get_products call succeeded!")
        print(f"Response type: {type(result)}")
        print(f"Response: {result.model_dump() if hasattr(result, 'model_dump') else result}")

        if hasattr(result, "products"):
            print(f"Number of products: {len(result.products)}")
            for i, product in enumerate(result.products):
                print(f"Product {i}: {product.model_dump()}")

    except Exception as e:
        print(f"❌ get_products call failed: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_direct_get_products())
