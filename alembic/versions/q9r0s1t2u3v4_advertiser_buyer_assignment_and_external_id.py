"""advertiser buyer assignment and external_id

Adds the columns required to project GAM orders/line items into media_buys:

- ``gam_advertisers.principal_id`` — the buyer agent assigned to surface
  this advertiser's orders. NULL means no agent has claimed the
  advertiser yet (orders are not visible to any buyer).
- ``media_buys.source`` — origin marker. ``adcp`` (default) for buys
  created through the AdCP protocol; ``gam_import`` for materialized
  imports from gam_orders.
- ``media_buys.external_id`` — adapter-side ID (e.g. GAM order ID).
  Populated for both native and imported buys so lookups can resolve by
  either the canonical ``media_buy_id`` or the adapter ID.

Revision ID: q9r0s1t2u3v4
Revises: 523ed762edce
Create Date: 2026-05-07

Lands on top of main's latest merge revision ``523ed762edce``.

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "q9r0s1t2u3v4"
down_revision: str | Sequence[str] | None = "523ed762edce"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "gam_advertisers",
        sa.Column("principal_id", sa.String(50), nullable=True),
    )
    op.create_foreign_key(
        "fk_gam_advertisers_principal",
        "gam_advertisers",
        "principals",
        ["tenant_id", "principal_id"],
        ["tenant_id", "principal_id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_gam_advertisers_principal",
        "gam_advertisers",
        ["tenant_id", "principal_id"],
        postgresql_where=sa.text("principal_id IS NOT NULL"),
    )

    op.add_column(
        "media_buys",
        sa.Column(
            "source",
            sa.String(20),
            nullable=False,
            server_default="adcp",
        ),
    )
    op.add_column(
        "media_buys",
        sa.Column("external_id", sa.String(100), nullable=True),
    )
    op.create_index(
        "idx_media_buys_external_id",
        "media_buys",
        ["tenant_id", "external_id"],
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_media_buys_external_id", table_name="media_buys")
    op.drop_column("media_buys", "external_id")
    op.drop_column("media_buys", "source")

    op.drop_index("idx_gam_advertisers_principal", table_name="gam_advertisers")
    op.drop_constraint("fk_gam_advertisers_principal", "gam_advertisers", type_="foreignkey")
    op.drop_column("gam_advertisers", "principal_id")
