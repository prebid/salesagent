"""add supported_billing to tenants

Revision ID: 4ccbe6f82b4b
Revises: 018bd7bdeed8
Create Date: 2026-04-03 15:59:54.408748

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from src.core.database.json_type import JSONType


# revision identifiers, used by Alembic.
revision: str = "4ccbe6f82b4b"
down_revision: Union[str, Sequence[str], None] = "018bd7bdeed8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add supported_billing JSON column to tenants (BR-RULE-059).

    Also fixes missing server_default on created_at — the model declares
    server_default=func.now() but no prior migration applied it to the column.
    """
    op.add_column("tenants", sa.Column("supported_billing", JSONType, nullable=True))

    # Fix missing server_defaults on timestamp columns (model declares them,
    # but original migrations didn't apply them to the DB columns)
    op.alter_column("tenants", "created_at", server_default=sa.func.now())
    op.alter_column("tenants", "updated_at", server_default=sa.func.now())


def downgrade() -> None:
    """Remove supported_billing from tenants."""
    op.drop_column("tenants", "supported_billing")
    op.alter_column("tenants", "created_at", server_default=None)
    op.alter_column("tenants", "updated_at", server_default=None)
