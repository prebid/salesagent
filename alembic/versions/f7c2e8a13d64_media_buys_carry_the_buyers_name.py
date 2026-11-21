"""media_buys carry the buyer's name

``media-buy/create-media-buy-request.json`` (v3.2.7), ``name``: "When supplied, the seller MUST
persist it and echo it unchanged on the create success response and subsequent get_media_buys
reads." ``order_name`` is generated for the ad server, so it cannot carry it.

Revision ID: f7c2e8a13d64
Revises: e3a1c7d95b20
Create Date: 2026-11-04 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7c2e8a13d64"
down_revision: str | Sequence[str] | None = "e3a1c7d95b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable column."""
    op.add_column("media_buys", sa.Column("name", sa.String(255), nullable=True))


def downgrade() -> None:
    """Drop the column."""
    op.drop_column("media_buys", "name")
