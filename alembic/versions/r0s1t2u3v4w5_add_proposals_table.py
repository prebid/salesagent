"""add proposals table

Persistent backing for :class:`SalesAgentProposalStore` — the v1.5
``ProposalStore`` Protocol impl that lets ``create_media_buy(proposal_id=X)``
resolve the prior ``get_products`` proposal to its allocations and
recipes. Without this table the framework's ``proposal_dispatch`` has no
store to wire and every brief→create_media_buy storyboard flow fails
with ``INVALID_REQUEST: Invalid budget: 0.0`` (no packages were derived
because no proposal was loaded).

Schema mirrors :class:`adcp.decisioning.proposal_store.ProposalRecord`:

* ``proposal_id`` — stable identifier the buyer echoes into
  ``create_media_buy``. Primary key; tenant-scoped uniqueness is
  implicit because the manager mints fresh ``prop_{uuid}`` strings.
* ``tenant_id`` — multi-tenant isolation; not in the Protocol's
  ``ProposalRecord`` but required for the salesagent's row-level
  scoping invariants.
* ``account_id`` — the AdCP account that owns the proposal. The
  framework passes ``expected_account_id`` on every read; mismatches
  collapse to ``None`` per the Protocol (cross-tenant probe defense).
* ``state`` — ``draft`` / ``committed`` / ``consuming`` / ``consumed``
  per :class:`ProposalState`. v1 store auto-commits at ``put_draft``
  time (see :class:`SalesAgentProposalStore` docstring) so the
  ``draft`` state is rarely observed; modeled for forward-compat
  with v2 finalize semantics.
* ``recipes`` — typed :class:`adcp.decisioning.recipe.Recipe` mapping
  (``product_id`` → Recipe). v1 carries empty dict (no typed
  ``implementation_config`` yet); v2 hydrates from products.
* ``proposal_payload`` — the wire ``Proposal`` shape (allocations,
  pricing, etc.). Source of truth for re-issuing the proposal on
  refine and for the framework to derive packages on consumption.
* ``expires_at`` — committed-state hold deadline. Framework rejects
  ``try_reserve_consumption`` past this point. Set at commit time
  (or, in the v1 auto-commit path, at put_draft time).
* ``media_buy_id`` — terminal-binding to a media buy on
  ``finalize_consumption``. Reverse index uses ``(account_id,
  media_buy_id)`` per the Protocol's cross-tenant collision defense.
* ``recipe_schema_version`` — captured at put_draft. Adopters whose
  Recipe subclasses bump required fields write a migration or evict.

Indexes:

* PK on ``proposal_id`` — primary access path (the framework hands us
  the ``proposal_id`` straight from the buyer's request).
* ``(account_id, media_buy_id)`` partial unique — reverse-index lookup
  via :meth:`get_by_media_buy_id`. WHERE ``media_buy_id IS NOT NULL``
  so the constraint doesn't reject rows pre-consumption.
* ``tenant_id`` — secondary for admin / debugging queries that scope
  by tenant (the Protocol doesn't expose a list-by-tenant method, but
  ops will want one).

Revision ID: r0s1t2u3v4w5
Revises: 8820c87e8ae3
Create Date: 2026-05-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "r0s1t2u3v4w5"
down_revision: str | Sequence[str] | None = "8820c87e8ae3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "proposals",
        sa.Column("proposal_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("recipes", postgresql.JSONB(none_as_null=True), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("proposal_payload", postgresql.JSONB(none_as_null=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("media_buy_id", sa.String(64), nullable=True),
        sa.Column("recipe_schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.tenant_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("proposal_id"),
    )
    # Partial unique constraint on (account_id, media_buy_id) — the
    # Protocol's reverse-index lookup needs uniqueness on consumed
    # proposals only. Pre-consumption rows have NULL media_buy_id and
    # must not be rejected.
    op.create_index(
        "ux_proposals_account_media_buy",
        "proposals",
        ["account_id", "media_buy_id"],
        unique=True,
        postgresql_where=sa.text("media_buy_id IS NOT NULL"),
    )
    # Secondary index for tenant-scoped ops queries (admin dashboard,
    # debugging). Not used by the Protocol's hot path.
    op.create_index("ix_proposals_tenant_id", "proposals", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_proposals_tenant_id", table_name="proposals")
    op.drop_index("ux_proposals_account_media_buy", table_name="proposals")
    op.drop_table("proposals")
