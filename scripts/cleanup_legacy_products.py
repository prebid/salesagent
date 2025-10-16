#!/usr/bin/env python3
"""Script to identify and optionally clean up legacy products.

Legacy products are identified by:
1. String format IDs (instead of proper FormatId dict structure with agent_url)
2. Missing or invalid agent_url in formats
3. Using format_id instead of id (AdCP spec requires id)

Usage:
    python scripts/cleanup_legacy_products.py --dry-run  # List legacy products
    python scripts/cleanup_legacy_products.py --delete   # Delete legacy products
    python scripts/cleanup_legacy_products.py --fix      # Convert to proper FormatId structure

Note: The format_id → id migration is handled by Alembic migration 0d4fe6eb03ab.
      This script identifies other issues (string IDs, missing agent_url, etc.)
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Product

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def identify_legacy_products(tenant_id=None):
    """Identify products with legacy format data.

    Args:
        tenant_id: Optional tenant ID to filter by

    Returns:
        List of (product, issues) tuples where issues is a list of problem descriptions
    """
    legacy_products = []

    with get_db_session() as session:
        stmt = select(Product)
        if tenant_id:
            stmt = stmt.filter_by(tenant_id=tenant_id)

        products = session.scalars(stmt).all()

        for product in products:
            issues = []

            if not product.formats:
                issues.append("No formats defined")
                continue

            # Check each format
            for idx, fmt in enumerate(product.formats):
                if isinstance(fmt, str):
                    # Legacy: plain string format ID
                    issues.append(f"Format {idx}: String format ID '{fmt}' (should be dict)")
                elif isinstance(fmt, dict):
                    # Check for required fields
                    if "agent_url" not in fmt:
                        issues.append(f"Format {idx}: Missing agent_url field")
                    if "id" not in fmt and "format_id" not in fmt:
                        issues.append(f"Format {idx}: Missing format_id field")
                else:
                    issues.append(f"Format {idx}: Invalid type {type(fmt)}")

            if issues:
                legacy_products.append((product, issues))

    return legacy_products


def delete_legacy_products(legacy_products):
    """Delete legacy products from database.

    Args:
        legacy_products: List of (product, issues) tuples
    """
    with get_db_session() as session:
        for product, _issues in legacy_products:
            logger.info(f"Deleting product {product.product_id} ({product.name})")
            # Re-fetch product in this session
            stmt = select(Product).filter_by(tenant_id=product.tenant_id, product_id=product.product_id)
            product_in_session = session.scalars(stmt).first()
            if product_in_session:
                session.delete(product_in_session)

        session.commit()
        logger.info(f"Deleted {len(legacy_products)} legacy products")


def fix_legacy_products(legacy_products, default_agent_url="http://localhost:8888"):
    """Attempt to fix legacy products by converting string formats to proper structure.

    Args:
        legacy_products: List of (product, issues) tuples
        default_agent_url: Default agent URL to use for formats without one
    """
    with get_db_session() as session:
        for product, _issues in legacy_products:
            logger.info(f"Fixing product {product.product_id} ({product.name})")

            # Re-fetch product in this session
            stmt = select(Product).filter_by(tenant_id=product.tenant_id, product_id=product.product_id)
            product_in_session = session.scalars(stmt).first()
            if not product_in_session:
                logger.warning(f"Product {product.product_id} not found in session")
                continue

            fixed_formats = []
            for fmt in product_in_session.formats:
                if isinstance(fmt, str):
                    # Convert string to proper FormatId structure
                    fixed_formats.append({"agent_url": default_agent_url, "id": fmt})
                elif isinstance(fmt, dict):
                    # Fix missing fields
                    fixed_fmt = dict(fmt)
                    if "agent_url" not in fixed_fmt:
                        fixed_fmt["agent_url"] = default_agent_url
                    if "id" not in fixed_fmt and "format_id" in fixed_fmt:
                        # Rename format_id to id (AdCP spec)
                        fixed_fmt["id"] = fixed_fmt.pop("format_id")
                    elif "id" not in fixed_fmt:
                        logger.error(f"Cannot fix format: {fmt} (no format_id or id field)")
                        continue
                    fixed_formats.append(fixed_fmt)
                else:
                    logger.error(f"Cannot fix format: {fmt} (invalid type {type(fmt)})")

            if fixed_formats:
                product_in_session.formats = fixed_formats
                from sqlalchemy.orm import attributes

                attributes.flag_modified(product_in_session, "formats")
                logger.info(f"Fixed {len(fixed_formats)} formats for product {product.product_id}")

        session.commit()
        logger.info(f"Fixed {len(legacy_products)} legacy products")


def main():
    parser = argparse.ArgumentParser(description="Identify and clean up legacy products")
    parser.add_argument("--tenant-id", help="Filter by tenant ID")
    parser.add_argument(
        "--dry-run", action="store_true", default=True, help="List legacy products without making changes (default)"
    )
    parser.add_argument("--delete", action="store_true", help="Delete legacy products")
    parser.add_argument(
        "--fix", action="store_true", help="Fix legacy products by converting to proper FormatId structure"
    )
    parser.add_argument(
        "--default-agent-url",
        default="http://localhost:8888",
        help="Default agent URL for fixing products (default: http://localhost:8888)",
    )

    args = parser.parse_args()

    # Identify legacy products
    logger.info("Scanning for legacy products...")
    legacy_products = identify_legacy_products(args.tenant_id)

    if not legacy_products:
        logger.info("✓ No legacy products found!")
        return 0

    # Print report
    logger.info(f"\nFound {len(legacy_products)} legacy products:\n")
    for product, issues in legacy_products:
        logger.info(f"Product: {product.product_id} ({product.name})")
        logger.info(f"  Tenant: {product.tenant_id}")
        for issue in issues:
            logger.info(f"  ⚠️  {issue}")
        logger.info("")

    # Take action based on flags
    if args.delete:
        logger.info("Deleting legacy products...")
        delete_legacy_products(legacy_products)
        logger.info("✓ Done!")
    elif args.fix:
        logger.info(f"Fixing legacy products (using default agent URL: {args.default_agent_url})...")
        fix_legacy_products(legacy_products, args.default_agent_url)
        logger.info("✓ Done!")
    else:
        logger.info("Dry run - no changes made. Use --delete or --fix to make changes.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
