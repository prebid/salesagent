"""add NOT NULL constraint for delivery_measurement

The prior migration (6aee724a2d1d) backfilled NULL values with adapter-specific
defaults. This migration adds the NOT NULL constraint so new products cannot
be created without delivery_measurement, enforcing AdCP v3.6 at the DB level.

A server_default of '{"provider": "publisher"}' ensures INSERT statements
without an explicit value still succeed (e.g. Admin UI, legacy API clients).

Task: salesagent-dj7p

Revision ID: aa005b733aed
Revises: 6aee724a2d1d
Create Date: 2026-03-08 23:30:33.183342

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "aa005b733aed"
down_revision: str | Sequence[str] | None = "6aee724a2d1d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Default for products without explicit delivery_measurement
SERVER_DEFAULT = '{"provider": "publisher"}'


def upgrade() -> None:
    """Add NOT NULL constraint with server_default to delivery_measurement.

    Prerequisites: migration 6aee724a2d1d must have run first (backfills NULLs).
    """
    # Safety: backfill any remaining NULLs (in case 6aee724a2d1d was partially applied)
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE products
            SET delivery_measurement = CAST(:default AS jsonb)
            WHERE delivery_measurement IS NULL
            """
        ),
        {"default": SERVER_DEFAULT},
    )

    # Add server_default first, then set NOT NULL
    op.alter_column(
        "products",
        "delivery_measurement",
        server_default=sa.text(f"'{SERVER_DEFAULT}'::jsonb"),
        existing_type=sa.dialects.postgresql.JSONB(),
    )
    op.alter_column(
        "products",
        "delivery_measurement",
        nullable=False,
        existing_type=sa.dialects.postgresql.JSONB(),
        existing_server_default=sa.text(f"'{SERVER_DEFAULT}'::jsonb"),
    )


def downgrade() -> None:
    """Remove NOT NULL constraint and server_default from delivery_measurement."""
    op.alter_column(
        "products",
        "delivery_measurement",
        nullable=True,
        server_default=None,
        existing_type=sa.dialects.postgresql.JSONB(),
    )
