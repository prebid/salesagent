"""drop adapter_config.mock_dry_run

Revision ID: b3d9c41e7a02
Revises: e4b7c2a91f05
Create Date: 2026-09-14 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3d9c41e7a02"
down_revision: str | Sequence[str] | None = "e4b7c2a91f05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Remove the column that fed a key nothing reads.

    ``mock_dry_run`` was copied into the mock adapter's config dict as ``dry_run``
    and offered as a checkbox in the admin UI, but the mock adapter's ``dry_run``
    flag went away with the testing-hook channel (commit a1b79d22d). What was left
    was an operator-settable column whose only effect was to set a dict key with
    zero readers, which reads to an operator as a working switch.

    ``dry_run`` still exists in this codebase as an AdCP REQUEST field
    (``sync_accounts``, ``sync_creatives``) handled at the unit-of-work boundary.
    That is a per-request preview, not adapter configuration, and it never read
    this column.
    """
    op.drop_column("adapter_config", "mock_dry_run")


def downgrade() -> None:
    """Restore the column, nullable and empty.

    It was ``nullable=True`` with no server default and no reader, so restoring it
    empty restores exactly the state the upgrade removed: rows carry NULL, and the
    two former copy sites treated NULL as False.
    """
    op.add_column("adapter_config", sa.Column("mock_dry_run", sa.Boolean(), nullable=True))
