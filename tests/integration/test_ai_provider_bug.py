#!/usr/bin/env python3
"""Test to see if the AI provider has the Product validation bug."""

import asyncio
import json
import logging
import sys
from pathlib import Path

# Add the src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

# Enable debug logging
logging.basicConfig(level=logging.INFO)

from product_catalog_providers.ai import AIProductCatalog


async def test_ai_provider_bug(integration_db):
    """Test if the AI provider has Product validation issues."""

    print("🔍 Testing AI provider for Product validation bug...")

    # First, let's create a problematic product in the database to test with
    # This simulates what might be causing the issue on the external server

    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import Product as ProductModel

    with get_db_session() as session:
        # Create a test product with potentially problematic format data
        test_product = ProductModel(
            tenant_id="default",
            product_id="test_audio_bug",
            name="Test Audio Product",
            description="Test product to reproduce bug",
            formats=["audio_15s", "audio_30s"],  # JSONType expects list, not json.dumps()
            targeting_template={},
            delivery_type="guaranteed",
            is_fixed_price=True,
            cpm=10.0,
            is_custom=False,
        )
        session.merge(test_product)
        session.commit()
        print("   ✅ Created test product with problematic format data")

    try:
        # Test the AI provider
        config = {"model": "gemini-flash-latest", "max_products": 5}
        provider = AIProductCatalog(config)

        products = await provider.get_products(
            brief="test audio campaign",
            tenant_id="default",
            principal_id="test_principal",
            context={"promoted_offering": "test"},
            principal_data={},
        )

        print(f"   ✅ AI provider returned {len(products)} products")

        # Check if any product has the validation issue
        for _i, product in enumerate(products):
            product_dict = product.model_dump()
            if product.product_id == "test_audio_bug":
                print(f"   🔍 Found test product: {product.product_id}")
                print(f"   📊 Product data: {json.dumps(product_dict, indent=2)}")

                # Check if audio fields leaked as top-level keys
                if "audio_15s" in product_dict or "audio_30s" in product_dict:
                    print("   ❌ BUG FOUND: Audio format fields are top-level Product keys!")
                    return False
                else:
                    print("   ✅ No audio fields as top-level keys")

    except Exception as e:
        print(f"   ❌ AI provider failed with error: {e}")
        import traceback

        traceback.print_exc()

        # Check if this is the specific validation error we're looking for
        if "Field required" in str(e) and "formats" in str(e):
            print("   🎯 This matches the original validation error!")
            return False

    finally:
        # Clean up test product
        with get_db_session() as session:
            test_product = session.scalars(
                select(ProductModel).filter_by(tenant_id="default", product_id="test_audio_bug")
            ).first()
            if test_product:
                session.delete(test_product)
                session.commit()
                print("   🧹 Cleaned up test product")

    print("   ✅ AI provider test completed without reproducing the bug")
    return True


if __name__ == "__main__":
    success = asyncio.run(test_ai_provider_bug())
    sys.exit(0 if success else 1)
