"""fix_pricing_options_trigger_for_product_deletion

Fix the prevent_empty_pricing_options trigger to allow product deletion.

The original trigger blocked deletion of the last pricing option unconditionally,
which prevented product deletion (even though pricing_options.product_id has
ON DELETE CASCADE). This migration updates the trigger to check if the parent
product is being deleted - if so, allow the cascade.

Revision ID: 6ac7f95b69c6
Revises: f9300bf2246d
Create Date: 2025-10-16 11:28:35.219008

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6ac7f95b69c6"
down_revision: str | Sequence[str] | None = "f9300bf2246d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Update trigger to allow product deletion cascade."""
    connection = op.get_bind()

    # Replace the trigger function with updated version that checks if product is being deleted
    connection.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION prevent_empty_pricing_options()
            RETURNS TRIGGER AS $$
            DECLARE
                remaining_count INTEGER;
                product_exists BOOLEAN;
            BEGIN
                -- Check if the parent product still exists
                -- If product is being deleted, allow CASCADE to proceed
                SELECT EXISTS(
                    SELECT 1 FROM products
                    WHERE tenant_id = OLD.tenant_id
                      AND product_id = OLD.product_id
                ) INTO product_exists;

                -- If product doesn't exist, it's being deleted - allow cascade
                IF NOT product_exists THEN
                    RETURN OLD;
                END IF;

                -- Product exists, check if this DELETE would leave it with no pricing options
                SELECT COUNT(*) INTO remaining_count
                FROM pricing_options
                WHERE tenant_id = OLD.tenant_id
                  AND product_id = OLD.product_id
                  AND id != OLD.id;

                IF remaining_count = 0 THEN
                    RAISE EXCEPTION 'Cannot delete last pricing option for product % (tenant %). Every product must have at least one pricing option.',
                        OLD.product_id, OLD.tenant_id;
                END IF;

                RETURN OLD;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )


def downgrade() -> None:
    """Revert to original trigger function."""
    connection = op.get_bind()

    # Restore original trigger function (without product existence check)
    connection.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION prevent_empty_pricing_options()
            RETURNS TRIGGER AS $$
            DECLARE
                remaining_count INTEGER;
            BEGIN
                -- Check if this DELETE would leave the product with no pricing options
                SELECT COUNT(*) INTO remaining_count
                FROM pricing_options
                WHERE tenant_id = OLD.tenant_id
                  AND product_id = OLD.product_id
                  AND id != OLD.id;

                IF remaining_count = 0 THEN
                    RAISE EXCEPTION 'Cannot delete last pricing option for product % (tenant %). Every product must have at least one pricing option.',
                        OLD.product_id, OLD.tenant_id;
                END IF;

                RETURN OLD;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )
