"""add server_default now() to all timestamp columns

Fixes schema drift: model definitions declare server_default=func.now()
on created_at/updated_at, but earlier migrations didn't include it.
This caused NOT NULL violations when factories or legacy code inserted
rows without providing explicit timestamp values.

Revision ID: 529ae3fa444b
Revises: 018bd7bdeed8
Create Date: 2026-04-03 18:09:30.791908

"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "529ae3fa444b"
down_revision: Union[str, Sequence[str], None] = "018bd7bdeed8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables with created_at and/or updated_at that need server_default=now()
_TIMESTAMP_COLUMNS = {
    "tenants": ["created_at", "updated_at"],
    "principals": ["created_at", "updated_at"],
    "currency_limits": ["created_at", "updated_at"],
    "property_tags": ["created_at", "updated_at"],
    "publisher_partners": ["created_at", "updated_at"],
    "accounts": ["created_at", "updated_at"],
    "media_buys": ["created_at", "updated_at"],
    "push_notification_configs": ["created_at", "updated_at"],
    "authorized_properties": ["created_at"],
    "contexts": ["created_at"],
    "creative_agents": ["created_at"],
    "creative_assignments": ["created_at"],
    "adapter_config": ["created_at"],
    "object_workflow_mapping": ["created_at"],
    "signals_agents": ["created_at"],
    "strategies": ["created_at"],
    "tenant_auth_configs": ["created_at"],
    "users": ["created_at"],
    "webhook_deliveries": ["created_at"],
    "webhook_delivery_log": ["created_at"],
    "workflow_steps": ["created_at"],
}


def upgrade() -> None:
    """Add server_default=now() to all timestamp columns missing it."""
    conn = op.get_bind()
    for table, columns in _TIMESTAMP_COLUMNS.items():
        # Skip tables that don't exist yet (e.g., in partial migration runs)
        result = conn.execute(
            text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"),
            {"t": table},
        )
        if not result.fetchone():
            continue
        for col in columns:
            # Check if column exists and lacks a default
            result = conn.execute(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_name = :t AND column_name = :c"
                ),
                {"t": table, "c": col},
            )
            row = result.fetchone()
            if row and row[0] is None:
                op.alter_column(table, col, server_default=text("now()"))


def downgrade() -> None:
    """Remove server_default from timestamp columns."""
    for table, columns in _TIMESTAMP_COLUMNS.items():
        for col in columns:
            op.alter_column(table, col, server_default=None)
