"""add property_targeting_allowed to products

Add the 7th adcp 3.6.0 product field: property_targeting_allowed (Boolean).
This was missed in the original add_adcp36_product_fields migration.

When False (default), the product is "all or nothing" — buyers must accept
all properties. When True, buyers can filter to a subset via property_list
targeting.

Task: salesagent-kntn

Revision ID: b4aa81561fea
Revises: b8e5f3a2c7d9
Create Date: 2026-02-27 22:27:21.724086
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4aa81561fea"
down_revision: Union[str, Sequence[str], None] = "b8e5f3a2c7d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("property_targeting_allowed", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("products", "property_targeting_allowed")
