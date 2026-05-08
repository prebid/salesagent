"""add tenant.report_pre_start_buys

Revision ID: bcd40819d318
Revises: d2e3f4a5b6c7
Create Date: 2026-05-07 05:38:52.563143

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "bcd40819d318"
down_revision: Union[str, Sequence[str], None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add tenant.report_pre_start_buys boolean column.

    When True, the delivery webhook scheduler sends heartbeat reports for
    buys in pending_start (warm-up window) and paused — not just active and
    completed. Default True so buyers configured for delivery webhooks stop
    polling for "did my flight start yet?" without opt-in. See issue #48.
    """
    op.add_column(
        "tenants",
        sa.Column(
            "report_pre_start_buys",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    """Drop tenant.report_pre_start_buys."""
    op.drop_column("tenants", "report_pre_start_buys")
