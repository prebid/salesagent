"""add payload_hash to idempotency_attempts

Revision ID: 1d9b1402eacb
Revises: ee84c805a0b1
Create Date: 2026-06-07 06:47:12.704173

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1d9b1402eacb"
down_revision: str | Sequence[str] | None = "ee84c805a0b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "idempotency_attempts",
        sa.Column(
            "payload_hash",
            sa.String(length=64),
            nullable=True,
            comment="RFC 8785 JCS SHA-256 of the request payload (excluded fields stripped); enables IDEMPOTENCY_CONFLICT detection",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("idempotency_attempts", "payload_hash")
