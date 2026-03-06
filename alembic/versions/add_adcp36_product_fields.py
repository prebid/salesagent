"""Add adcp 3.6.0 product fields

Add 6 new columns to the products table for fields introduced in adcp 3.6.0:
- signal_targeting_allowed (Boolean)
- catalog_match (JSONB)
- catalog_types (JSONB)
- conversion_tracking (JSONB)
- data_provider_signals (JSONB)
- forecast (JSONB)

Bug: salesagent-qo8a

Revision ID: a7d4e2f1b3c5
Revises: 3a16c5fc27ce
Create Date: 2026-02-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src.core.database.json_type import JSONType

# revision identifiers, used by Alembic.
revision: str = "a7d4e2f1b3c5"
down_revision: Union[str, None] = "3a16c5fc27ce"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("products", sa.Column("signal_targeting_allowed", sa.Boolean(), nullable=True, server_default="false"))
    op.add_column("products", sa.Column("catalog_match", JSONType(), nullable=True))
    op.add_column("products", sa.Column("catalog_types", JSONType(), nullable=True))
    op.add_column("products", sa.Column("conversion_tracking", JSONType(), nullable=True))
    op.add_column("products", sa.Column("data_provider_signals", JSONType(), nullable=True))
    op.add_column("products", sa.Column("forecast", JSONType(), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "forecast")
    op.drop_column("products", "data_provider_signals")
    op.drop_column("products", "conversion_tracking")
    op.drop_column("products", "catalog_types")
    op.drop_column("products", "catalog_match")
    op.drop_column("products", "signal_targeting_allowed")
