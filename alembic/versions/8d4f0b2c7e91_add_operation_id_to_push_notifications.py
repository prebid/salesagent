"""add catalog webhook metadata and product sdk fields

Revision ID: 8d4f0b2c7e91
Revises: 570b50f516af
Create Date: 2026-05-22 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "8d4f0b2c7e91"
down_revision: str | Sequence[str] | None = "570b50f516af"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "push_notification_configs",
        sa.Column("operation_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "push_notification_configs",
        sa.Column("account_id", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "push_notification_configs",
        sa.Column("subscriber_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "push_notification_configs",
        sa.Column("event_types", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "push_notification_configs",
        sa.Column("purpose", sa.String(length=32), server_default="async_task", nullable=False),
    )
    op.add_column(
        "push_notification_configs",
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.create_index(
        "idx_push_notification_configs_account",
        "push_notification_configs",
        ["tenant_id", "account_id"],
    )
    op.add_column("products", sa.Column("allowed_actions", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("products", sa.Column("format_options", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column(
        "products", sa.Column("vendor_metric_optimization", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("products", "vendor_metric_optimization")
    op.drop_column("products", "format_options")
    op.drop_column("products", "allowed_actions")
    op.drop_index("idx_push_notification_configs_account", table_name="push_notification_configs")
    op.drop_column("push_notification_configs", "is_current")
    op.drop_column("push_notification_configs", "purpose")
    op.drop_column("push_notification_configs", "event_types")
    op.drop_column("push_notification_configs", "subscriber_id")
    op.drop_column("push_notification_configs", "account_id")
    op.drop_column("push_notification_configs", "operation_id")
