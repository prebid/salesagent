"""add_gam_service_account_email_to_adapter_config

Revision ID: 661c474053fa
Revises: 1a7693edad5d
Create Date: 2025-10-19 04:41:52.439532

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "661c474053fa"
down_revision: str | Sequence[str] | None = "1a7693edad5d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Only add service account email - GCP project ID is environment config, not per-tenant
    op.add_column("adapter_config", sa.Column("gam_service_account_email", sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("adapter_config", "gam_service_account_email")
