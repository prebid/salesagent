"""Add property_targeting_allowed column to products

Add the 7th adcp 3.6.0 product field: property_targeting_allowed (Boolean).
This was missed in the original add_adcp36_product_fields migration.

When False (default), the product is "all or nothing" — buyers must accept
all properties. When True, buyers can filter to a subset via property_list
targeting.

Task: salesagent-kntn

Revision ID: b8e5f3a2c7d9
Revises: a7d4e2f1b3c5, 1a88e4967119
Create Date: 2026-02-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e5f3a2c7d9"
down_revision: Union[str, tuple[str, ...], None] = ("a7d4e2f1b3c5", "1a88e4967119")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("property_targeting_allowed", sa.Boolean(), nullable=True, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("products", "property_targeting_allowed")
