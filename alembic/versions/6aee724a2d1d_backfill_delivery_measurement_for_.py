"""backfill delivery_measurement for existing products

Per AdCP v3.5/3.6, delivery_measurement is REQUIRED on all products.
This migration backfills NULL delivery_measurement values with adapter-appropriate
defaults based on the tenant's configured adapter type.

Mapping:
- google_ad_manager -> {"provider": "google_ad_manager", "notes": "Delivery measured by Google Ad Manager ad serving and reporting"}
- mock             -> {"provider": "mock", "notes": "Simulated delivery measurement for testing"}
- (other/unknown)  -> {"provider": "publisher"}

Task: salesagent-pxhs

Revision ID: 6aee724a2d1d
Revises: b4aa81561fea
Create Date: 2026-03-02 01:01:48.607100

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6aee724a2d1d"
down_revision: str | Sequence[str] | None = "b4aa81561fea"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Adapter-specific defaults for delivery_measurement
ADAPTER_DEFAULTS = {
    "google_ad_manager": '{"provider": "google_ad_manager", "notes": "Delivery measured by Google Ad Manager ad serving and reporting"}',
    "mock": '{"provider": "mock", "notes": "Simulated delivery measurement for testing"}',
}
FALLBACK_DEFAULT = '{"provider": "publisher"}'


def upgrade() -> None:
    """Backfill delivery_measurement for products that have NULL values.

    Uses the tenant's adapter_config.adapter_type to determine the appropriate
    default. Falls back to "publisher" for tenants without adapter_config or
    with an unknown adapter type.
    """
    conn = op.get_bind()

    # Step 1: Update products for tenants with known adapter types
    for adapter_type, default_json in ADAPTER_DEFAULTS.items():
        conn.execute(
            sa.text(
                """
                UPDATE products
                SET delivery_measurement = CAST(:default_json AS jsonb)
                WHERE delivery_measurement IS NULL
                  AND tenant_id IN (
                      SELECT tenant_id FROM adapter_config WHERE adapter_type = :adapter_type
                  )
                """
            ),
            {"default_json": default_json, "adapter_type": adapter_type},
        )

    # Step 2: Update any remaining products (unknown adapter or no adapter_config)
    conn.execute(
        sa.text(
            """
            UPDATE products
            SET delivery_measurement = CAST(:default_json AS jsonb)
            WHERE delivery_measurement IS NULL
            """
        ),
        {"default_json": FALLBACK_DEFAULT},
    )


def downgrade() -> None:
    """Revert backfilled delivery_measurement to NULL for products matching defaults.

    Clears products whose delivery_measurement matches one of the known backfill
    defaults. NOTE: This cannot distinguish between backfilled and manually-set
    values when they happen to match a default pattern (e.g., a user who manually
    set {"provider": "publisher"} will also have their value cleared).
    """
    conn = op.get_bind()
    all_defaults = list(ADAPTER_DEFAULTS.values()) + [FALLBACK_DEFAULT]
    for default_json in all_defaults:
        conn.execute(
            sa.text(
                """
                UPDATE products
                SET delivery_measurement = NULL
                WHERE delivery_measurement = CAST(:default_json AS jsonb)
                """
            ),
            {"default_json": default_json},
        )
