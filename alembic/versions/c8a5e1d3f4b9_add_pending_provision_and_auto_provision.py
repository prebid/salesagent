"""add_pending_provision_status_and_auto_provision_advertisers

Revision ID: c8a5e1d3f4b9
Revises: 20c448890df9
Create Date: 2026-05-04 16:00:00.000000

Sprint 1.6 scaffolding for sync_accounts → GAM advertiser mapping
(see docs/design/sync-accounts-advertiser-mapping.md):

1. Extends ``accounts.status`` CHECK constraint to include
   ``pending_provision`` — sync_accounts lands new Accounts there when
   GAM is configured but no advertiser is pre-mapped, signaling
   "provision-on-first-buy or wait for manual mapping."

2. Adds ``tenants.auto_provision_advertisers`` boolean flag (default
   False). When True, ``_create_media_buy_impl`` will lazily call
   ``GAMOrdersManager.create_advertiser`` for ``pending_provision``
   accounts on first buy. When False, returns ACCOUNT_NOT_PROVISIONED.

No behavior change in this migration — just enables the new state space.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c8a5e1d3f4b9"
down_revision: Union[str, Sequence[str], None] = "20c448890df9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Add `pending_provision` to accounts.status CHECK constraint.
    op.drop_constraint("ck_accounts_status", "accounts", type_="check")
    op.create_check_constraint(
        "ck_accounts_status",
        "accounts",
        "status IN ('active', 'pending_approval', 'pending_provision', "
        "'rejected', 'payment_required', 'suspended', 'closed')",
    )

    # 2. Add tenants.auto_provision_advertisers flag (default False —
    # backward compatible with today's open-instance tenants; managed-
    # mode provisioning will set True per-tenant via the management API).
    op.add_column(
        "tenants",
        sa.Column(
            "auto_provision_advertisers",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("tenants", "auto_provision_advertisers")
    op.drop_constraint("ck_accounts_status", "accounts", type_="check")
    op.create_check_constraint(
        "ck_accounts_status",
        "accounts",
        "status IN ('active', 'pending_approval', 'rejected', " "'payment_required', 'suspended', 'closed')",
    )
