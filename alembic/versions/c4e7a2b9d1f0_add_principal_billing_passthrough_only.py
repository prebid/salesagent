"""add principals.billing_passthrough_only

The seller's onboarding record of a buyer agent's commercial relationship: TRUE when the
agent is onboarded as passthrough-only (no payments relationship, so only the operator can
be invoiced). ``sync_accounts`` reads it through ``identity.principal`` to answer
``BILLING_NOT_PERMITTED_FOR_AGENT`` when the agent asks for a billing value the seller's
capability accepts but its own relationship does not (enums/error-code.json,
error-details/billing-not-permitted-for-agent.json).

Default FALSE: an agent is restricted by recording the restriction, never by leaving the
column unset.

Revision ID: c4e7a2b9d1f0
Revises: d8b2f4a6c1e3
Create Date: 2026-10-30 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e7a2b9d1f0"
down_revision: str | Sequence[str] | None = "d8b2f4a6c1e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add principals.billing_passthrough_only (Boolean, NOT NULL, default FALSE)."""
    op.add_column(
        "principals",
        sa.Column("billing_passthrough_only", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    """Drop principals.billing_passthrough_only."""
    op.drop_column("principals", "billing_passthrough_only")
