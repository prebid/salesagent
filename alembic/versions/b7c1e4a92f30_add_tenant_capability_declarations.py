"""add tenant capability_declarations column

Adds ``tenants.capability_declarations`` (JSONType, nullable) — the per-tenant
store for implementation-BACKED AdCP capability declaration blocks (#1592 T1a).

STRICT policy (KonstantinMirin's decision, 2026-07-27): the store may carry only
blocks the implementation backs — business facts the get_adcp_capabilities
response echoes (trusted_match surfaces, measurement catalog, adapter-backed
creative_specs, legacy axe_integrations). It deliberately has no field for a
behavioral posture production does not implement (request_signing /
webhook_signing / identity signing / webhook or offline report delivery); those
arrive with RFC 9421 signing (#1291).

No backfill: NULL means "nothing declared" and reproduces the pre-#1592
capabilities wire byte-for-byte, so this migration is a pure additive no-op for
every existing tenant.

Revision ID: b7c1e4a92f30
Revises: 846006a30d9f
Create Date: 2026-07-27 01:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.core.database.json_type import JSONType

# revision identifiers, used by Alembic.
revision: str = "b7c1e4a92f30"
down_revision: str | Sequence[str] | None = "846006a30d9f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add tenants.capability_declarations (JSONType, nullable, no backfill)."""
    op.add_column(
        "tenants",
        sa.Column(
            "capability_declarations",
            JSONType,
            nullable=True,
            comment="Implementation-backed AdCP capability declaration blocks (#1592); NULL = nothing declared",
        ),
    )


def downgrade() -> None:
    """Drop tenants.capability_declarations."""
    op.drop_column("tenants", "capability_declarations")
