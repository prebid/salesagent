"""add idempotency_attempts table

Revision ID: 097b909c7b5f
Revises: b4e2bffdd4f8
Create Date: 2026-05-17 05:47:47.566930

Caches rejection envelopes keyed by (tenant, principal, tool, idempotency_key)
so AdCP spec contract item 7 (replay-after-rejection returns the original answer)
can be satisfied — successful media buys are already idempotent via
media_buys.idempotency_key.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "097b909c7b5f"
down_revision: Union[str, Sequence[str], None] = "b4e2bffdd4f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "idempotency_attempts",
        sa.Column("attempt_id", sa.String(length=50), nullable=False),
        sa.Column("tenant_id", sa.String(length=50), nullable=False),
        sa.Column("principal_id", sa.String(length=50), nullable=False),
        sa.Column(
            "tool_name",
            sa.String(length=50),
            nullable=False,
            comment="Tool that produced the rejection, e.g. 'create_media_buy'",
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column(
            "response_envelope",
            postgresql.JSONB(),
            nullable=False,
            comment="Cached rejection envelope (Pydantic .model_dump()); returned verbatim on replay",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "principal_id"],
            ["principals.tenant_id", "principals.principal_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("attempt_id"),
    )
    op.create_index(
        "idx_idempotency_attempts_lookup",
        "idempotency_attempts",
        ["tenant_id", "principal_id", "tool_name", "idempotency_key"],
        unique=True,
    )
    op.create_index(
        "idx_idempotency_attempts_expires_at",
        "idempotency_attempts",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_idempotency_attempts_expires_at", table_name="idempotency_attempts")
    op.drop_index("idx_idempotency_attempts_lookup", table_name="idempotency_attempts")
    op.drop_table("idempotency_attempts")
