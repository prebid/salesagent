#!/usr/bin/env python3
"""Diagnostic script to check product formats in database."""

import json
import sys

from sqlalchemy import select

# Add src to path
sys.path.insert(0, "/Users/brianokelley/Developer/salesagent/.conductor/atlanta-v1")

from src.core.database.database_session import get_db_session
from src.core.database.models import Product


def diagnose_product_formats(product_id: str = None):
    """Check product formats in database."""
    with get_db_session() as session:
        if product_id:
            # Check specific product
            stmt = select(Product).where(Product.product_id == product_id)
            products = [session.scalars(stmt).first()]
            if not products[0]:
                print(f"Product {product_id} not found")
                return
        else:
            # Check all products
            stmt = select(Product).order_by(Product.name).limit(5)
            products = session.scalars(stmt).all()

        print(f"\n{'='*80}")
        print("PRODUCT FORMATS DIAGNOSTIC")
        print(f"{'='*80}\n")

        for product in products:
            print(f"Product: {product.product_id} - {product.name}")
            print(f"  Tenant: {product.tenant_id}")
            print(f"  Formats type: {type(product.formats)}")
            print(f"  Formats value: {product.formats}")

            if product.formats:
                if isinstance(product.formats, list):
                    print(f"  Formats count: {len(product.formats)}")
                    for i, fmt in enumerate(product.formats[:3]):  # Show first 3
                        print(f"    [{i}] {fmt}")
                elif isinstance(product.formats, str):
                    try:
                        parsed = json.loads(product.formats)
                        print(f"  Formats (JSON string): {len(parsed)} items")
                        for i, fmt in enumerate(parsed[:3]):
                            print(f"    [{i}] {fmt}")
                    except:
                        print("  ERROR: formats is string but not valid JSON")
                else:
                    print(f"  ERROR: Unexpected formats type: {type(product.formats)}")
            else:
                print("  ⚠️  Formats is empty/None")

            print(f"{'-'*80}\n")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        diagnose_product_formats(sys.argv[1])
    else:
        print("Checking last 5 products...")
        diagnose_product_formats()
