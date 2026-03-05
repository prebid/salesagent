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

    # --- Step 1: Drop ALL FK constraints referencing creatives.creative_id ---
    # Query ALL tables, not just creative_reviews/creative_assignments.
    # The legacy creative_associations table also has a FK here.
    result = conn.execute(
        text("""
            SELECT tc.constraint_name, tc.table_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.constraint_column_usage ccu
                ON ccu.constraint_name = tc.constraint_name
                AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
                AND ccu.table_name = 'creatives'
                AND ccu.column_name = 'creative_id'
        """)
    )
    for row in result:
        op.drop_constraint(row[0], row[1], type_="foreignkey")

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

    # Delete orphan reviews where the creative no longer exists.
    # These rows have NULL principal_id because the JOIN didn't match.
    # Setting principal_id='unknown' doesn't help because the composite FK
    # requires (creative_id, tenant_id, principal_id) to exist in creatives.
    conn.execute(
        text("""
            DELETE FROM creative_reviews
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

    # Delete orphan assignments where the creative no longer exists.
    conn.execute(
        text("""
            DELETE FROM creative_assignments
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

    # Re-create legacy creative_associations FK (from initial_schema)
    op.create_foreign_key(
        "creative_associations_creative_id_fkey",
        "creative_associations",
        "creatives",
        ["creative_id"],
        ["creative_id"],
    )
