"""sprint_1_8_routing_foundation

Revision ID: e7a4c2b9d5f1
Revises: d9f3e7a2b1c4
Create Date: 2026-05-04 18:00:00.000000

Sprint 1.8 schema foundation. See
docs/design/managed-tenant-mode-sprint-1.8-buyer-advertiser-routing.md.

Five additions in one migration (they share a sprint and ship together;
splitting buys us nothing operationally — none of these are large
backfills):

1. ``tenants.default_gam_advertiser_id`` — required-before-activation
   fallback. Buys that fall through the routing chain land here.

2. ``tenants.sync_cadence_minutes`` — per-tenant override of the default
   6h sync cadence. NULL = use default. ``sync_all_tenants.py`` reads
   this when picking which tenants to sync per cron run.

3. ``adapter_config.gam_sandbox_advertiser_id`` — sprint 1.6's deferred
   sandbox advertiser cache. Lazy-populated by
   ``ensure_sandbox_advertiser`` on first sandbox call. Sprint 1.8's
   routing chain short-circuits sandbox traffic to this advertiser.

4. ``accounts.resolved_via`` — enum tracking which path the routing
   chain took to attach the gam_advertiser_id on this Account. Lets
   the recent-buyers UI color-code matches vs fall-throughs without
   re-running resolution. Backfilled NULL on existing rows; surfaces as
   "unknown" in API responses.

5. ``advertiser_routing_rules`` table — ordered overrides for the
   routing chain. Keyed on (tenant, operator_domain, brand_house,
   brand_id) with NULLs participating in uniqueness via COALESCE.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7a4c2b9d5f1"
down_revision: Union[str, Sequence[str], None] = "d9f3e7a2b1c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Tenant.default_gam_advertiser_id
    op.add_column(
        "tenants",
        sa.Column("default_gam_advertiser_id", sa.String(length=64), nullable=True),
    )

    # 2. Tenant.sync_cadence_minutes
    op.add_column(
        "tenants",
        sa.Column("sync_cadence_minutes", sa.Integer(), nullable=True),
    )

    # 3. AdapterConfig.gam_sandbox_advertiser_id
    op.add_column(
        "adapter_config",
        sa.Column("gam_sandbox_advertiser_id", sa.String(length=64), nullable=True),
    )

    # 4. Account.resolved_via
    op.add_column(
        "accounts",
        sa.Column("resolved_via", sa.String(length=20), nullable=True),
    )
    op.create_check_constraint(
        "ck_accounts_resolved_via",
        "accounts",
        "resolved_via IS NULL OR resolved_via IN " "('account', 'sandbox', 'exact', 'house', 'operator', 'default')",
    )

    # 5. advertiser_routing_rules table
    op.create_table(
        "advertiser_routing_rules",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=50),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("operator_domain", sa.String(length=255), nullable=False),
        sa.Column("brand_house", sa.String(length=255), nullable=True),
        sa.Column("brand_id", sa.String(length=255), nullable=True),
        sa.Column("gam_advertiser_id", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # Uniqueness on the natural key with NULLs participating.
    # Postgres treats NULL as distinct in UNIQUE constraints by default;
    # COALESCE coerces NULL to empty-string for the comparison so two
    # "any-brand" rules under the same operator collide.
    op.create_index(
        "uq_routing_rule_natural_key",
        "advertiser_routing_rules",
        [
            "tenant_id",
            "operator_domain",
            sa.text("COALESCE(brand_house, '')"),
            sa.text("COALESCE(brand_id, '')"),
        ],
        unique=True,
    )
    op.create_index(
        "idx_routing_rules_tenant",
        "advertiser_routing_rules",
        ["tenant_id"],
    )
    op.create_index(
        "idx_routing_rules_operator",
        "advertiser_routing_rules",
        ["tenant_id", "operator_domain"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_routing_rules_operator", table_name="advertiser_routing_rules")
    op.drop_index("idx_routing_rules_tenant", table_name="advertiser_routing_rules")
    op.drop_index("uq_routing_rule_natural_key", table_name="advertiser_routing_rules")
    op.drop_table("advertiser_routing_rules")

    op.drop_constraint("ck_accounts_resolved_via", "accounts", type_="check")
    op.drop_column("accounts", "resolved_via")

    op.drop_column("adapter_config", "gam_sandbox_advertiser_id")

    op.drop_column("tenants", "sync_cadence_minutes")
    op.drop_column("tenants", "default_gam_advertiser_id")
