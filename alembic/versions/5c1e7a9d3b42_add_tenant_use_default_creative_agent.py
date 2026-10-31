"""add tenant use_default_creative_agent column

Adds ``tenants.use_default_creative_agent`` (Boolean, NOT NULL, default TRUE): whether
the seller federates the AdCP standard creative agent in its format catalog and its
``creative_agents`` referrals. Read by ``CreativeAgentRegistry._get_tenant_agents``
through ``CreativeAgentRepository.federates_default_agent``; set on the tenant's
creative-agents admin page.

Default TRUE keeps every existing tenant's catalog as it is.

Revision ID: 5c1e7a9d3b42
Revises: 89442a2be9e1
Create Date: 2026-10-29 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5c1e7a9d3b42"
down_revision: str | Sequence[str] | None = "89442a2be9e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add tenants.use_default_creative_agent (Boolean, NOT NULL, default TRUE)."""
    op.add_column(
        "tenants",
        sa.Column("use_default_creative_agent", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    """Drop tenants.use_default_creative_agent."""
    op.drop_column("tenants", "use_default_creative_agent")
