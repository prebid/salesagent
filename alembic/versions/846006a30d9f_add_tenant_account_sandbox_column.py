"""add tenant account_sandbox column

Adds ``tenants.account_sandbox`` (Boolean, default true) — the source for
``account.sandbox`` on the get_adcp_capabilities response (#1592 C2) and the
provisioning gate on sync_accounts (#1592 A2, salesagent-5g8e). Owner decision
2026-07-14: default true (owner-confirmed, shared source for both consumers).

Revision ID: 846006a30d9f
Revises: e381618812f1
Create Date: 2026-07-24 12:25:44.738415

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '846006a30d9f'
down_revision: Union[str, Sequence[str], None] = 'e381618812f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add tenants.account_sandbox (Boolean, NOT NULL, default true)."""
    op.add_column(
        "tenants",
        sa.Column("account_sandbox", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    """Drop tenants.account_sandbox."""
    op.drop_column("tenants", "account_sandbox")
