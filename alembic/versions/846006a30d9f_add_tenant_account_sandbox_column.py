"""add tenant account_sandbox column

Adds ``tenants.account_sandbox`` (Boolean, default false) — the source for
``account.sandbox`` on the get_adcp_capabilities response (#1592 C2) and the
provisioning gate on sync_accounts (#1592 A2, salesagent-5g8e). Shared source
for both consumers, read through resolve_account_sandbox (src/core/billing_policy.py).

Default FALSE (#1721, superseding the 2026-07-14 default-true decision): a seller
advertises sandbox support by configuring it, never by leaving the column unset.
True-by-default made every unconfigured tenant declare account.sandbox support it
had not opted into, and made the provisioning gate admit sandbox entries for it.

Revision ID: 846006a30d9f
Revises: e381618812f1
Create Date: 2026-07-24 12:25:44.738415

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '846006a30d9f'
down_revision: str | Sequence[str] | None = 'e381618812f1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add tenants.account_sandbox (Boolean, NOT NULL, default FALSE)."""
    op.add_column(
        "tenants",
        sa.Column("account_sandbox", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    """Drop tenants.account_sandbox."""
    op.drop_column("tenants", "account_sandbox")
