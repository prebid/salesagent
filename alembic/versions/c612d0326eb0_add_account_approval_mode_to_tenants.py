"""add account_approval_mode to tenants

Revision ID: c612d0326eb0
Revises: 4ccbe6f82b4b
Create Date: 2026-04-15 21:21:33.661734

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c612d0326eb0"
down_revision: Union[str, Sequence[str], None] = "4ccbe6f82b4b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add account_approval_mode column to tenants (BR-RULE-060).

    Account approval mode is distinct from creative approval_mode (BR-RULE-037):
    different enums, different semantics, different defaults. Nullable — NULL
    means 'auto' (accounts activate immediately on creation).
    """
    op.add_column("tenants", sa.Column("account_approval_mode", sa.String(length=50), nullable=True))


def downgrade() -> None:
    """Remove account_approval_mode from tenants."""
    op.drop_column("tenants", "account_approval_mode")
