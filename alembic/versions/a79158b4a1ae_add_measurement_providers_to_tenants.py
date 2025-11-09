"""add_measurement_providers_to_tenants

Revision ID: a79158b4a1ae
Revises: e40d739b89c6
Create Date: 2025-11-07 12:44:40.932528

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a79158b4a1ae"
down_revision: str | Sequence[str] | None = "e40d739b89c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add measurement_providers JSONB column to tenants table
    # Structure: {"providers": ["Provider 1", "Provider 2"], "default": "Provider 1"}
    op.add_column("tenants", sa.Column("measurement_providers", sa.dialects.postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("tenants", "measurement_providers")
