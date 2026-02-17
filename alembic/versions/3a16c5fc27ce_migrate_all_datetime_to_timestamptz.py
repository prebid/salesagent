"""migrate_all_datetime_to_timestamptz

Convert all naive TIMESTAMP columns to TIMESTAMPTZ (timezone-aware).
Existing data is assumed to be UTC (Docker containers run in UTC).

Revision ID: 3a16c5fc27ce
Revises: b0bde1dcb049
Create Date: 2026-02-17 20:05:06.329416

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3a16c5fc27ce"
down_revision: Union[str, Sequence[str], None] = "b0bde1dcb049"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# All naive DateTime columns to migrate, grouped by table
COLUMNS_TO_MIGRATE = [
    ("adapter_config", "created_at"),
    ("adapter_config", "updated_at"),
    ("audit_logs", "timestamp"),
    ("authorized_properties", "created_at"),
    ("authorized_properties", "updated_at"),
    ("authorized_properties", "verification_checked_at"),
    ("contexts", "created_at"),
    ("contexts", "last_activity_at"),
    ("creative_agents", "created_at"),
    ("creative_agents", "updated_at"),
    ("creative_assignments", "created_at"),
    ("creative_reviews", "reviewed_at"),
    ("creatives", "approved_at"),
    ("creatives", "created_at"),
    ("creatives", "updated_at"),
    ("currency_limits", "created_at"),
    ("currency_limits", "updated_at"),
    ("format_performance_metrics", "created_at"),
    ("format_performance_metrics", "last_updated"),
    ("gam_inventory", "created_at"),
    ("gam_inventory", "last_synced"),
    ("gam_inventory", "updated_at"),
    ("gam_line_items", "created_at"),
    ("gam_line_items", "creation_date"),
    ("gam_line_items", "end_date"),
    ("gam_line_items", "last_modified_date"),
    ("gam_line_items", "last_synced"),
    ("gam_line_items", "start_date"),
    ("gam_line_items", "updated_at"),
    ("gam_orders", "created_at"),
    ("gam_orders", "end_date"),
    ("gam_orders", "last_modified_date"),
    ("gam_orders", "last_synced"),
    ("gam_orders", "start_date"),
    ("gam_orders", "updated_at"),
    ("inventory_profiles", "created_at"),
    ("inventory_profiles", "updated_at"),
    ("media_buys", "approved_at"),
    ("media_buys", "created_at"),
    ("media_buys", "end_time"),
    ("media_buys", "start_time"),
    ("media_buys", "updated_at"),
    ("object_workflow_mapping", "created_at"),
    ("principals", "created_at"),
    ("principals", "updated_at"),
    ("product_inventory_mappings", "created_at"),
    ("products", "archived_at"),
    ("products", "expires_at"),
    ("products", "last_synced_at"),
    ("property_tags", "created_at"),
    ("property_tags", "updated_at"),
    ("publisher_partners", "created_at"),
    ("publisher_partners", "last_synced_at"),
    ("publisher_partners", "updated_at"),
    ("push_notification_configs", "created_at"),
    ("push_notification_configs", "updated_at"),
    ("signals_agents", "created_at"),
    ("signals_agents", "updated_at"),
    ("strategies", "created_at"),
    ("strategies", "updated_at"),
    ("strategy_states", "updated_at"),
    ("superadmin_config", "updated_at"),
    ("sync_jobs", "completed_at"),
    ("sync_jobs", "started_at"),
    ("tenants", "created_at"),
    ("tenants", "updated_at"),
    ("users", "created_at"),
    ("users", "last_login"),
    ("webhook_deliveries", "created_at"),
    ("webhook_deliveries", "delivered_at"),
    ("webhook_deliveries", "last_attempt_at"),
    ("workflow_steps", "completed_at"),
    ("workflow_steps", "created_at"),
]


def upgrade() -> None:
    """Convert all naive TIMESTAMP columns to TIMESTAMPTZ."""
    for table, column in COLUMNS_TO_MIGRATE:
        op.alter_column(
            table,
            column,
            type_=sa.DateTime(timezone=True),
            existing_type=sa.DateTime(),
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
        )


def downgrade() -> None:
    """Revert TIMESTAMPTZ columns back to naive TIMESTAMP."""
    for table, column in COLUMNS_TO_MIGRATE:
        op.alter_column(
            table,
            column,
            type_=sa.DateTime(),
            existing_type=sa.DateTime(timezone=True),
        )
