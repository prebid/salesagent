"""add_package_pricing_columns

Extract budget, bid_price, and pacing from package_config JSONB to dedicated columns
for better query performance, data integrity, and AdCP schema compliance.

Per AdCP spec (https://adcontextprotocol.org/schemas/v1/core/package.json):
- budget: number (float, package-level)
- bid_price: number (float, optional, for auction pricing)
- pacing: enum ("even", "asap", "front_loaded")

Revision ID: 319e6b366151
Revises: a098c8bb42ed
Create Date: 2025-10-27 21:31:01.068931
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text, DECIMAL


# revision identifiers, used by Alembic.
revision: str = '319e6b366151'
down_revision: Union[str, Sequence[str], None] = 'a098c8bb42ed'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Extract budget, bid_price, pacing from package_config to dedicated columns."""
    connection = op.get_bind()

    print("\n" + "="*70)
    print("MIGRATION: add_package_pricing_columns")
    print("="*70)

    # Step 1: Add columns (no defaults = instant, no table rewrite)
    print("\n📝 Step 1: Adding columns...")

    # Check if columns already exist (idempotent migration)
    inspector = sa.inspect(connection)
    existing_columns = {col['name'] for col in inspector.get_columns('media_packages')}

    if 'budget' not in existing_columns:
        op.add_column('media_packages', sa.Column('budget', DECIMAL(15, 2), nullable=True,
                                                   comment='Package budget allocation (AdCP spec)'))
        print("   ✅ Added column: budget")
    else:
        print("   ⏭️  Column 'budget' already exists, skipping")

    if 'bid_price' not in existing_columns:
        op.add_column('media_packages', sa.Column('bid_price', DECIMAL(15, 2), nullable=True,
                                                   comment='Bid price for auction-based pricing (AdCP spec)'))
        print("   ✅ Added column: bid_price")
    else:
        print("   ⏭️  Column 'bid_price' already exists, skipping")

    if 'pacing' not in existing_columns:
        op.add_column('media_packages', sa.Column('pacing', sa.String(20), nullable=True,
                                                   comment='Pacing strategy: even, asap, front_loaded (AdCP enum)'))
        print("   ✅ Added column: pacing")
    else:
        print("   ⏭️  Column 'pacing' already exists, skipping")

    # Step 2: Audit existing data
    print("\n📊 Step 2: Auditing existing data...")
    total_packages = connection.execute(text("SELECT COUNT(*) FROM media_packages")).scalar()

    # Note: Using -> operator to check key existence (avoiding ?? psycopg2 issue)
    budget_count = connection.execute(
        text("""
            SELECT COUNT(*) FROM media_packages
            WHERE (package_config::jsonb)->'budget' IS NOT NULL
        """)
    ).scalar()

    # Note: bid_price is nested in pricing_info dict
    bid_price_count = connection.execute(
        text("""
            SELECT COUNT(*) FROM media_packages
            WHERE (package_config::jsonb)->'pricing_info'->'bid_price' IS NOT NULL
        """)
    ).scalar()

    pacing_count = connection.execute(
        text("""
            SELECT COUNT(*) FROM media_packages
            WHERE (package_config::jsonb)->'pacing' IS NOT NULL
        """)
    ).scalar()

    print(f"   Found {total_packages} total packages:")
    print(f"   - {budget_count} with budget field")
    print(f"   - {bid_price_count} with bid_price in pricing_info")
    print(f"   - {pacing_count} with pacing field")

    # Step 3: Migrate budget (handle float, Budget object, and nested dict formats)
    print("\n💰 Step 3: Migrating budget values...")

    # Handle numeric budget (simple float)
    result = connection.execute(text("""
        UPDATE media_packages
        SET budget = ((package_config::jsonb)->>'budget')::DECIMAL(15,2)
        WHERE (package_config::jsonb)->'budget' IS NOT NULL
          AND jsonb_typeof((package_config::jsonb)->'budget') = 'number'
    """))
    numeric_budget_count = result.rowcount

    # Handle Budget object (extract 'total' field)
    result = connection.execute(text("""
        UPDATE media_packages
        SET budget = ((package_config::jsonb)->'budget'->>'total')::DECIMAL(15,2)
        WHERE (package_config::jsonb)->'budget' IS NOT NULL
          AND jsonb_typeof((package_config::jsonb)->'budget') = 'object'
          AND (package_config::jsonb)->'budget'->'total' IS NOT NULL
    """))
    object_budget_count = result.rowcount

    print(f"   ✅ Migrated budget:")
    print(f"      - {numeric_budget_count} numeric budgets")
    print(f"      - {object_budget_count} Budget objects (extracted 'total' field)")

    # Step 4: Migrate bid_price (from pricing_info.bid_price)
    print("\n💵 Step 4: Migrating bid_price values...")
    result = connection.execute(text("""
        UPDATE media_packages
        SET bid_price = ((package_config::jsonb)->'pricing_info'->>'bid_price')::DECIMAL(15,2)
        WHERE (package_config::jsonb)->'pricing_info'->'bid_price' IS NOT NULL
          AND jsonb_typeof((package_config::jsonb)->'pricing_info'->'bid_price') = 'number'
    """))
    print(f"   ✅ Migrated {result.rowcount} bid_price values")

    # Step 5: Migrate pacing (normalize variations)
    print("\n⏱️  Step 5: Migrating pacing values...")

    # First, check for any non-standard pacing values
    non_standard_pacing = connection.execute(text("""
        SELECT DISTINCT (package_config::jsonb)->>'pacing' as pacing_value
        FROM media_packages
        WHERE (package_config::jsonb)->'pacing' IS NOT NULL
          AND LOWER((package_config::jsonb)->>'pacing') NOT IN ('even', 'asap', 'front_loaded', 'front-loaded', 'frontloaded')
    """)).fetchall()

    if non_standard_pacing:
        print(f"   ⚠️  Warning: Found non-standard pacing values: {[row[0] for row in non_standard_pacing]}")

    # Normalize pacing values to AdCP spec
    result = connection.execute(text("""
        UPDATE media_packages
        SET pacing = CASE
            WHEN LOWER((package_config::jsonb)->>'pacing') = 'even' THEN 'even'
            WHEN LOWER((package_config::jsonb)->>'pacing') IN ('asap', 'as_ap', 'as-ap') THEN 'asap'
            WHEN LOWER((package_config::jsonb)->>'pacing') IN ('front_loaded', 'front-loaded', 'frontloaded') THEN 'front_loaded'
            ELSE NULL
        END
        WHERE (package_config::jsonb)->'pacing' IS NOT NULL
    """))
    print(f"   ✅ Migrated {result.rowcount} pacing values")

    # Step 6: Verify migration results
    print("\n✓ Step 6: Verifying migration...")

    migrated_budget = connection.execute(
        text("SELECT COUNT(*) FROM media_packages WHERE budget IS NOT NULL")
    ).scalar()

    migrated_bid = connection.execute(
        text("SELECT COUNT(*) FROM media_packages WHERE bid_price IS NOT NULL")
    ).scalar()

    migrated_pacing = connection.execute(
        text("SELECT COUNT(*) FROM media_packages WHERE pacing IS NOT NULL")
    ).scalar()

    print(f"   Budget: {migrated_budget}/{budget_count} migrated")
    print(f"   Bid price: {migrated_bid}/{bid_price_count} migrated")
    print(f"   Pacing: {migrated_pacing}/{pacing_count} migrated")

    # Check for unmigrated data
    if migrated_budget < budget_count:
        print(f"   ⚠️  {budget_count - migrated_budget} budgets could not be migrated (invalid format)")

    # Step 7: Add constraints (idempotent)
    print("\n🔒 Step 7: Adding constraints...")

    # Check existing constraints
    existing_constraints = {c['name'] for c in inspector.get_check_constraints('media_packages')}

    if 'ck_media_packages_budget_positive' not in existing_constraints:
        op.create_check_constraint(
            'ck_media_packages_budget_positive',
            'media_packages',
            'budget > 0'
        )
        print("   ✅ Added constraint: ck_media_packages_budget_positive")
    else:
        print("   ⏭️  Constraint 'ck_media_packages_budget_positive' already exists")

    if 'ck_media_packages_bid_price_non_negative' not in existing_constraints:
        op.create_check_constraint(
            'ck_media_packages_bid_price_non_negative',
            'media_packages',
            'bid_price >= 0'
        )
        print("   ✅ Added constraint: ck_media_packages_bid_price_non_negative")
    else:
        print("   ⏭️  Constraint 'ck_media_packages_bid_price_non_negative' already exists")

    if 'ck_media_packages_pacing_values' not in existing_constraints:
        op.create_check_constraint(
            'ck_media_packages_pacing_values',
            'media_packages',
            "pacing IN ('even', 'asap', 'front_loaded')"
        )
        print("   ✅ Added constraint: ck_media_packages_pacing_values")
    else:
        print("   ⏭️  Constraint 'ck_media_packages_pacing_values' already exists")

    # Step 8: Add indexes for query performance (idempotent)
    print("\n📇 Step 8: Adding indexes...")

    # Check existing indexes
    existing_indexes = {idx['name'] for idx in inspector.get_indexes('media_packages')}

    if 'idx_media_packages_budget' not in existing_indexes:
        op.create_index(
            'idx_media_packages_budget',
            'media_packages',
            ['budget'],
            postgresql_where=text('budget IS NOT NULL')
        )
        print("   ✅ Added partial index: idx_media_packages_budget")
    else:
        print("   ⏭️  Index 'idx_media_packages_budget' already exists")

    print("\n" + "="*70)
    print("✅ MIGRATION COMPLETE!")
    print("="*70 + "\n")


def downgrade() -> None:
    """Restore budget, bid_price, pacing back into package_config JSONB."""
    connection = op.get_bind()

    print("\n" + "="*70)
    print("ROLLBACK: add_package_pricing_columns")
    print("="*70)

    # Step 1: Restore values back into package_config
    print("\n📝 Step 1: Restoring values to package_config...")
    packages = connection.execute(
        text("""
            SELECT media_buy_id, package_id, budget, bid_price, pacing, package_config
            FROM media_packages
            WHERE budget IS NOT NULL OR bid_price IS NOT NULL OR pacing IS NOT NULL
        """)
    ).fetchall()

    restored_count = 0
    for pkg in packages:
        media_buy_id, package_id, budget, bid_price, pacing, config = pkg

        # Parse existing config
        if isinstance(config, str):
            config = json.loads(config)
        elif config is None:
            config = {}

        # Restore budget to package_config (as simple float per AdCP spec)
        if budget is not None:
            config['budget'] = float(budget)

        # Restore bid_price to pricing_info.bid_price (nested location)
        if bid_price is not None:
            if 'pricing_info' not in config:
                config['pricing_info'] = {}
            config['pricing_info']['bid_price'] = float(bid_price)

        # Restore pacing
        if pacing is not None:
            config['pacing'] = pacing

        # Update package_config
        connection.execute(
            text("""
                UPDATE media_packages
                SET package_config = :config::jsonb
                WHERE media_buy_id = :media_buy_id AND package_id = :package_id
            """),
            {"config": json.dumps(config), "media_buy_id": media_buy_id, "package_id": package_id}
        )
        restored_count += 1

    print(f"   ✅ Restored {restored_count} packages to package_config")

    # Step 2: Drop indexes
    print("\n📇 Step 2: Dropping indexes...")
    op.drop_index('idx_media_packages_budget', 'media_packages')
    print("   ✅ Dropped budget index")

    # Step 3: Drop constraints
    print("\n🔓 Step 3: Dropping constraints...")
    op.drop_constraint('ck_media_packages_pacing_values', 'media_packages', type_='check')
    op.drop_constraint('ck_media_packages_bid_price_non_negative', 'media_packages', type_='check')
    op.drop_constraint('ck_media_packages_budget_positive', 'media_packages', type_='check')
    print("   ✅ Dropped check constraints")

    # Step 4: Drop columns
    print("\n🗑️  Step 4: Dropping columns...")
    op.drop_column('media_packages', 'pacing')
    op.drop_column('media_packages', 'bid_price')
    op.drop_column('media_packages', 'budget')
    print("   ✅ Dropped columns: budget, bid_price, pacing")

    print("\n" + "="*70)
    print("⚠️  ROLLBACK COMPLETE - Data restored to package_config")
    print("="*70 + "\n")
