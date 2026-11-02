"""drop protocol from push_notification_configs

Revision ID: b8e21d4f7c60
Revises: a773916cc1ca
Create Date: 2026-09-07 15:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8e21d4f7c60'
down_revision: Union[str, Sequence[str], None] = 'a773916cc1ca'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop the column that recorded which transport a webhook was registered over.

    AdCP 3.1.1 L3/webhooks.mdx :308 makes the webhook envelope a function of the
    REGISTRATION CHANNEL, and names keying on the sync transport as the model it
    is not (:328). Every registration this seller accepts arrives through the AdCP
    channel, so every webhook it sends is one ``mcp-webhook-payload`` envelope and
    no sender has a second shape to choose between. 6a52cf43ad75 added this column
    to make that choice; with the choice gone it has no writer and no reader.
    """
    op.drop_column("push_notification_configs", "protocol")


def downgrade() -> None:
    """Re-add the column, nullable and unbackfilled, exactly as 6a52cf43ad75 left it."""
    op.add_column(
        "push_notification_configs",
        sa.Column("protocol", sa.String(length=20), nullable=True),
    )
