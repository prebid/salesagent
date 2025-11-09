"""add variant name and description template fields

Revision ID: 149ad85edb6f
Revises: c608bc822a13
Create Date: 2025-11-08 05:29:06.834972

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "149ad85edb6f"
down_revision: str | Sequence[str] | None = "c608bc822a13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add variant name and description template fields to products table
    op.add_column("products", sa.Column("variant_name_template", sa.String(length=500), nullable=True))
    op.add_column("products", sa.Column("variant_description_template", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # Remove variant name and description template fields from products table
    op.drop_column("products", "variant_description_template")
    op.drop_column("products", "variant_name_template")
