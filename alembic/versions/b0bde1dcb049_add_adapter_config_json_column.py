"""add_adapter_config_json_column

Adds config_json JSONB column to adapter_config table for schema-driven
adapter configuration. This column stores validated configuration data
alongside existing hardcoded columns for backwards compatibility.

Revision ID: b0bde1dcb049
Revises: f972939dd331
Create Date: 2026-01-31 13:18:22.155071

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = "b0bde1dcb049"
down_revision: Union[str, Sequence[str], None] = "f972939dd331"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add config_json column for schema-driven adapter configuration."""
    op.add_column(
        "adapter_config",
        sa.Column(
            "config_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="Schema-validated adapter configuration. Coexists with legacy columns during migration.",
        ),
    )


def downgrade() -> None:
    """Remove config_json column."""
    op.drop_column("adapter_config", "config_json")
