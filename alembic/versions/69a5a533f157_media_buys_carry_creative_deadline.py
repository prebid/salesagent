"""media buys carry creative_deadline

``create_media_buy`` answers with the buy-level ``creative_deadline`` its adapter reports
(``media-buy/create-media-buy-response.json``), and ``get_media_buys`` must report the
same field on ``media_buys[]`` (``media-buy/get-media-buys-response.json``). Until now the
value was not stored, so a later read had nothing to report.

One nullable ``creative_deadline`` timestamp column on ``media_buys``. NULL means the
adapter set no deadline, which is also what every existing row has.

Revision ID: 69a5a533f157
Revises: a34a2ff137da
Create Date: 2026-10-29 00:04:36.758206

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "69a5a533f157"
down_revision: str | Sequence[str] | None = "a34a2ff137da"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the buy-level creative deadline column."""
    op.add_column("media_buys", sa.Column("creative_deadline", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Drop the buy-level creative deadline column."""
    op.drop_column("media_buys", "creative_deadline")
