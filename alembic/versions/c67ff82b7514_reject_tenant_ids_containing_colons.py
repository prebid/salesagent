"""reject tenant ids containing colons

Revision ID: c67ff82b7514
Revises: f1b2c3d4e5f6
Create Date: 2026-05-25 01:21:24.273574

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c67ff82b7514"
down_revision: str | Sequence[str] | None = "f1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CONSTRAINT_NAME = "ck_tenants_tenant_id_no_colon"


def _tenant_ids_with_colons() -> list[str]:
    connection = op.get_bind()
    rows = connection.execute(
        text(
            "SELECT tenant_id FROM tenants "
            "WHERE position(':' in tenant_id) > 0 "
            "ORDER BY tenant_id"
        )
    )
    return [row[0] for row in rows]


def upgrade() -> None:
    """Reject tenant IDs that would break compound account-id parsing."""
    offending_tenant_ids = _tenant_ids_with_colons()
    if offending_tenant_ids:
        joined_ids = ", ".join(repr(tenant_id) for tenant_id in offending_tenant_ids)
        raise RuntimeError(
            "Cannot add ck_tenants_tenant_id_no_colon: "
            f"existing tenants contain colons in tenant_id: {joined_ids}"
        )

    op.create_check_constraint(
        CONSTRAINT_NAME,
        "tenants",
        sa.text("position(':' in tenant_id) = 0"),
    )


def downgrade() -> None:
    """Remove tenant ID colon rejection."""
    op.drop_constraint(CONSTRAINT_NAME, "tenants", type_="check")
