"""drop tenants.policy_settings

Revision ID: 8391a08f08f9
Revises: c3ebb9a41337
Create Date: 2026-11-23 21:17:37

The Policy page now reads and writes ``tenants.advertising_policy``, the column
``get_products`` enforces (GH #2697). ``policy_settings`` was the Policy page's own copy,
which no brief check ever read, so it is dropped. Its ``check_policy_settings_is_object``
constraint references only this column and goes with it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8391a08f08f9"
down_revision: str | Sequence[str] | None = "c3ebb9a41337"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the column and, with it, its object check."""
    op.drop_column("tenants", "policy_settings")


def downgrade() -> None:
    """Restore the nullable JSONB column and its object check."""
    op.add_column("tenants", sa.Column("policy_settings", postgresql.JSONB(), nullable=True))
    op.create_check_constraint(
        "check_policy_settings_is_object",
        "tenants",
        "jsonb_typeof(policy_settings) = 'object' OR policy_settings IS NULL",
    )
