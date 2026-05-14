"""Add cancellation columns and pause persistence

Revision ID: a62fd2d7b808
Revises: b4e2bffdd4f8
Create Date: 2026-05-03 00:00:00.000000

Adds columns required for AdCP-spec-compliant cancellation and to close the
pre-existing pause-state decoupling:

- media_buys.canceled_at, canceled_by, cancellation_reason
- media_buys index on (tenant_id, canceled_at)
- creative_assignments.released_at (soft-delete on cancel)
- media_packages.is_paused (was missing; mirrors MediaBuy.is_paused)

No backfill — production has never written `status='canceled'` so canceled_at
is null for every existing row by definition. media_packages.is_paused defaults
to false via server_default; existing rows get false at ALTER TABLE time.
"""

import sqlalchemy as sa

from alembic import op

revision = "a62fd2d7b808"
down_revision = "b4e2bffdd4f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    media_buys_cols = {col["name"] for col in inspector.get_columns("media_buys")}
    media_buys_indexes = {idx["name"] for idx in inspector.get_indexes("media_buys")}
    creative_assignments_cols = {col["name"] for col in inspector.get_columns("creative_assignments")}
    media_packages_cols = {col["name"] for col in inspector.get_columns("media_packages")}

    if "canceled_at" not in media_buys_cols:
        op.add_column("media_buys", sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True))
    if "canceled_by" not in media_buys_cols:
        op.add_column("media_buys", sa.Column("canceled_by", sa.String(20), nullable=True))
    if "cancellation_reason" not in media_buys_cols:
        op.add_column("media_buys", sa.Column("cancellation_reason", sa.String(500), nullable=True))

    if "idx_media_buys_canceled_at" not in media_buys_indexes:
        op.create_index("idx_media_buys_canceled_at", "media_buys", ["tenant_id", "canceled_at"])

    if "released_at" not in creative_assignments_cols:
        op.add_column(
            "creative_assignments",
            sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        )

    if "is_paused" not in media_packages_cols:
        op.add_column(
            "media_packages",
            sa.Column("is_paused", sa.Boolean(), nullable=False, server_default="false"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    media_buys_cols = {col["name"] for col in inspector.get_columns("media_buys")}
    media_buys_indexes = {idx["name"] for idx in inspector.get_indexes("media_buys")}
    creative_assignments_cols = {col["name"] for col in inspector.get_columns("creative_assignments")}
    media_packages_cols = {col["name"] for col in inspector.get_columns("media_packages")}

    if "is_paused" in media_packages_cols:
        op.drop_column("media_packages", "is_paused")

    if "released_at" in creative_assignments_cols:
        op.drop_column("creative_assignments", "released_at")

    if "idx_media_buys_canceled_at" in media_buys_indexes:
        op.drop_index("idx_media_buys_canceled_at", table_name="media_buys")

    if "cancellation_reason" in media_buys_cols:
        op.drop_column("media_buys", "cancellation_reason")
    if "canceled_by" in media_buys_cols:
        op.drop_column("media_buys", "canceled_by")
    if "canceled_at" in media_buys_cols:
        op.drop_column("media_buys", "canceled_at")
