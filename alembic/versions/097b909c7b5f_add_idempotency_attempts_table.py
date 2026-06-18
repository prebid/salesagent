"""add idempotency_attempts table

Revision ID: 097b909c7b5f
Revises: b4e2bffdd4f8
Create Date: 2026-05-17 05:47:47.566930

Verbatim success-response cache keyed by (tenant, principal, tool,
idempotency_key): a retry carrying the same key replays the ORIGINAL success
response byte-for-byte (AdCP 3.0.1 idempotency); errors are never cached.
media_buys.idempotency_key remains the dup-booking backstop.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "097b909c7b5f"
down_revision: str | Sequence[str] | None = "b4e2bffdd4f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
            comment="Tool that produced the cached success, e.g. 'create_media_buy'",
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column(
            "response_envelope",
            postgresql.JSONB(),
            nullable=False,
            comment="Verbatim original success response envelope; returned unchanged on replay (marked replayed=true)",
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
        ["tenant_id", "principal_id", "idempotency_key"],
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
