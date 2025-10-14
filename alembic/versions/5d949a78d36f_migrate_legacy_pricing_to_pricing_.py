"""migrate_legacy_pricing_to_pricing_options

Migrate all products with legacy pricing fields (cpm, price_guidance) to use
pricing_options table. This ensures all pricing is stored in the new format.

Revision ID: 5d949a78d36f
Revises: 0937c1edf84c
Create Date: 2025-10-13 01:47:49.462268

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = '5d949a78d36f'
down_revision: Union[str, Sequence[str], None] = '0937c1edf84c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Convert legacy pricing to pricing_options."""
    conn = op.get_bind()

    # Get all products with legacy pricing that don't have pricing_options yet
    products = conn.execute(text("""
        SELECT p.tenant_id, p.product_id, p.is_fixed_price, p.cpm, p.price_guidance, p.currency
        FROM products p
        WHERE NOT EXISTS (
            SELECT 1 FROM pricing_options po
            WHERE po.tenant_id = p.tenant_id AND po.product_id = p.product_id
        )
    """)).fetchall()

    migrated_count = 0
    for product in products:
        tenant_id, product_id, is_fixed_price, cpm, price_guidance, currency = product

        # Determine currency (default to USD if not set)
        currency_code = currency or 'USD'

        # Create pricing option based on legacy fields
        if is_fixed_price and cpm:
            # Fixed CPM pricing
            conn.execute(text("""
                INSERT INTO pricing_options
                (pricing_option_id, tenant_id, product_id, pricing_model, rate, currency, is_fixed, created_at, updated_at)
                VALUES (:id, :tenant_id, :product_id, 'cpm', :rate, :currency, true, NOW(), NOW())
            """), {
                'id': str(uuid.uuid4()),
                'tenant_id': tenant_id,
                'product_id': product_id,
                'rate': float(cpm),
                'currency': currency_code
            })
            migrated_count += 1

        elif not is_fixed_price and price_guidance:
            # Auction CPM pricing with price guidance
            # price_guidance can be in two formats:
            # 1. New format: {"floor": X, "p25": Y, "p50": Z, ...}
            # 2. Old format: {"min": X, "max": Y}

            # Parse price_guidance JSON
            import json
            if isinstance(price_guidance, str):
                pg = json.loads(price_guidance)
            else:
                pg = price_guidance

            # Build price_guidance for pricing_option
            guidance_data = {}
            if 'floor' in pg:
                # New format - use as-is
                guidance_data = pg
            elif 'min' in pg:
                # Old format - convert to new format
                guidance_data = {'floor': pg['min']}
                if 'max' in pg and pg['max'] != pg['min']:
                    guidance_data['p90'] = pg['max']

            if guidance_data:
                conn.execute(text("""
                    INSERT INTO pricing_options
                    (pricing_option_id, tenant_id, product_id, pricing_model, currency, is_fixed, price_guidance, created_at, updated_at)
                    VALUES (:id, :tenant_id, :product_id, 'cpm', :currency, false, :price_guidance, NOW(), NOW())
                """), {
                    'id': str(uuid.uuid4()),
                    'tenant_id': tenant_id,
                    'product_id': product_id,
                    'currency': currency_code,
                    'price_guidance': json.dumps(guidance_data)
                })
                migrated_count += 1

    print(f"✅ Migrated {migrated_count} products to pricing_options")


def downgrade() -> None:
    """Remove pricing_options created by this migration.

    Note: This only removes pricing_options for products that still have
    legacy pricing fields. It does not restore data if legacy fields were
    already removed by a subsequent migration.
    """
    conn = op.get_bind()

    # Delete pricing_options that match legacy pricing (best effort)
    result = conn.execute(text("""
        DELETE FROM pricing_options po
        WHERE EXISTS (
            SELECT 1 FROM products p
            WHERE p.tenant_id = po.tenant_id
            AND p.product_id = po.product_id
            AND (
                (p.is_fixed_price = true AND p.cpm IS NOT NULL AND po.is_fixed = true AND po.pricing_model = 'cpm')
                OR (p.is_fixed_price = false AND p.price_guidance IS NOT NULL AND po.is_fixed = false AND po.pricing_model = 'cpm')
            )
        )
    """))

    print(f"✅ Removed {result.rowcount} pricing_options in downgrade")
