"""add account notification_configs column

Adds ``accounts.notification_configs`` (JSONType, nullable) — the account-level
notification subscriber set (#1592 T2).

Whole-array declarative replace (maxItems 16, always read and written entire), so
a column rather than a table: no cross-account query, no per-entry lifecycle.

NULL and ``[]`` are DIFFERENT states the wire must distinguish: NULL means "never
configured" and the field is omitted from the echo; ``[]`` means "explicitly
cleared" and the echo carries an empty array. ``JSONType`` uses
``JSONB(none_as_null=True)``, so that distinction survives the round trip.

No backfill: every existing account has never configured subscribers, which is
exactly NULL.

Revision ID: c4d8e1b73a52
Revises: b7c1e4a92f30
Create Date: 2026-07-27 02:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.core.database.json_type import JSONType

# revision identifiers, used by Alembic.
revision: str = "c4d8e1b73a52"
down_revision: str | Sequence[str] | None = "b7c1e4a92f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add accounts.notification_configs (JSONType, nullable, no backfill)."""
    op.add_column(
        "accounts",
        sa.Column(
            "notification_configs",
            JSONType,
            nullable=True,
            comment="Account-level notification subscribers (#1592); NULL = never configured, [] = cleared",
        ),
    )


def downgrade() -> None:
    """Drop accounts.notification_configs."""
    op.drop_column("accounts", "notification_configs")
