"""store operation_id and token on push_notification_configs

Revision ID: c93f5a2e81d7
Revises: b8e21d4f7c60
Create Date: 2026-09-07 16:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c93f5a2e81d7'
down_revision: Union[str, Sequence[str], None] = 'b8e21d4f7c60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Persist the two registration fields the seller MUST echo into every payload.

    ``core/push-notification-config.json`` declares four fields; this table stored two of
    them. ``operation_id`` and ``token`` both carry "the seller MUST echo this value
    verbatim into every webhook payload", and ``operation_id`` is a REQUIRED property of
    ``core/mcp-webhook-payload.json`` — so without it every webhook body this seller sends
    is schema-invalid. The spec also forbids recovering ``operation_id`` by parsing the
    receiver URL, and the senders run long after the request that carried it, so storing it
    is the only way to honour the echo.

    Nullable, and no backfill. A row written before this column existed carries no
    ``operation_id``, and inventing one would assert a correlation identifier the buyer
    never sent — worse than omitting it, because the buyer would correlate against it.
    """
    op.add_column(
        "push_notification_configs",
        sa.Column("operation_id", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "push_notification_configs",
        sa.Column("token", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    """Drop both columns."""
    op.drop_column("push_notification_configs", "token")
    op.drop_column("push_notification_configs", "operation_id")
