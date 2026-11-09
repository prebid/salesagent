"""add tenant publisher_format_options column

Adds ``tenants.publisher_format_options`` (JSON, nullable): the reusable publisher format
options the tenant publishes as the ``formats[]`` of the adagents.json served at its own host
(RFC S3). NULL means no catalog, which is every existing tenant's state, so the served
document is unchanged until an operator authors one.

Revision ID: b6e2d94f1a07
Revises: ca46f294d896
Create Date: 2026-10-29 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.core.database.json_type import JSONType

# revision identifiers, used by Alembic.
revision: str = "b6e2d94f1a07"
down_revision: str | Sequence[str] | None = "ca46f294d896"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add tenants.publisher_format_options (JSON, nullable)."""
    op.add_column("tenants", sa.Column("publisher_format_options", JSONType, nullable=True))


def downgrade() -> None:
    """Drop tenants.publisher_format_options."""
    op.drop_column("tenants", "publisher_format_options")
