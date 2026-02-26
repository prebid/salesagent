"""composite PK for creatives table cross-principal isolation

Revision ID: 1a88e4967119
Revises: 3a16c5fc27ce
Create Date: 2026-02-27 00:13:42.624253

BR-RULE-034 (P0): creative_id is buyer-scoped. The sole PK on creative_id
prevents cross-principal isolation — two principals with the same creative_id
hit UniqueViolation.

Fix: composite PK (creative_id, tenant_id, principal_id) and update all FKs.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.sql import text

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1a88e4967119"
down_revision: Union[str, Sequence[str], None] = "3a16c5fc27ce"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Change creatives PK from sole creative_id to composite (creative_id, tenant_id, principal_id).

    Steps:
    1. Drop FK constraints from creative_reviews and creative_assignments that reference creatives.creative_id
    2. Drop the sole PK on creatives
    3. Create composite PK on creatives (creative_id, tenant_id, principal_id)
    4. Add principal_id column to creative_reviews and creative_assignments
    5. Backfill principal_id from the creatives table
    6. Set principal_id NOT NULL
    7. Create composite FK references
    """
    conn = op.get_bind()

    # --- Step 1: Drop existing FK constraints referencing creatives.creative_id ---

    # creative_reviews -> creatives FK
    # Find the actual constraint name (may vary between environments)
    result = conn.execute(
        text("""
            SELECT tc.constraint_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
                AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
                ON ccu.constraint_name = tc.constraint_name
                AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
                AND tc.table_name = 'creative_reviews'
                AND ccu.table_name = 'creatives'
                AND ccu.column_name = 'creative_id'
        """)
    )
    for row in result:
        op.drop_constraint(row[0], "creative_reviews", type_="foreignkey")

    # creative_assignments -> creatives FK
    result = conn.execute(
        text("""
            SELECT tc.constraint_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
                AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
                ON ccu.constraint_name = tc.constraint_name
                AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
                AND tc.table_name = 'creative_assignments'
                AND ccu.table_name = 'creatives'
                AND ccu.column_name = 'creative_id'
        """)
    )
    for row in result:
        op.drop_constraint(row[0], "creative_assignments", type_="foreignkey")

    # Also drop the inline ForeignKey on creative_reviews.creative_id column
    # (from the original column-level ForeignKey definition)
    # This was already handled above via information_schema query

    # --- Step 2: Drop old sole PK on creatives ---
    # Remove the ForeignKey on creatives.tenant_id column-level FK first
    # (the table-level FKC is separate; we only drop the PK)
    op.drop_constraint("creatives_pkey", "creatives", type_="primary")

    # --- Step 3: Create composite PK on creatives ---
    op.create_primary_key("creatives_pkey", "creatives", ["creative_id", "tenant_id", "principal_id"])

    # --- Step 4: Add principal_id column to creative_reviews ---
    op.add_column("creative_reviews", sa.Column("principal_id", sa.String(100), nullable=True))

    # --- Step 5: Backfill principal_id from creatives table ---
    conn.execute(
        text("""
            UPDATE creative_reviews cr
            SET principal_id = c.principal_id
            FROM creatives c
            WHERE cr.creative_id = c.creative_id
                AND cr.tenant_id = c.tenant_id
        """)
    )

    # For any orphan reviews where the creative no longer exists,
    # set a sentinel value so NOT NULL can be enforced
    conn.execute(
        text("""
            UPDATE creative_reviews
            SET principal_id = 'unknown'
            WHERE principal_id IS NULL
        """)
    )

    # --- Step 6: Set principal_id NOT NULL on creative_reviews ---
    op.alter_column("creative_reviews", "principal_id", nullable=False)

    # --- Step 7: Add principal_id column to creative_assignments ---
    op.add_column("creative_assignments", sa.Column("principal_id", sa.String(100), nullable=True))

    # Backfill principal_id from creatives table
    conn.execute(
        text("""
            UPDATE creative_assignments ca
            SET principal_id = c.principal_id
            FROM creatives c
            WHERE ca.creative_id = c.creative_id
                AND ca.tenant_id = c.tenant_id
        """)
    )

    # For any orphan assignments, set sentinel
    conn.execute(
        text("""
            UPDATE creative_assignments
            SET principal_id = 'unknown'
            WHERE principal_id IS NULL
        """)
    )

    # Set NOT NULL
    op.alter_column("creative_assignments", "principal_id", nullable=False)

    # --- Step 8: Create composite FK references ---
    op.create_foreign_key(
        "fk_creative_reviews_creative_composite",
        "creative_reviews",
        "creatives",
        ["creative_id", "tenant_id", "principal_id"],
        ["creative_id", "tenant_id", "principal_id"],
        ondelete="CASCADE",
    )

    op.create_foreign_key(
        "fk_creative_assignments_creative_composite",
        "creative_assignments",
        "creatives",
        ["creative_id", "tenant_id", "principal_id"],
        ["creative_id", "tenant_id", "principal_id"],
    )


def downgrade() -> None:
    """Revert to sole creative_id PK on creatives table."""
    conn = op.get_bind()

    # Drop composite FKs
    op.drop_constraint("fk_creative_assignments_creative_composite", "creative_assignments", type_="foreignkey")
    op.drop_constraint("fk_creative_reviews_creative_composite", "creative_reviews", type_="foreignkey")

    # Drop principal_id columns from child tables
    op.drop_column("creative_assignments", "principal_id")
    op.drop_column("creative_reviews", "principal_id")

    # Revert PK to sole creative_id
    op.drop_constraint("creatives_pkey", "creatives", type_="primary")
    op.create_primary_key("creatives_pkey", "creatives", ["creative_id"])

    # Re-create original FK constraints
    op.create_foreign_key(
        "creative_reviews_creative_id_fkey",
        "creative_reviews",
        "creatives",
        ["creative_id"],
        ["creative_id"],
        ondelete="CASCADE",
    )

    op.create_foreign_key(
        "creative_assignments_creative_id_fkey",
        "creative_assignments",
        "creatives",
        ["creative_id"],
        ["creative_id"],
    )
