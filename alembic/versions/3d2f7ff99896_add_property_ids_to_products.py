"""add_property_ids_to_products

Revision ID: 3d2f7ff99896
Revises: rename_formats_to_format_ids
Create Date: 2025-11-15 17:20:29.181829

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3d2f7ff99896"
down_revision: Union[str, Sequence[str], None] = "rename_formats_to_format_ids"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add property_ids column to products table for AdCP 2.0.0 discriminated union support.

    AdCP 2.0.0 spec requires publisher_properties with discriminated unions:
    - by_id variant: {publisher_domain, property_ids: [str], selection_type: 'by_id'}
    - by_tag variant: {publisher_domain, property_tags: [str], selection_type: 'by_tag'}

    This migration adds property_ids to support the by_id variant.
    Products can use one of three authorization methods:
    - properties: Full Property objects (legacy, still supported)
    - property_ids: Array of property IDs for by_id variant (NEW)
    - property_tags: Array of tags for by_tag variant (existing)
    """
    # Add property_ids column
    op.add_column("products", sa.Column("property_ids", sa.dialects.postgresql.JSONB(), nullable=True))

    # Update the existing XOR constraint to include property_ids
    # Drop old constraint if it exists
    connection = op.get_bind()
    constraint_exists = connection.execute(
        sa.text(
            """
        SELECT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'ck_product_authorization'
            AND conrelid = 'products'::regclass
        )
    """
        )
    ).scalar()

    if constraint_exists:
        op.drop_constraint("ck_product_authorization", "products", type_="check")

    # Add new constraint: exactly one of (properties, property_ids, property_tags) must be set
    op.create_check_constraint(
        "ck_product_authorization",
        "products",
        """
        (
            (properties IS NOT NULL AND property_ids IS NULL AND property_tags IS NULL) OR
            (properties IS NULL AND property_ids IS NOT NULL AND property_tags IS NULL) OR
            (properties IS NULL AND property_ids IS NULL AND property_tags IS NOT NULL)
        )
        """,
    )


def downgrade() -> None:
    """Remove property_ids column and restore original constraint."""
    # Restore original XOR constraint (without property_ids)
    op.drop_constraint("ck_product_authorization", "products", type_="check")
    op.create_check_constraint(
        "ck_product_authorization",
        "products",
        """
        (
            (properties IS NOT NULL AND property_tags IS NULL) OR
            (properties IS NULL AND property_tags IS NOT NULL)
        )
        """,
    )

    # Drop property_ids column
    op.drop_column("products", "property_ids")
