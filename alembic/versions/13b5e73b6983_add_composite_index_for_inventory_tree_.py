"""add composite index for inventory tree queries

Revision ID: 13b5e73b6983
Revises: 2e04733a751f
Create Date: 2026-03-30 11:36:21.572929

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '13b5e73b6983'
down_revision: Union[str, Sequence[str], None] = '2e04733a751f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add composite index for inventory tree query pattern (tenant_id, inventory_type, status)."""
    op.create_index(
        "idx_gam_inventory_tenant_type_status",
        "gam_inventory",
        ["tenant_id", "inventory_type", "status"],
    )


def downgrade() -> None:
    """Remove composite index."""
    op.drop_index("idx_gam_inventory_tenant_type_status", table_name="gam_inventory")
